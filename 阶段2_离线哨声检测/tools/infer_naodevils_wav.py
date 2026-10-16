"""Continuous float TestBench inference and candidate events; no event metrics."""
import argparse
import csv
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import time
import wave

os.environ['CUDA_VISIBLE_DEVICES']='-1'
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL','2')
import h5py
import numpy as np
import tensorflow as tf

import continuous_whistle as c
from infer_naodevils_full import sha,write


def save_clip(audio,rate,start,end,channel,path):
    samples=np.clip(audio[start:end,channel],-1,1)
    pcm=np.round(samples*32767).astype('<i2')
    with wave.open(str(path),'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(pcm.tobytes())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',required=True)
    parser.add_argument('--data',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    model_path,data,output=Path(args.model),Path(args.data),Path(args.output)
    output.mkdir(parents=True,exist_ok=False)
    audit=json.loads((model_path.parent.parent.parent/'static-audit.json').read_text(encoding='utf-8'))
    wavs=sorted((data/'TestBench').glob('*.wav'))
    before={str(path.absolute()):sha(path) for path in [model_path]+wavs}
    expected={row['filename']:row['sha256'] for row in audit['wav']}
    if before[str(model_path.absolute())]!=audit['model']['sha256'] or any(before[str(p.absolute())]!=expected[p.name] for p in wavs):
        raise ValueError('Source differs from static audit')
    write(output/'plan-before-prediction.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),
        window_samples=c.WINDOW,hop_samples=c.HOP,window='np.hamming',threshold=c.THRESHOLD,
        sample_rate_contract='Require observed TestBench 22050Hz; does not establish all training source rates',
        silence='Per-channel all-zero window: score0, no model call; exact-zero FFT bins replaced with1e-12',
        eof='Only complete windows; trailing samples recorded, no padding. Active run flushed as EOF candidate, natural offset unverified',
        event_rule='OR across channels per window, at least2 consecutive positive windows; no gap bridging; candidate only',
        source_hashes=before, tool_sha256=sha(Path(__file__)), helper_sha256=sha(Path(c.__file__)),
        audition='First/last candidate from each file, longest nontrigger gap center, final4s. Selected before listening, not ground truth.',
        no_training=True,no_threshold_search=True,no_board_upload=True,absolute_utc_start=None))
    with h5py.File(str(model_path),'r') as asset:
        model=tf.keras.models.load_model(asset,compile=False)
    records,listening=[],[]
    (output/'listening').mkdir()
    for file_index,path in enumerate(wavs,1):
        start_time=time.perf_counter()
        audio,rate=c.read_float_wav(path)
        if rate!=22050:
            raise ValueError('Unexpected TestBench sample rate; no silent resampling')
        starts=c.starts_for_samples(len(audio))
        scores=np.zeros((len(starts),audio.shape[1]),dtype=np.float32)
        silence=np.zeros_like(scores,dtype=bool)
        zero_bins=0
        forward_seconds=feature_seconds=0.
        for first in range(0,len(starts),256):
            selected=starts[first:first+256]
            stamp=time.perf_counter()
            features,silent,zeros=c.features_for_starts(audio,selected)
            feature_seconds+=time.perf_counter()-stamp
            zero_bins+=zeros
            flat=features.reshape(-1,513,1)
            nonsilent=~silent.reshape(-1)
            predicted=np.zeros(len(flat),dtype=np.float32)
            stamp=time.perf_counter()
            if nonsilent.any():
                predicted[nonsilent]=np.asarray(model(flat[nonsilent],training=False)).reshape(-1)
            forward_seconds+=time.perf_counter()-stamp
            if not np.isfinite(predicted).all() or not ((predicted>=0)&(predicted<=1)).all():
                raise ValueError('Invalid continuous prediction')
            scores[first:first+len(selected)]=predicted.reshape(len(selected),audio.shape[1])
            silence[first:first+len(selected)]=silent
        events=c.candidate_events(scores,starts,len(audio),rate)
        name=path.stem
        np.save(str(output/(name+'-scores.npy')),scores,allow_pickle=False)
        with (output/(name+'-windows.csv')).open('w',encoding='utf-8',newline='') as target:
            writer=csv.writer(target)
            writer.writerow(['window_index','channel_index','start_sample','end_sample_exclusive','start_seconds','score','digital_silence','above_0_65'])
            for index,position in enumerate(starts):
                for channel in range(audio.shape[1]):
                    writer.writerow([index,channel,int(position),int(position)+c.WINDOW,format(int(position)/rate,'.12g'),
                        format(float(scores[index,channel]),'.9g'),int(silence[index,channel]),int(float(scores[index,channel])>=c.THRESHOLD)])
        write(output/(name+'-events.json'),events)
        per_channel=[len(c.candidate_events(scores[:,channel:channel+1],starts,len(audio),rate)) for channel in range(audio.shape[1])]
        coverage=int(starts[-1])+c.WINDOW if len(starts) else 0
        records.append(dict(filename=path.name,rate=rate,channels=audio.shape[1],sample_frames=len(audio),duration_seconds=len(audio)/rate,
            windows_per_channel=len(starts),covered_end_sample=coverage,uncovered_tail_samples=len(audio)-coverage,
            digital_silent_windows_per_channel=silence.sum(axis=0).tolist(),zero_fft_bins_replaced=zero_bins,
            positive_windows_per_channel=(scores.astype(np.float64)>=c.THRESHOLD).sum(axis=0).tolist(),
            candidate_event_count=len(events),per_channel_candidate_counts=per_channel,
            feature_seconds=feature_seconds,forward_seconds=forward_seconds,
            file_processing_seconds=time.perf_counter()-start_time,
            time_scope='Read, features, model, score/event files; excludes later audition clips and source hash checks'))
        # Pick first/last candidates, not the best-looking sounds. Keep context and a 12s cap.
        for label,event in [('first',events[0]),('last',events[-1])] if events else []:
            clip_start=max(0,event['estimated_start_sample']-2*rate)
            clip_end=min(len(audio),event['estimated_end_sample']+2*rate,clip_start+12*rate)
            clip_id='P%d%s'%(file_index,'a' if label=='first' else 'b')
            clip_path=output/'listening'/(clip_id+'.wav')
            save_clip(audio,rate,clip_start,clip_end,event['audition_channel'],clip_path)
            listening.append(dict(clip_id=clip_id,kind='candidate_'+label,filename=path.name,source_start_sample=clip_start,
                source_end_sample=clip_end,source_start_seconds=clip_start/rate,channel_index=event['audition_channel'],
                event_index=event['event_index'],candidate_onset_in_clip_seconds=(event['estimated_start_sample']-clip_start)/rate,
                candidate_end_in_clip_seconds=(event['estimated_end_sample']-clip_start)/rate,
                clip_truncates_candidate=clip_end<event['estimated_end_sample'],listening_status='pending'))
        positive=np.any(scores.astype(np.float64)>=c.THRESHOLD,axis=1)
        negative=np.flatnonzero(~positive)
        if len(negative):
            runs=np.split(negative,np.flatnonzero(np.diff(negative)>1)+1)
            longest=max(runs,key=len)
            center=int(starts[longest[len(longest)//2]])
            clip_start=max(int(starts[longest[0]]),center-2*rate)
            clip_end=min(int(starts[longest[-1]])+c.WINDOW,clip_start+4*rate,len(audio))
            clip_id='N%d'%file_index
            save_clip(audio,rate,clip_start,clip_end,0,output/'listening'/(clip_id+'.wav'))
            listening.append(dict(clip_id=clip_id,kind='nontrigger_context_not_assumed_negative',filename=path.name,
                source_start_sample=clip_start,source_end_sample=clip_end,source_start_seconds=clip_start/rate,
                channel_index=0,listening_status='pending'))
        clip_id='E%d'%file_index
        clip_start=max(0,len(audio)-4*rate)
        save_clip(audio,rate,clip_start,len(audio),0,output/'listening'/(clip_id+'.wav'))
        listening.append(dict(clip_id=clip_id,kind='EOF_context',filename=path.name,source_start_sample=clip_start,
            source_end_sample=len(audio),source_start_seconds=clip_start/rate,channel_index=0,listening_status='pending'))
        print(path.name,'windows',len(starts),'candidates',len(events),'tail',len(audio)-coverage,flush=True)
    after={str(path.absolute()):sha(path) for path in [model_path]+wavs}
    if before!=after:
        raise ValueError('Source changed')
    write(output/'integrity.json',dict(before=before,after=after,unchanged=True))
    write(output/'listening-checklist.json',listening)
    write(output/'summary.json',dict(completed_utc=datetime.now(timezone.utc).isoformat(),records=records,
        versions=dict(tensorflow=tf.__version__,numpy=np.__version__,h5py=h5py.__version__),
        no_event_recall_reported=True,no_false_alarms_per_hour_reported=True,listening_status='pending',
        scope='Candidate events only, source audio origin relative positions; no UTC/field-source classification'))


if __name__=='__main__':
    main()

# -*- coding: utf-8 -*-
"""Fixed H5/v1 inference on six basketball clips, scored in original samples."""
import csv
import os
from pathlib import Path
import time
os.environ['CUDA_VISIBLE_DEVICES']='-1'
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL','2')
import h5py
import numpy as np
import tensorflow as tf
from continuous_whistle import read_float_wav,starts_for_samples,features_for_starts,candidate_events
from basketball_audio_adapter import source_boundary
from evaluate_testbench_events import read,write,sha,match,stats

STAGE=Path(__file__).absolute().parent.parent


def main():
    start_main=time.perf_counter()
    out=STAGE/'outputs/basketball-inference-20261012-01'
    out.mkdir(exist_ok=False)
    adaptation=STAGE/'outputs/basketball-input-adaptation-20261012-01'
    manifest=read(adaptation/'manifest.json')
    material=read(STAGE/'outputs/basketball-manual-20261012-01/material.json')
    truth_path=STAGE/'outputs/basketball-manual-review-20261012-02/manual_annotations_coverage_corrected.json'
    truth=read(truth_path)
    confirmation=read(truth_path.parent/'user-final-confirmation.json')
    assert sha(truth_path)==confirmation['annotation_sha256'] and len(truth['events'])==7
    model_path=STAGE/'outputs/naodevils-review-20261011-01/PreTrainedNetworks/WhistleDetection/WhistleNetMk16.h5'
    protected=read(adaptation/'verification.json')['protected_sha256']
    protected.update({f['output_path']:f['output_sha256'] for f in manifest['files']})
    protected[str(model_path)]='924e779a5791eba1ba26def09a368dae904323e73a2ae240061a844c09f96f75'
    assert all(sha(Path(p))==h for p,h in protected.items())
    write(out/'plan-before-inference.json',dict(user_authorization='可以都用来验证检测模型，后续我还有别的片段可以加进来',
        all_six_files_use='basketball clip offline verification, none used to choose parameters in this run',
        no_train_validation_test_split=True,training_provenance_unknown=True,
        model_sha256=sha(model_path),source_sha256=protected,tool_sha256=sha(Path(__file__)),
        helper_sha256={str(p):sha(p) for p in [STAGE/'tools/continuous_whistle.py',STAGE/'tools/basketball_audio_adapter.py',STAGE/'tools/evaluate_testbench_events.py']},
        threshold=.65,event_logic='v1: any actual channel >=0.65, >=2 consecutive windows; no bridging',
        window=1024,hop=512,rate=22050,no_gain_normalization=True,
        scoring='all seven audible blows, identity unknown; original44100 samples,0.2s inclusive8820,1:1 max count/min onset error',
        FP_hour_denominator='all original decoded audio durations; channels not multiplied',
        no_threshold_selection_training_board_or_communications=True))
    stamp=time.perf_counter()
    with h5py.File(str(model_path),'r') as asset:
        model=tf.keras.models.load_model(asset,compile=False)
    load_seconds=time.perf_counter()-stamp
    assert model.input_shape==(None,513,1) and model.output_shape==(None,1)
    results=[]
    for f in manifest['files']:
        stamp=time.perf_counter()
        audio,rate=read_float_wav(f['output_path'])
        assert rate==22050 and audio.shape==(f['output_frames'],f['channels'])
        starts=starts_for_samples(len(audio))
        scores=np.zeros((len(starts),f['channels']),dtype=np.float32)
        silent=np.zeros_like(scores,dtype=bool);zero_bins=0;feature_seconds=forward_seconds=0
        for first in range(0,len(starts),256):
            tick=time.perf_counter()
            feature,s,zeros=features_for_starts(audio,starts[first:first+256])
            feature_seconds+=time.perf_counter()-tick;zero_bins+=zeros
            active=~s.reshape(-1)
            prediction=np.zeros(active.size,dtype=np.float32)
            tick=time.perf_counter()
            if active.any():prediction[active]=np.asarray(model(feature.reshape(-1,513,1)[active],training=False)).reshape(-1)
            forward_seconds+=time.perf_counter()-tick
            assert np.isfinite(prediction).all() and ((prediction>=0)&(prediction<=1)).all()
            scores[first:first+len(s)]=prediction.reshape(s.shape);silent[first:first+len(s)]=s
        native=candidate_events(scores,starts,len(audio),rate)
        mapped=[]
        for e in native:
            p=dict(e,adapted_22050_event=e.copy())
            for key in ['estimated_start_sample','estimated_end_sample','decision_sample','closure_available_sample']:
                p[key]=source_boundary(e[key],f['input_frames'])
            p['estimated_start_seconds']=p['estimated_start_sample']/44100
            p['estimated_end_seconds']=p['estimated_end_sample']/44100
            p['sample_rate']=44100;p['time_scope']='original decoded audio sample grid, no absolute UTC'
            mapped.append(p)
        np.save(str(out/(f['id']+'-scores.npy')),scores,allow_pickle=False)
        with (out/(f['id']+'-windows.csv')).open('w',encoding='utf-8',newline='') as stream:
            writer=csv.writer(stream)
            writer.writerow(['window_index','channel_index','adapted_start_sample','adapted_end_sample','original_start_sample','original_end_sample','original_start_seconds','score','digital_silence','above_0_65'])
            for i,j in enumerate(starts):
                for c in range(f['channels']):
                    writer.writerow([i,c,int(j),int(j)+1024,source_boundary(int(j),f['input_frames']),source_boundary(int(j)+1024,f['input_frames']),int(j)/22050,format(float(scores[i,c]),'.9g'),int(silent[i,c]),int(float(scores[i,c])>=.65)])
        write(out/(f['id']+'-events.json'),mapped)
        g=[e for e in truth['events'] if e['file_id']==f['id']]
        pairs=match(mapped,g,8820);pi={i for i,j in pairs};gi={j for i,j in pairs}
        matched=[dict(prediction=mapped[i],label=g[j],onset_error_seconds=(mapped[i]['estimated_start_sample']-g[j]['start_sample'])/44100,
                      offset_error_seconds=None if mapped[i]['closed_by']=='end_of_file' else (mapped[i]['estimated_end_sample']-g[j]['end_sample'])/44100,
                      decision_delay_seconds=(mapped[i]['decision_sample']-g[j]['start_sample'])/44100) for i,j in pairs]
        fp=[dict(prediction=p,duplicate_within_onset_tolerance=any(abs(p['estimated_start_sample']-g[j]['start_sample'])<=8820 for j in gi)) for i,p in enumerate(mapped) if i not in pi]
        fn=[e for j,e in enumerate(g) if j not in gi]
        results.append(dict(id=f['id'],filename=next(x['video_filename'] for x in material['files'] if x['id']==f['id']),
            duration_seconds=f['input_frames']/44100,manual_count=len(g),candidate_count=len(mapped),TP=len(matched),FP=len(fp),FN=len(fn),
            matches=matched,false_positives=fp,false_negatives=fn,window_count=len(starts),actual_channels=f['channels'],
            adapted_tail_uncomputed_samples=f['uncomputed_tail_samples'],zero_fft_bins_replaced=zero_bins,
            score_min=float(scores.min()),score_max=float(scores.max()),
            feature_seconds=feature_seconds,forward_seconds=forward_seconds,file_processing_seconds=time.perf_counter()-stamp))
    tp,fp,fn=(sum(r[k] for r in results) for k in ['TP','FP','FN'])
    duration=sum(r['duration_seconds'] for r in results);matches=[m for r in results for m in r['matches']]
    summary=dict(TP=tp,FP=fp,FN=fn,manual_count=7,candidate_count=sum(r['candidate_count'] for r in results),
        precision=tp/(tp+fp) if tp+fp else None,recall=tp/7,F1=2*tp/(2*tp+fp+fn),
        duration_seconds=duration,FP_per_hour=fp*3600/duration,
        duplicate_FP=sum(p['duplicate_within_onset_tolerance'] for r in results for p in r['false_positives']),
        onset_error_seconds=stats([m['onset_error_seconds'] for m in matches]),
        offset_error_seconds=stats([m['offset_error_seconds'] for m in matches if m['offset_error_seconds'] is not None]),
        decision_delay_seconds=stats([m['decision_delay_seconds'] for m in matches]),
        model_load_seconds=load_seconds,forward_seconds=sum(r['forward_seconds'] for r in results),
        main_seconds_excluding_TF_import=time.perf_counter()-start_main,
        files=[{k:r[k] for k in ['id','filename','manual_count','candidate_count','TP','FP','FN','duration_seconds','file_processing_seconds']} for r in results],
        scope='six short basketball clips, seven audible whistles; not proven training-independent blind test, not full-match or board acceptance')
    assert tp+fn==7 and tp+fp==sum(r['candidate_count'] for r in results)
    assert all(sha(Path(p))==h for p,h in protected.items())
    write(out/'event-details.json',results);write(out/'result.json',summary)
    write(out/'integrity.json',dict(all_protected_inputs_unchanged=True,sha256=protected))
    print(summary)


if __name__=='__main__':main()

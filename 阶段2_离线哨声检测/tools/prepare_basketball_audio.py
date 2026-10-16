# -*- coding: utf-8 -*-
"""Decode original audio only; generate initially empty manual review materials."""
import hashlib
import json
from pathlib import Path
import subprocess
import wave
import numpy as np
from continuous_whistle import read_float_wav

STAGE=Path(__file__).absolute().parent.parent
ROOT=STAGE.parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def prepare_playback(files,out):
    gain=min(1.0,.99/max(max(f['peak_abs_per_channel']) for f in files))
    for f in files:
        audio,rate=read_float_wav(f['audio_path'])
        for channel,relative in enumerate(f['audio']):
            scaled=audio[:,channel].astype(float)*gain
            assert np.max(np.abs(scaled))<=1
            pcm=np.round(scaled*32767).astype('<i2')
            with wave.open(str(out/relative),'wb') as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(pcm.tobytes())
            with wave.open(str(out/relative),'rb') as w:
                assert w.getnframes()==len(audio) and w.readframes(len(audio))==pcm.tobytes()
        f['playback_gain']=gain
    return gain


def main():
    out=STAGE/'outputs/basketball-manual-20261012-01'
    data_dir=STAGE/'data/basketball-audio-20261012-01'
    out.mkdir(exist_ok=False);data_dir.mkdir(exist_ok=False)
    (out/'audio').mkdir()
    ffmpeg=ROOT/'tools/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe'
    ffprobe=ffmpeg.with_name('ffprobe.exe')
    inventory=json.loads((STAGE/'outputs/basketball-video-audit-20261012-01/inventory.json').read_text(encoding='utf-8-sig'))
    selected=[r for r in inventory if r['include_as_basketball_candidate']]
    assert len(selected)==6
    protected=json.loads((STAGE/'outputs/event-v2-plan-20261011-01/verification.json').read_text(encoding='utf-8'))['sha256']
    protected.update(json.loads((STAGE/'outputs/testbench-v2a-regression-20261011-01/plan-before-merge.json').read_text(encoding='utf-8'))['protected_input_sha256'])
    protected.update({r['path']:r['sha256'] for r in inventory})
    assert all(sha(Path(p))==h for p,h in protected.items())
    write(out/'plan-before-decoding.json',dict(source_sha256=protected,
        ffmpeg_sha256=sha(ffmpeg),tool_sha256=sha(Path(__file__)),
        decode='first original audio stream to float32 WAV; no -ar/-ac/normalization/time trim',
        no_model_inference=True,screen_recording_excluded=True))
    files=[]
    for number,row in enumerate(selected,1):
        source=Path(row['path']);fid='B'+str(number).zfill(2)
        path=data_dir/(fid+'-'+source.stem+'.wav')
        command=[str(ffmpeg),'-nostdin','-v','warning','-xerror','-n','-i',str(source),'-map','0:a:0','-vn','-c:a','pcm_f32le',str(path)]
        result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        (out/(fid+'-decode.log')).write_bytes(result.stderr)
        if result.returncode:raise RuntimeError('Audio decoding failed: '+fid)
        audio,rate=read_float_wav(path)
        stream=next(s for s in row['metadata']['streams'] if s['codec_type']=='audio')
        assert rate==int(stream['sample_rate']) and audio.shape[1]==stream['channels']
        # Independent output route verifies no gain/channel ordering change in WAV payload.
        raw_command=[str(ffmpeg),'-nostdin','-v','error','-xerror','-i',str(source),'-map','0:a:0','-vn','-c:a','pcm_f32le','-f','f32le','pipe:1']
        raw=subprocess.run(raw_command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        assert raw.returncode==0 and raw.stdout==audio.astype('<f4',copy=False).tobytes()
        frame_command=[str(ffprobe),'-v','error','-select_streams','a:0','-show_frames','-show_entries','frame=best_effort_timestamp_time,nb_samples','-of','json',str(source)]
        probe=subprocess.run(frame_command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        assert probe.returncode==0
        frames=json.loads(probe.stdout)['frames']
        write(out/(fid+'-decoded-frame-timeline.json'),frames)
        assert sum(int(f['nb_samples']) for f in frames)==len(audio)
        first_pts=float(frames[0]['best_effort_timestamp_time'])
        last_end=float(frames[-1]['best_effort_timestamp_time'])+int(frames[-1]['nb_samples'])/rate
        mono_paths=[];envelopes=[]
        step=max(1,int(np.ceil(len(audio)/12000)))
        for channel in range(audio.shape[1]):
            mono=out/'audio'/(fid+'-C'+str(channel)+'.wav')
            mono_paths.append('audio/'+mono.name)
            envelopes.append([[float(audio[i:i+step,channel].min()),float(audio[i:i+step,channel].max())] for i in range(0,len(audio),step)])
        assert sha(source)==row['sha256']
        files.append(dict(id=fid,group_id='user-confirmed-different-match-'+fid,split='unassigned',
            video_filename=source.name,video_path=str(source),video_url='../../../video/'+source.name,video_sha256=row['sha256'],
            audio_filename=path.name,audio_path=str(path),audio_sha256=sha(path),rate=rate,channels=audio.shape[1],frames=len(audio),duration=len(audio)/rate,
            original_audio_stream=stream,decode_command=command,raw_check_command=raw_command,
            decode_exit_code=result.returncode,first_decoded_pts_seconds=first_pts,last_decoded_end_pts_seconds=last_end,
            pts_span_minus_wav_duration_seconds=last_end-first_pts-len(audio)/rate,
            peak_abs_per_channel=np.max(np.abs(audio),axis=0).tolist(),rms_per_channel=np.sqrt(np.mean(audio.astype(float)**2,axis=0)).tolist(),
            all_zero_channels=np.all(audio==0,axis=0).tolist(),outside_unit_range_samples=int(np.sum(np.abs(audio)>1)),
            audio=mono_paths,envelope_step=step,envelopes=envelopes))
    playback_gain=prepare_playback(files,out)
    assert all(sha(Path(p))==h for p,h in protected.items())
    identity=hashlib.sha256(json.dumps([(f['video_sha256'],f['audio_sha256']) for f in files]).encode()).hexdigest()[:16]
    material=dict(schema='basketball-manual-material-v1',material_id='basketball-manual-'+identity,
        files=files,annotation_time='decoded audio sample positions; not absolute UTC or guaranteed exact video synchronization',
        model_predictions_present=False,source_match_groups_basis='user confirmation different matches; provisional grouping, splits unassigned')
    write(out/'material.json',material)
    initial=dict(schema='basketball-single-blow-manual-v1',material_id=material['material_id'],
        source_files=[{k:f[k] for k in ['id','video_sha256','audio_sha256','rate','channels','frames']} for f in files],
        files=[dict(id=f['id'],reviewed_channels=[False]*f['channels'],context_reviewed=False,context_note='') for f in files],events=[])
    write(out/'initial-empty-annotations.json',initial)
    template=(STAGE/'tools/basketball_manual_review.html').read_text(encoding='utf-8')
    (out/'review.html').write_text(template.replace('__DATA__',json.dumps(material,ensure_ascii=False).replace('</','<\/')).replace('__INITIAL__',json.dumps(initial,ensure_ascii=False)),encoding='utf-8')
    write(out/'verification.json',dict(all_original_and_prior_inputs_unchanged=True,protected_sha256=protected,
        all_6_decode_exit_zero=True,all_float_wav_equal_separate_raw_decode=True,
        all_frame_sample_counts_equal_decoded_wav=True,all_7_mono_clips_equal_common_gain_quantization=True,
        common_playback_gain=playback_gain,playback_extra_clipping_samples=0,
        initial_events_empty=True,initial_review_flags_all_false=True,model_not_run=True,
        listening_content_and_actual_browser_pending=True,
        timestamp_scope='decoded-frame PTS checked; approximate video seeking only; AAC metadata vs decoded EOF may differ'))
    print(json.dumps([{k:f[k] for k in ['id','rate','channels','frames','duration','first_decoded_pts_seconds','pts_span_minus_wav_duration_seconds','all_zero_channels','outside_unit_range_samples']} for f in files],indent=2))


if __name__=='__main__':main()

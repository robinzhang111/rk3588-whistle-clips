# -*- coding: utf-8 -*-
"""Decode the audited unique videos; create unscored manual review materials."""
import json
import hashlib
from pathlib import Path
import subprocess
import numpy as np
from prepare_basketball_audio import sha, write, prepare_playback
from continuous_whistle import read_float_wav

STAGE = Path(__file__).absolute().parent.parent
ROOT = STAGE.parent


def main():
    inventory = json.loads((STAGE/'outputs/basketball-new-video-audit-20261012-01/inventory.json').read_text(encoding='utf-8'))
    groups = {}
    for row in inventory:
        groups.setdefault(row['sha256'], []).append(row)
    assert len(inventory) == 19 and len(groups) == 17
    protected = {r['path']:r['sha256'] for r in inventory}
    old_material = json.loads((STAGE/'outputs/basketball-manual-20261012-01/material.json').read_text(encoding='utf-8'))
    for f in old_material['files']:
        protected[f['video_path']] = f['video_sha256']
        protected[f['audio_path']] = f['audio_sha256']
    for rel in ['outputs/basketball-manual-review-20261012-04/manual_annotations_corrected.json',
                'outputs/naodevils-review-20261011-01/PreTrainedNetworks/WhistleDetection/WhistleNetMk16.h5']:
        p = STAGE/rel
        protected[str(p)] = sha(p)
    for p in (STAGE/'outputs/basketball-evaluation-20261012-03').rglob('*'):
        if p.is_file():
            protected[str(p)] = sha(p)
    assert all(sha(Path(p)) == h for p,h in protected.items())
    out = STAGE/'outputs/basketball-new-manual-20261012-01'
    data = STAGE/'data/basketball-new-audio-20261012-01'
    out.mkdir(exist_ok=False)
    data.mkdir(exist_ok=False)
    (out/'audio').mkdir()
    ffmpeg = ROOT/'tools/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe'
    probe = ffmpeg.with_name('ffprobe.exe')
    write(out/'plan-before-decoding.json', dict(protected_sha256=protected,
        ffmpeg_sha256=sha(ffmpeg), tool_sha256=sha(Path(__file__)),
        source_basis='User confirms different matches; identical hash aliases grouped together',
        audio_policy='First original audio stream, float32 WAV, no resampling, mixing, gain, trim or padding',
        model_not_run=True, usage_unassigned=True))
    files = []
    for number, aliases in enumerate(groups.values(), 1):
        row = aliases[0]
        fid = 'N'+str(number).zfill(2)
        source = Path(row['path'])
        path = data/(fid+'-'+source.stem+'.wav')
        command = [str(ffmpeg), '-nostdin', '-v', 'warning', '-xerror', '-n', '-i', str(source),
                   '-map', '0:a:0', '-vn', '-c:a', 'pcm_f32le', str(path)]
        decoded = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (out/(fid+'-decode.log')).write_bytes(decoded.stderr)
        assert decoded.returncode == 0, fid+' decode failed'
        audio, rate = read_float_wav(path)
        stream = next(s for s in row['metadata']['streams'] if s['codec_type'] == 'audio')
        assert len(audio) > 0 and rate == int(stream['sample_rate']) and audio.shape[1] == stream['channels']
        raw_command = command[:-1]+['-f', 'f32le', 'pipe:1']
        raw = subprocess.run(raw_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert raw.returncode == 0 and raw.stdout == audio.astype('<f4', copy=False).tobytes()
        frame_command = [str(probe), '-v', 'error', '-select_streams', 'a:0', '-show_frames',
                         '-show_entries', 'frame=best_effort_timestamp_time,nb_samples', '-of', 'json', str(source)]
        result = subprocess.run(frame_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert result.returncode == 0
        frames = json.loads(result.stdout)['frames']
        write(out/(fid+'-decoded-frame-timeline.json'), frames)
        assert sum(int(f['nb_samples']) for f in frames) == len(audio)
        first = float(frames[0]['best_effort_timestamp_time'])
        last = float(frames[-1]['best_effort_timestamp_time'])+int(frames[-1]['nb_samples'])/rate
        pts_steps = [float(b['best_effort_timestamp_time'])-float(a['best_effort_timestamp_time'])-int(a['nb_samples'])/rate
                     for a,b in zip(frames, frames[1:])]
        step = max(1, int(np.ceil(len(audio)/12000)))
        envelopes = [[[float(audio[i:i+step,c].min()), float(audio[i:i+step,c].max())]
                      for i in range(0,len(audio),step)] for c in range(audio.shape[1])]
        files.append(dict(id=fid, group_id='user-confirmed-new-match-'+fid, split='unassigned',
            video_filename=source.name, video_path=str(source), video_url='../../../video/'+source.name,
            video_sha256=row['sha256'], duplicate_aliases=[r['filename'] for r in aliases[1:]],
            audio_filename=path.name, audio_path=str(path), audio_sha256=sha(path),
            rate=rate, channels=audio.shape[1], frames=len(audio), duration=len(audio)/rate,
            original_audio_stream=stream, decode_command=command, raw_check_command=raw_command,
            decode_exit_code=decoded.returncode, first_decoded_pts_seconds=first,
            last_decoded_end_pts_seconds=last, pts_span_minus_wav_duration_seconds=last-first-len(audio)/rate,
            max_abs_frame_pts_step_error_seconds=max(map(abs,pts_steps), default=0),
            peak_abs_per_channel=np.max(np.abs(audio),axis=0).tolist(),
            rms_per_channel=np.sqrt(np.mean(audio.astype(float)**2,axis=0)).tolist(),
            all_zero_channels=np.all(audio==0,axis=0).tolist(),
            outside_unit_range_samples=int(np.sum(np.abs(audio)>1)),
            audio=['audio/'+fid+'-C'+str(c)+'.wav' for c in range(audio.shape[1])],
            envelope_step=step, envelopes=envelopes))
    gain = prepare_playback(files,out)
    identity = hashlib.sha256(json.dumps([(f['video_sha256'],f['audio_sha256']) for f in files]).encode()).hexdigest()[:16]
    material = dict(schema='basketball-manual-material-v1',material_id='basketball-new-manual-'+identity,
        files=files, model_predictions_present=False,
        annotation_time='Decoded audio sample positions, half-open [start,end); not UTC or precise video synchronization',
        source_match_groups_basis='User confirms different matches; hash aliases same source; uses unassigned')
    initial = dict(schema='basketball-single-blow-manual-v1',material_id=material['material_id'],
        source_files=[{k:f[k] for k in ['id','video_sha256','audio_sha256','rate','channels','frames']} for f in files],
        files=[dict(id=f['id'],reviewed_channels=[False]*f['channels'],context_reviewed=False,context_note='') for f in files],events=[])
    write(out/'material.json',material)
    write(out/'initial-empty-annotations.json',initial)
    template = (STAGE/'tools/basketball_manual_review.html').read_text(encoding='utf-8')
    template = template.replace("+'/6 片段", "+'/'+material.files.length+' 片段")
    template = template.replace('basketball_manual_annotations.json','basketball_new_manual_annotations.json')
    (out/'review.html').write_text(template.replace('__DATA__',json.dumps(material,ensure_ascii=False).replace('</','<\\/'))
        .replace('__INITIAL__',json.dumps(initial,ensure_ascii=False)),encoding='utf-8')
    assert all(sha(Path(p)) == h for p,h in protected.items())
    write(out/'verification.json',dict(protected_sha256=protected,all_protected_hashes_unchanged=True,
        unique_decoded_files=len(files),preview_mono_files=sum(f['channels'] for f in files),
        all_decode_exit_zero=True,all_wav_payloads_equal_separate_raw_decode=True,
        all_frame_sample_counts_equal_wav=True,all_preview_payloads_equal_common_gain_quantization=True,
        common_playback_gain=gain,preview_extra_clipping_samples=0,model_not_run=True,
        initial_events_empty=True,initial_review_flags_false=True,actual_listening_and_browser_pending=True))
    summary = dict(files=[{k:f[k] for k in ['id','video_filename','duplicate_aliases','rate','channels','frames','duration',
        'peak_abs_per_channel','rms_per_channel','all_zero_channels','outside_unit_range_samples',
        'first_decoded_pts_seconds','pts_span_minus_wav_duration_seconds','max_abs_frame_pts_step_error_seconds']} for f in files],
        total_decoded_seconds=sum(f['duration'] for f in files),common_playback_gain=gain)
    write(out/'decode-summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()

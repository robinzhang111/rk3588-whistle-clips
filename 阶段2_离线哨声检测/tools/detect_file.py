"""CPU file detector: fixed WhistleNetMk16/0.65/v1, relative candidate times."""
import argparse
import csv
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import time
import wave

os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')
import numpy as np
import continuous_whistle as c
from basketball_audio_adapter import FILTER

STAGE = Path(__file__).absolute().parent.parent
FFMPEG = STAGE.parent/'tools/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe'
MODEL = STAGE/'outputs/naodevils-review-20261011-01/PreTrainedNetworks/WhistleDetection/WhistleNetMk16.h5'
MODEL_SHA = '924e779a5791eba1ba26def09a368dae904323e73a2ae240061a844c09f96f75'
FFPROBE = FFMPEG.with_name('ffprobe.exe')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def run(command, log):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log.write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError('Media command failed; see '+str(log))
    return result.stdout


def decode(source, out, ffmpeg=FFMPEG, probe=FFPROBE):
    metadata = json.loads(run([str(probe), '-v', 'error', '-show_streams', '-show_format',
                               '-of', 'json', str(source)], out/'probe.log'))
    write(out/'input-metadata.json', metadata)
    streams = [s for s in metadata['streams'] if s['codec_type'] == 'audio']
    if not streams:
        raise ValueError('Input has no audio stream / 输入没有音轨')
    stream = streams[0]
    timeline = json.loads(run([str(probe), '-v', 'error', '-select_streams', 'a:0',
        '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time,nb_samples',
        '-of', 'json', str(source)], out/'timeline.log'))['frames']
    write(out/'decoded-frame-timeline.json', timeline)
    if not timeline:
        raise ValueError('Audio stream has no decoded frames')
    rate = int(stream['sample_rate'])
    first_pts = float(timeline[0]['best_effort_timestamp_time'])
    expected = first_pts
    max_gap = 0.
    for frame in timeline:
        pts = float(frame['best_effort_timestamp_time'])
        max_gap = max(max_gap, abs(pts-expected))
        expected = pts+int(frame['nb_samples'])/rate
    if max_gap > 2/rate:
        raise ValueError('Discontinuous audio timestamps; mapping needs separate review')
    command = [str(ffmpeg), '-nostdin', '-v', 'warning', '-xerror', '-n', '-i',
               str(source), '-map', '0:a:0', '-vn', '-c:a', 'pcm_f32le', str(out/'decoded.wav')]
    run(command, out/'decode.log')
    original, decoded_rate = c.read_float_wav(out/'decoded.wav')
    if decoded_rate != rate or original.shape[1] != stream['channels']:
        raise ValueError('Decoded format differs from metadata')
    if not len(original) or sum(int(f['nb_samples']) for f in timeline) != len(original):
        raise ValueError('Decoded sample count differs from frame timeline')
    commands = [command]
    if rate == 22050:
        audio = original
        adapted_path = out/'decoded.wav'
    else:
        adapted_path = out/'adapted.wav'
        command = [str(ffmpeg), '-nostdin', '-v', 'warning', '-xerror', '-n', '-i',
            str(out/'decoded.wav'), '-map', '0:a:0', '-af', FILTER, '-c:a',
            'pcm_f32le', str(adapted_path)]
        run(command, out/'adapt.log')
        commands.append(command)
        audio, adapted_rate = c.read_float_wav(adapted_path)
        if adapted_rate != 22050 or audio.shape[1] != original.shape[1]:
            raise ValueError('Adapter changed channel count or target rate')
        if abs(len(audio)/22050-len(original)/rate) > 2/22050:
            raise ValueError('Adapter duration mismatch')
    return audio, dict(stream=stream, original_rate=rate, original_frames=len(original),
        channels=original.shape[1], duration_seconds=len(original)/rate,
        first_decoded_pts_seconds=first_pts, maximum_pts_gap_seconds=max_gap,
        has_video=any(s['codec_type']=='video' for s in metadata['streams']),
        commands=commands, adapted_path=str(adapted_path), adapted_sha256=sha(adapted_path),
        channel_policy='preserve actual channels independently; per-window OR; no mixing/duplication',
        gain_normalization=False)


def save_review(out, source, audio, events):
    gain = min(1., .99/max(float(np.max(np.abs(audio))), .99))
    players = []
    for channel in range(audio.shape[1]):
        name = 'listen-C%d.wav'%channel
        with wave.open(str(out/name), 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(22050)
            stream.writeframes(np.round(audio[:, channel]*gain*32767).astype('<i2').tobytes())
        players.append('<p>声道 %d <audio id="c%d" controls preload="metadata" src="%s"></audio></p>'%(channel, channel, name))
    rows = []
    for e in events:
        rows.append('<tr><td>%d</td><td>%.6f</td><td>%.6f</td><td>%.6f</td><td>%s</td><td><button onclick="play(%d,%.9f,%.9f)">试听候选及前后1秒</button></td></tr>'%
            (e['event_id'], e['estimated_start_seconds'], e['estimated_end_seconds'], e['peak_score'],
             e['closed_by'], e['audition_channel'], e['estimated_start_seconds'], e['estimated_end_seconds']))
    page = '''<!doctype html><meta charset="utf-8"><title>哨声候选核对</title>
<style>body{font-family:system-ui;margin:24px}td,th{padding:8px;border:1px solid #ccc}table{border-collapse:collapse}audio{width:500px}</style>
<h1>哨声候选核对（只读）</h1><p>来源：SOURCE</p>
<p>固定WhistleNetMk16 / 0.65 / v1。候选不是人工真值；已知鞋声/喊叫误报、漏报和末端拖长仍存在。</p>
<p>秒数从解码音轨首样本起算，无UTC。分数为事件内所有窗/声道的最大模型输出，不是校准概率。试听增益 GAIN 仅用于播放。</p>
PLAYERS<table><tr><th>ID</th><th>开始秒</th><th>结束秒</th><th>峰值分数</th><th>关闭方式</th><th>定位</th></tr>ROWS</table>
<p>可用上方完整声道播放器检查未触发区域。此页不提供删补改，人工纠错入口为下一子任务。</p>
<script>let stopAt=null;function play(c,start,end){document.querySelectorAll('audio').forEach(a=>a.pause());let a=document.getElementById('c'+c);stopAt=Math.min(a.duration||Infinity,end+1);a.currentTime=Math.max(0,start-1);a.play();a.ontimeupdate=()=>{if(stopAt!==null&&a.currentTime>=stopAt)a.pause();};}</script>'''
    (out/'review.html').write_text(page.replace('SOURCE', html.escape(str(source))).replace('GAIN', str(gain)).replace('PLAYERS', ''.join(players)).replace('ROWS', ''.join(rows)), encoding='utf-8')
    return gain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New directory; existing directories refused')
    parser.add_argument('--model', type=Path, default=MODEL)
    parser.add_argument('--ffmpeg', type=Path, default=FFMPEG)
    parser.add_argument('--ffprobe', type=Path, default=FFPROBE)
    args = parser.parse_args()
    source, out = args.input.absolute(), args.output.absolute()
    if not source.is_file():
        parser.error('Input file does not exist')
    model_path = args.model.absolute()
    if sha(model_path) != MODEL_SHA:
        raise ValueError('Fixed model SHA256 mismatch')
    out.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    protected = {str(p):sha(p) for p in [source, model_path, Path(__file__), Path(c.__file__),
                                       STAGE/'tools/basketball_audio_adapter.py', args.ffmpeg, args.ffprobe]}
    plan = dict(schema='pc-whistle-run-v1', input_path=str(source), protected_sha256=protected,
        model=dict(name='WhistleNetMk16', path=str(model_path), sha256=MODEL_SHA), threshold=c.THRESHOLD,
        rule_version='v1', rule='OR across actual channels; >=2 consecutive positive windows; no gap bridge',
        preprocessing=dict(rate=22050, window=c.WINDOW, hop=c.HOP, window_function='np.hamming',
                           feature='513 FFT magnitude bins,20log10; exact zeros replaced with1e-12; silent channel score0'),
        score_definition='peak_score=max raw model output over all event windows/channels; not calibrated probability',
        time_basis='seconds from first decoded audio sample; media PTS separately recorded', absolute_utc_start=None,
        eof='complete windows only,no padding; active run flushed; natural offset unverified',
        review_status='pending', known_defects=['shoe/shout false positives','missed whistles','extended offsets'],
        no_training=True, no_v2a=True, inference_backend='tensorflow_cpu')
    write(out/'run.json', plan)
    try:
        media_start = time.perf_counter()
        audio, adaptation = decode(source, out, args.ffmpeg, args.ffprobe)
        media_seconds = time.perf_counter()-media_start
        import_start = time.perf_counter()
        import h5py
        import tensorflow as tf
        import_seconds = time.perf_counter()-import_start
        load_start = time.perf_counter()
        with h5py.File(str(model_path), 'r') as asset:
            model = tf.keras.models.load_model(asset, compile=False)
        load_seconds = time.perf_counter()-load_start
        if model.input_shape != (None,513,1) or model.output_shape != (None,1):
            raise ValueError('Unexpected model shape')
        starts = c.starts_for_samples(len(audio))
        scores = np.zeros((len(starts), audio.shape[1]), dtype=np.float32)
        silent = np.zeros_like(scores, dtype=bool)
        feature_seconds = forward_seconds = 0.
        for first in range(0, len(starts), 256):
            feature_start = time.perf_counter()
            feature, s, _ = c.features_for_starts(audio, starts[first:first+256])
            feature_seconds += time.perf_counter()-feature_start
            active = ~s.reshape(-1)
            prediction = np.zeros(active.size, dtype=np.float32)
            if active.any():
                forward_start = time.perf_counter()
                prediction[active] = np.asarray(model(feature.reshape(-1,513,1)[active], training=False)).reshape(-1)
                forward_seconds += time.perf_counter()-forward_start
            if not np.isfinite(prediction).all() or not ((prediction>=0)&(prediction<=1)).all():
                raise ValueError('Invalid model output')
            scores[first:first+len(s)] = prediction.reshape(s.shape)
            silent[first:first+len(s)] = s
        events = c.candidate_events(scores, starts, len(audio), 22050)
        for event in events:
            event.update(event_id=event['event_index']+1, event_type='whistle_candidate', review_status='pending')
            event['source_start_sample'] = min(adaptation['original_frames'], int(event['estimated_start_sample']*adaptation['original_rate']/22050+.5))
            event['source_end_sample'] = min(adaptation['original_frames'], int(event['estimated_end_sample']*adaptation['original_rate']/22050+.5))
            event['media_start_seconds'] = adaptation['first_decoded_pts_seconds']+event['estimated_start_seconds']
            event['media_end_seconds'] = adaptation['first_decoded_pts_seconds']+event['estimated_end_seconds']
        write(out/'events.json', dict(schema='relative-whistle-candidates-v1', input_path=str(source),
            input_sha256=protected[str(source)], time_basis=plan['time_basis'], sample_rate=22050, events=events))
        np.save(str(out/'scores.npy'), scores, allow_pickle=False)
        with (out/'events.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            fields = ['event_id','estimated_start_seconds','estimated_end_seconds','peak_score','audition_channel','closed_by','review_status']
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore'); writer.writeheader(); writer.writerows(events)
        with (out/'windows.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream); writer.writerow(['window_index','channel_index','start_sample_22050','score','digital_silence'])
            for i, position in enumerate(starts):
                for channel in range(audio.shape[1]):
                    writer.writerow([i, channel, int(position), float(scores[i,channel]), bool(silent[i,channel])])
        gain = save_review(out, source, audio, events)
        after = {p:sha(p) for p in protected}
        if after != protected:
            raise ValueError('Protected source/model/tool changed during run')
        write(out/'integrity.json', dict(before=protected, after=after, unchanged=True))
        coverage = int(starts[-1])+c.WINDOW if len(starts) else 0
        summary = dict(status='completed', adaptation=adaptation, candidate_count=len(events),
            window_count=len(starts), adapted_frames=len(audio), uncovered_tail_samples=len(audio)-coverage,
            playback_gain=gain, work_seconds_including_TF_import=time.perf_counter()-start,
            timing_seconds=dict(media=media_seconds, imports=import_seconds, model_load=load_seconds,
                                features=feature_seconds, forward_including_tensor_conversion=forward_seconds),
            timing_scope='cold file run; work excludes process startup/top-level NumPy import; forward includes first-call setup and tensor transfer; not online latency',
            versions=dict(tensorflow=tf.__version__, numpy=np.__version__, h5py=h5py.__version__),
            inference_devices=[d.name for d in tf.config.get_visible_devices()], review_status='pending')
        write(out/'summary.json', summary)
        print(json.dumps(dict(output=str(out), candidates=len(events), review=str(out/'review.html')), ensure_ascii=False))
    except Exception as error:
        write(out/'failure.json', dict(status='failed', error=str(error)))
        raise


if __name__ == '__main__':
    main()

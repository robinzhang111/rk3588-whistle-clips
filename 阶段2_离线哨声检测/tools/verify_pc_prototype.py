"""Verify actual prototype outputs against preserved historical scores/events."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import wave
import numpy as np
import detect_file as d


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    root = d.STAGE/'outputs/pc-prototype-20261012-01'
    references = [
        ('wav-TestBench', d.STAGE/'outputs/naodevils-continuous-20261011-01', 'Testing_20221104_091937_2', False),
        ('video-B01', d.STAGE/'outputs/basketball-inference-20261012-01', 'B01', True)]
    reports = []
    for name, legacy, stem, mapped in references:
        out = root/name
        plan, summary = read(out/'run.json'), read(out/'summary.json')
        container = read(out/'events.json'); events = container['events']
        score_path, event_path = legacy/(stem+'-scores.npy'), legacy/(stem+'-events.json')
        protected = {str(p):d.sha(p) for p in [score_path, event_path]}
        scores = np.load(str(out/'scores.npy'), allow_pickle=False)
        previous = np.load(str(score_path), allow_pickle=False)
        np.testing.assert_allclose(scores, previous, rtol=1e-5, atol=1e-6)
        np.testing.assert_array_equal(scores.astype(float)>=.65, previous.astype(float)>=.65)
        old_events = read(event_path)
        assert len(events) == len(old_events) == summary['candidate_count']
        for new, old in zip(events, old_events):
            old = old['adapted_22050_event'] if mapped else old
            for key, value in old.items():
                assert new[key] == value, (name, key)
            assert 0 <= new['estimated_start_seconds'] < new['estimated_end_seconds'] <= summary['adapted_frames']/22050
            assert new['media_start_seconds'] == new['estimated_start_seconds']+summary['adaptation']['first_decoded_pts_seconds']
            assert new['review_status'] == 'pending' and 'event_time_utc' not in new
        assert plan['threshold'] == .65 and plan['rule_version'] == 'v1' and plan['absolute_utc_start'] is None
        assert summary['inference_devices'] == ['/physical_device:CPU:0']
        audio, rate = d.c.read_float_wav(summary['adaptation']['adapted_path'])
        assert rate == 22050 and audio.shape == (summary['adapted_frames'], summary['adaptation']['channels'])
        assert scores.shape == (summary['window_count'], audio.shape[1])
        rebuilt = d.c.candidate_events(scores, d.c.starts_for_samples(len(audio)), len(audio), rate)
        assert len(rebuilt) == len(events)
        rows = list(csv.DictReader((out/'events.csv').open(encoding='utf-8-sig')))
        assert len(rows) == len(events)
        for row, event in zip(rows, events):
            assert float(row['estimated_start_seconds']) == event['estimated_start_seconds']
            assert float(row['peak_score']) == event['peak_score']
        page = (out/'review.html').read_text(encoding='utf-8')
        assert page.count('<tr>') == len(events)+1 and 'SOURCE' not in page and 'PLAYERS' not in page
        for channel in range(audio.shape[1]):
            with wave.open(str(out/('listen-C%d.wav'%channel)), 'rb') as stream:
                assert stream.getnframes() == len(audio) and stream.getframerate() == 22050
                pcm = np.frombuffer(stream.readframes(len(audio)), dtype='<i2')
            expected = np.round(audio[:,channel]*summary['playback_gain']*32767).astype('<i2')
            np.testing.assert_array_equal(pcm, expected)
            assert np.max(np.abs(pcm.astype(int))) < 32767
        integrity = read(out/'integrity.json')
        assert integrity['unchanged'] and integrity['before'] == integrity['after']
        assert all(d.sha(p) == h for p,h in integrity['before'].items())
        # Refusal to reuse a directory must leave all result bytes unchanged.
        saved = {str(p):d.sha(p) for p in out.iterdir() if p.is_file()}
        retry = subprocess.run([sys.executable, str(d.STAGE/'tools/detect_file.py'),
            '--input', plan['input_path'], '--output', str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert retry.returncode != 0 and b'FileExistsError' in retry.stderr
        assert all(d.sha(p) == h for p,h in saved.items())
        assert all(d.sha(p) == h for p,h in protected.items())
        reports.append(dict(run=name, candidates=len(events), score_max_abs_difference=float(np.max(np.abs(scores-previous))),
            threshold_decisions_equal=True, legacy_event_fields_equal=True, cpu_only=True,
            csv_json_playback_integrity=True, refuses_output_reuse=True, protected_sha256=protected))
    d.write(root/'verification.json', dict(status='passed', runs=reports,
        playback_scope='PCM bytes/duration/seek target checked; new browser listening not user-confirmed',
        detection_quality='unchanged known defects; this verifies integration only'))
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

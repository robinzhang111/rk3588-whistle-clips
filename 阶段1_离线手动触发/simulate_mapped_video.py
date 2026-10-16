# -*- coding: utf-8 -*-
"""Synthetic UTC and gap fixtures over the existing short video only."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

from basketclip.media import inspect_video, require_tool
from basketclip.relative import sha
from basketclip.storage import save_json
from basketclip.timing import make_alignment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).absolute().parent.parent
    video = root/'data/new_video/5710e5b0e5691924647f88c18a8174b6.mp4'
    historic = root/'阶段1_离线手动触发/outputs/relative-video-20261013-01/manifest.json'
    old = json.loads(historic.read_text(encoding='utf-8'))
    protected = {str(root/p): digest for p, digest in old['protected_sha256'].items()}
    protected[str(historic)] = sha(historic)
    old_verification = historic.parent/'verification.json'
    protected[str(old_verification)] = sha(old_verification)
    previous = json.loads(old_verification.read_text(encoding='utf-8'))
    for clip in previous['checks']:
        protected[str(historic.parent/clip['file'])] = clip['sha256']
    assert all(sha(Path(p)) == h for p, h in protected.items())
    args.output.mkdir(parents=True, exist_ok=False)
    base = datetime(2000, 1, 1, tzinfo=timezone.utc)
    stamp = lambda seconds: (base+timedelta(seconds=seconds)).isoformat().replace('+00:00','Z')
    duration = old['video_duration']
    session = 'synthetic-E-20000101'
    intervals = [dict(utc_start=stamp(lo), utc_end=stamp(hi), video_start=lo, video_end=hi,
        alignment=make_alignment(session, stamp(lo), lo)) for lo, hi in [(0,12),(15,duration)]]
    mapping = dict(schema='bounded-video-utc-v1', kind='synthetic',
        evidence='Synthetic UTC and missing interval 12..15; not actual capture metadata',
        session_id=session, source_video_sha256=sha(video), video_duration=duration,
        video_first_pts=0., format_start_pts=0., intervals=intervals)
    save_json(args.output/'mapping.synthetic.json', mapping)
    with (args.output/'events.synthetic.jsonl').open('x', encoding='utf-8') as output:
        for i, second in enumerate([1,8,13,17,36],1):
            output.write(json.dumps(dict(session_id=session,event_id=i,event_type='whistle',
                                         event_time_utc=stamp(second)))+'\n')
    command = [sys.executable, '-B', '-m', 'basketclip.mapped', '--video', str(video),
               '--events', str(args.output/'events.synthetic.jsonl'),
               '--mapping', str(args.output/'mapping.synthetic.json')]
    # Synthetic fixtures cannot accidentally run as a real-capture invocation.
    refused = subprocess.run(command+['--output',str(args.output/'must-not-exist')], capture_output=True)
    assert refused.returncode and not (args.output/'must-not-exist').exists()
    (args.output/'synthetic-mode-refusal.log').write_bytes(refused.stderr)
    result = subprocess.run(command+['--output',str(args.output/'clips'),'--synthetic-test'], capture_output=True)
    (args.output/'extract.log').write_bytes(result.stdout+result.stderr)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((args.output/'clips/manifest.json').read_text(encoding='utf-8'))
    assert [r['status'] for r in manifest['results']] == ['completed','completed','rejected','rejected','completed']
    checked = []
    for row in manifest['results']:
        if row['status'] != 'completed':
            assert not (args.output/'clips'/('event_%06d.mp4'%row['event']['event_id'])).exists()
            continue
        path = args.output/'clips'/row['file']
        info, actual = inspect_video(path)
        expected = row['end_seconds']-row['start_seconds']
        assert abs(actual-expected) <= 1/24
        decode = subprocess.run([require_tool('ffmpeg'),'-v','error','-xerror','-i',str(path),
                                 '-f','null','-'], capture_output=True)
        assert decode.returncode == 0, decode.stderr
        checked.append(dict(file=row['file'],actual_duration=actual,requested_duration=expected,
                            full_decode_exit=decode.returncode,sha256=sha(path)))
    assert all(sha(Path(p)) == h for p, h in protected.items())
    save_json(args.output/'verification.json', dict(status='passed',completed=3,rejected=2,
        source_and_history_hashes_match=True,protected_sha256=protected,clips=checked,
        synthetic_mode_required=True,gap_cases_rejected=True,real_capture_utc_verified=False,
        tolerance_seconds=1/24))
    print('Synthetic E: 3 clips decoded, 2 gap cases rejected; input/history hashes match')


if __name__ == '__main__':
    main()

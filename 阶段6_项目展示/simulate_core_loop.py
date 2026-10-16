# -*- coding: utf-8 -*-
"""F synthetic integration only: A -> C diagnostics -> test D -> synthetic E."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).absolute().parent.parent
sys.path.insert(0, str(ROOT/'阶段2_离线哨声检测/tools'))
sys.path.insert(0, str(ROOT/'阶段3_实时传输'))
sys.path.insert(0, str(ROOT/'阶段1_离线手动触发'))
from continuous_input import run_segment
from simulate_continuous_input import SimulatedSource, DeterministicPredictor
from sample_time import SamplingAnchor, SegmentTime
from auto_events import candidate_event, EventJournal, DurableReplay
import eventlink as link
from basketclip.relative import sha
from basketclip.media import inspect_video, require_tool
from basketclip.storage import save_json
from basketclip.timing import make_alignment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--synthetic-test', action='store_true', required=True)
    args = parser.parse_args()
    out = args.output.absolute()
    snapshots = [ROOT/'阶段2_离线哨声检测/outputs/continuous-input-20261014-03/verification.json',
                 ROOT/'阶段2_离线哨声检测/outputs/sample-time-20261014-01/verification.json',
                 ROOT/'阶段3_实时传输/outputs/auto-events-20261014-01/verification.json',
                 ROOT/'阶段1_离线手动触发/outputs/mapped-video-20261014-01/acceptance.json']
    protected = {}
    for path in snapshots:
        value = json.loads(path.read_text(encoding='utf-8'))
        protected.update({str(ROOT/name): digest for name, digest in value['hashes'].items()})
        protected[str(path)] = sha(path)
    video = ROOT/'data/new_video/5710e5b0e5691924647f88c18a8174b6.mp4'
    previous = json.loads((ROOT/'阶段1_离线手动触发/outputs/mapped-video-20261014-01/verification.json').read_text(encoding='utf-8'))
    protected.update(previous['protected_sha256'])
    assert all(sha(Path(p)) == h for p, h in protected.items())
    out.mkdir(parents=True, exist_ok=False)
    session = 'synthetic-F-20000101'
    base = datetime(2000, 1, 1, tzinfo=timezone.utc)
    stamp = lambda seconds: (base+timedelta(seconds=seconds)).isoformat().replace('+00:00','Z')
    journal = EventJournal(out/'events.synthetic.jsonl', session)
    segments, diagnostics, linked = [], [], []
    try:
        for name, second, mode in [('normal-1',1,'normal'),('normal-2',8,'normal'),
                                   ('video-gap',13,'normal'),('unknown',20,'unknown'),
                                   ('interrupted',25,'interrupted')]:
            candidates = []
            source = SimulatedSource(22050, 1, mode == 'interrupted')
            predictor = DeterministicPredictor()
            report = run_segment(source, predictor, candidates.append, 1, name, chunk_frames=777)
            assert source.closed and predictor.closed and report['retained_after_finish'] == 0
            assert len(candidates) == 1 and report['windows'] == 42
            segments.append(report)
            anchor = None if mode == 'unknown' else SamplingAnchor(name,0,48000,0,
                stamp(second),'synthetic fixture only; not actual sampling UTC',0)
            clock = SegmentTime(name,anchor,original_at_target_zero=0,
                                relation_evidence='synthetic 48000/22050 relation')
            if mode == 'interrupted':
                clock.invalidate('input_interrupted; new anchor required')
            candidate = candidates[0]
            timing = clock.candidate_time(candidate)
            # The production boundary must reject ALL synthetic and unknown times.
            try:
                candidate_event(candidate,timing,session,journal.saved+1)
            except ValueError:
                production_blocked = True
            else:
                raise AssertionError('Synthetic timing escaped production gate')
            row = dict(candidate=candidate,timing=timing,production_path_blocked=production_blocked)
            if timing['status'] == 'synthetic':
                # Dedicated test fixture only, never change status to verified_sampling.
                event = dict(session_id=session,event_id=journal.saved+1,event_type='whistle',
                             event_time_utc=timing['synthetic_utc'])
                journal.save(event)
                linked.append(dict(event=event,segment_id=name,synthetic_only=True))
                row.update(test_event_id=event['event_id'],test_fixture_saved=True)
            else:
                row.update(test_fixture_saved=False)
            diagnostics.append(row)
    finally:
        journal.close()
    assert journal.saved == 3 and all(d['production_path_blocked'] for d in diagnostics)
    save_json(out/'source-time-diagnostics.json',dict(segments=segments,diagnostics=diagnostics,links=linked))
    store = link.Store(out/'labels.synthetic.sqlite3',session)
    http = link.server(('127.0.0.1',0),store)
    receiver = threading.Thread(target=http.serve_forever,daemon=True)
    receiver.start()
    url = 'http://127.0.0.1:%d/events' % http.server_port
    original_send, attempts = link.send, []
    def send(url,event,timeout):
        attempts.append(event['event_id'])
        if len(attempts) == 1:
            raise OSError('F synthetic disconnection')
        ack = original_send(url,event,timeout)
        if len(attempts) == 2:
            raise OSError('F synthetic ACK loss after commit')
        return ack
    maximum = 0
    try:
        with patch.object(link,'send',side_effect=send):
            for _ in range(2):
                uploader = link.Uploader(url,timeout=.5,retry=.05)
                replay = DurableReplay(journal.path,session,uploader)
                try:
                    deadline = time.perf_counter()+5
                    while time.perf_counter() < deadline:
                        state = replay.pump()
                        maximum = max(maximum,len(uploader.snapshot()))
                        if state == 'eof':
                            break
                        time.sleep(.01)
                    else:
                        raise AssertionError('Replay timeout')
                finally:
                    replay.close()
                    pending = uploader.close(1)
                assert not pending and not uploader.thread.is_alive()
        received = [json.loads(row[0]) for row in store.db.execute('SELECT payload FROM events ORDER BY id')]
        assert received == [x['event'] for x in linked] and maximum <= 1
        with (out/'received.synthetic.jsonl').open('x',encoding='utf-8') as stream:
            for event in received:
                stream.write(json.dumps(event)+'\n')
    finally:
        http.shutdown()
        http.server_close()
        receiver.join(2)
        store.db.close()
    assert not receiver.is_alive()
    template = json.loads((ROOT/'阶段1_离线手动触发/outputs/mapped-video-20261014-01/mapping.synthetic.json').read_text(encoding='utf-8'))
    template['session_id'] = session
    for interval in template['intervals']:
        interval['alignment'] = make_alignment(session,interval['utc_start'],interval['video_start'])
    save_json(out/'mapping.synthetic.json',template)
    env = dict(os.environ, PYTHONPATH=str(ROOT/'阶段1_离线手动触发'))
    command = [sys.executable,'-B','-m','basketclip.mapped','--video',str(video),
               '--events',str(out/'received.synthetic.jsonl'),'--mapping',str(out/'mapping.synthetic.json'),
               '--output',str(out/'clips'),'--synthetic-test']
    rendered = subprocess.run(command,env=env,capture_output=True)
    (out/'extract.log').write_bytes(rendered.stdout+rendered.stderr)
    assert rendered.returncode == 0, rendered.stderr
    manifest = json.loads((out/'clips/manifest.json').read_text(encoding='utf-8'))
    assert [x['status'] for x in manifest['results']] == ['completed','completed','rejected']
    checks = []
    for row in manifest['results']:
        if row['status'] != 'completed':
            continue
        path = out/'clips'/row['file']
        info,duration = inspect_video(path)
        error = abs(duration-(row['end_seconds']-row['start_seconds']))
        assert error <= 1/24
        decode = subprocess.run([require_tool('ffmpeg'),'-v','error','-xerror','-i',str(path),'-f','null','-'],capture_output=True)
        assert decode.returncode == 0, decode.stderr
        checks.append(dict(file=row['file'],duration_error=error,sha256=sha(path),full_decode_exit=0))
    assert all(sha(Path(p)) == h for p,h in protected.items())
    save_json(out/'verification.json',dict(status='passed',scope='F local synthetic integration only',
        segments=5,candidates=5,production_gate_rejections=5,time_blocks=2,test_labels_saved=3,
        sqlite_rows=3,fields_equal=True,max_pending=maximum,attempts=attempts,
        clips_completed=2,video_gap_rejections=1,clip_checks=checks,resources_released=True,
        predictor='injected constant 0.9, no model execution',real_sampling_utc_verified=False,
        real_video_capture_utc_verified=False,hardware_tested=False,formal_real_event_upload=False,
        protected_sha256=protected,source_and_history_unchanged=True))
    print('F synthetic integration passed: 5 candidates, 2 time blocks, 3 test labels, 2 clips, 1 video-gap rejection')


if __name__ == '__main__':
    main()

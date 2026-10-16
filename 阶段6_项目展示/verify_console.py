"""Local console integration with an SSH substitute and synthetic labels; no board access."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import urllib.request

import workbench as web
import console_control as control
from test_console_control import FakeBoard


def fixture_board():
    board = FakeBoard()
    board.status, board.pcm = 'closed', 'closed'
    event = dict(session_id='trial', event_id=1, event_type='whistle', event_time_utc='2026-01-01T00:00:05Z')
    row = dict(session_id='trial', candidate=dict(segment_id='trial:segment-0001',
        estimated_start_sample=110250, closed_by='normal_stop'), timing=dict(status='software_estimate',
        estimated_utc=event['event_time_utc']))
    state = dict(session_id='trial', status='closed', resources_released=True, candidates_saved=1,
        time_basis='SYNTHETIC console software fixture; not hardware',
        automatic_labels=dict(saved=1, submitted=1, all_acknowledged=True, pending_event_ids=[]))
    metadata = dict(session_id='trial', event_id=1, source_segment='trial:segment-0001', target_start_sample=110250)
    for name, value in {'status.json':state, 'events.jsonl':event, 'event-metadata.jsonl':metadata,
                        'candidates.relative.jsonl':row}.items():
        board.files['capture/'+name] = (json.dumps(value)+'\n').encode()
    board.files['capture/producer.log'] = b'synthetic fixture, no producer\n'
    board.files['capture/sampling-diagnostics.jsonl'] = b''
    return board, event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Reopen only the isolated mock browser workspace')
    parser.add_argument('--port', type=int, default=8768)
    args = parser.parse_args()
    os.environ['PATH'] = str(web.ROOT/'tools/ffmpeg-9.0.2-essentials_build/bin')+os.pathsep+os.environ['PATH']
    output = args.output.absolute()
    if args.resume and not args.serve:
        parser.error('--resume is only for the mock browser workspace')
    app = web.Workbench(output, resume=args.resume)
    board, event = fixture_board()
    def factory(host, identity):
        board.error = ValueError('SSH认证失败（页面软件测试替身）') if host=='auth-error' else (
                      ValueError('网络不可达（页面软件测试替身）') if host=='unreachable' else None)
        return board
    app.console = control.Console(app, factory)
    app.console.local['validation_only'] = 'SSH substitute; all board states synthetic; NO hardware access'
    if args.serve:
        server = web.make_server(('127.0.0.1', args.port), app)
        print('READY SYNTHETIC CONSOLE http://127.0.0.1:%d/'%server.server_port, flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            app.close()
        return
    job = dict(id='synthetic-integration', status='running')
    def op(operation, **data):
        app.console.execute(operation, dict(session='trial', **data), job)
    video = web.ROOT/'data/new_video/5710e5b0e5691924647f88c18a8174b6.mp4'
    before = web.sha(video)
    try:
        op('connect', host='fixture', identity='fixture-no-key-read')
        op('new-session', playback=True, bind='127.0.0.1', video=str(video))
        # Plain receiver uses the existing Store/server, with a loopback synthetic label.
        plain = app.console.directory('offline-receiver')
        plain.mkdir()
        web.save_new(plain/'session.json', dict(session='offline-receiver'))
        app.console.start_receiver('offline-receiver', dict(bind='127.0.0.1'))
        synthetic = dict(event, session_id='offline-receiver')
        control.flow.eventlink.send('http://127.0.0.1:8765/events', synthetic, 2)
        control.flow.eventlink.send('http://127.0.0.1:8765/events', synthetic, 2)
        app.console.receiver_status(plain, control.flow.config(plain))
        assert app.console.local['pc_saved_labels']==1
        app.console.execute('receiver-stop',dict(session='offline-receiver'),job)
        # Reuse the exact same four-field fixture as captured board/PC evidence.
        folder = app.console.directory('trial')
        pc = folder/'pc'
        pc.mkdir()
        control.flow.eventlink.stage1_path(web.ROOT/'阶段1_离线手动触发')
        store = control.flow.eventlink.Store(pc/'labels.sqlite3', 'trial')
        store.save(event)
        store.db.close()
        web.save_new(pc/'service-closed.json', dict(scope='synthetic software fixture'))
        observations = []
        origin = datetime(2026,1,1,tzinfo=timezone.utc).timestamp()*1000
        for index in range(102):
            kind = 'begin' if index==0 else 'playing' if index==1 else 'user_stop' if index==101 else 'sample'
            seconds = max(0,index-1)/10
            row = dict(kind=kind, wall_ms=origin+seconds*1000, performance_ms=seconds*1000,
                       media_seconds=seconds, rate=1, paused=kind in ('begin','user_stop'), ready_state=4,
                       sequence=index+1)
            observations.append(dict(session_id='trial', observation=row, time_quality='software_playback_observation',
                                     fixture='synthetic, not real playback evidence'))
        (pc/'playback-observations.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in observations))
        op('download')
        op('playback-finish')
        entry = app.entry(job['entry_id'])
        assert len(entry['results'])==1 and entry['results'][0]['full_decode_verified']
        assert not entry['reviews'] and not entry['historical_reviews']
        assert entry['alignment']['kind']=='software_playback_observation'
        app.persist()
    finally:
        app.close()
    assert web.sha(video)==before
    restored = web.Workbench(output, resume=True)
    try:
        assert restored.console.local['connection']=='disconnected'
        assert restored.state['entries'][0]['results'][0]['full_decode_verified']
    finally:
        restored.close()
    web.save_new(output/'integration-verification.json', dict(passed=True,
        scope='Synthetic SSH, synthetic label/playback observations, real loopback Store/ACK/dedup and FFmpeg',
        source_sha256_unchanged=before, manual_confirmation_created=False, hardware_connected=False))


if __name__ == '__main__':
    main()

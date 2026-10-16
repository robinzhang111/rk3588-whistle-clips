"""Offline use-entry regression with transformed historical fixtures; no services or board."""
import argparse
import copy
import json
from pathlib import Path

import session_flow as flow


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args=parser.parse_args()
    output=args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    historical=flow.ROOT/'阶段6_项目展示'/'outputs'/'core-live-playback-20261015-03'
    source=flow.ROOT/'data'/'new_video'/'5710e5b0e5691924647f88c18a8174b6.mp4'
    protected={str(p): flow.sha(p) for p in historical.rglob('*') if p.is_file()}
    acceptance=flow.load_json(historical/'acceptance.json')
    for name in acceptance['protected_sha256']:
        path=flow.ROOT/name
        protected[str(path)]=flow.sha(path)
    protected[str(source)]=flow.sha(source)
    session='software-fixture-delivery'
    directory=output/session
    flow.prepare(directory, session, '127.0.0.1', source)
    cfg=flow.config(directory)
    # These are deliberately transformed test inputs, not new hardware observations.
    records=copy.deepcopy(flow.jsonl(historical/'playback-observations.snapshot.jsonl'))
    for i, record in enumerate(records, 1):
        record['session_id']=session
        record['observation']['sequence']=i
    (directory/'pc').mkdir()
    with (directory/'pc'/'playback-observations.jsonl').open('x', encoding='utf-8') as target:
        for row in records:
            target.write(json.dumps(row)+'\n')
    events=flow.jsonl(historical/'board-originals'/'events.jsonl')
    for row in events:
        row['session_id']=session
    store=flow.eventlink.Store(directory/'pc'/'labels.sqlite3', session)
    for row in events:
        store.save(row)
    store.db.close()
    flow.save_json(directory/'pc'/'service-closed.json', dict(source='software fixture, no service started'))
    board=directory/'board-originals'
    board.mkdir()
    with (board/'events.jsonl').open('x', encoding='utf-8') as target:
        for row in events:
            target.write(json.dumps(row)+'\n')
    flow.save_json(board/'status.json', dict(session_id=session, status='closed', resources_released=True,
        automatic_labels=dict(submitted=1, all_acknowledged=True, pending_event_ids=[]),
        source='transformed software fixture; not runtime ACK evidence'))
    for name in flow.FILES:
        if not (board/name).exists():
            (board/name).write_text('fixture only\n', encoding='utf-8')
    flow.save_json(directory/'download-verification.json', dict(matched=True,
        remote_sha256={name:flow.sha(board/name) for name in flow.FILES},
        source='local fixture hashes; no remote download performed'))
    flow.export(directory, session)
    flow.main(['finish', '--directory', str(directory)])
    state=flow.status(directory, cfg)
    assert state['pc_confirmed'] and state['clips_generated']==1
    assert state['human_confirmation']=='pending' and not state['full_product_accepted']
    assert not state['clips_rejected_or_failed']
    position=flow.load_json(directory/'clips'/'manifest.json')['results'][0]['video_event_seconds']
    assert abs(position-2.9065268281314265)<1e-6
    try:
        flow.prepare(directory, session, '127.0.0.1', source)
    except FileExistsError:
        pass
    else:
        raise AssertionError('Existing session was not rejected')
    unchanged=all(flow.sha(Path(p))==digest for p,digest in protected.items())
    assert unchanged
    flow.save_json(output/'verification.json', dict(
        scope='offline entry regression using transformed historical fixtures; no new capture/ACK/download',
        passed=True, source_history_files_unchanged=unchanged,
        original_03_acceptance_hashes_match=all(flow.sha(flow.ROOT/p)==digest for p,digest in acceptance['protected_sha256'].items()),
        protected_sha256=protected, result=state, fixture_video_position=position,
        real_hardware_validation=False, human_review_performed=False))
    print('PASS: offline prepare/export/finish/decode/status; historical inputs preserved')


if __name__=='__main__':
    main()

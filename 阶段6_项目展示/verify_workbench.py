"""Offline workbench integration: existing media + explicitly synthetic candidate fixture."""
import argparse
import json
import os
from pathlib import Path

import workbench as web


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--video',type=Path,help='Explicit existing or synthetic test media; source remains read-only')
    args=parser.parse_args()
    output=args.output.absolute()
    output.mkdir(parents=True,exist_ok=False)
    os.environ['PATH']=str(web.ROOT/'tools/ffmpeg-9.0.2-essentials_build/bin')+os.pathsep+os.environ['PATH']
    video=args.video.absolute() if args.video else web.ROOT/'data/new_video/5710e5b0e5691924647f88c18a8174b6.mp4'
    protected={str(video):web.sha(video)}
    source=output/'synthetic-input'
    source.mkdir()
    rows=[dict(session_id='synthetic-workbench-fixture',candidate=dict(
        segment_id='synthetic-workbench-fixture:segment-1',estimated_start_sample=22050,
        closed_by='normal_stop'),timing=dict(status='relative_only',estimated_utc=None))]
    (source/'candidates.relative.jsonl').write_text(json.dumps(rows[0])+'\n',encoding='utf-8')
    web.save_new(source/'status.json',dict(session_id='synthetic-workbench-fixture',status='closed',
        resources_released=True,source='synthetic fixture only, no capture',time_basis='synthetic relative samples'))
    protected.update({str(p):web.sha(p) for p in source.iterdir()})
    app=web.Workbench(output/'workspace')
    try:
        job=dict(id='fixture-job',status='running',message='')
        entry=app.import_result(dict(session_dir=str(source),video=str(video)),job)
        try:
            app.extract(entry,dict(numbers=[1]),job)
        except ValueError:
            pass
        else:
            raise AssertionError('Missing alignment should block extraction')
        app.plan(entry,dict(confirmed=True,evidence='synthetic fixture offset, not real camera synchronization',
            offset=0,segment_id=rows[0]['candidate']['segment_id']),job)
        app.extract(entry,dict(numbers=[1]),job)
        assert entry['results'][0]['full_decode_verified']
        assert entry['results'][0]['video_seconds']==1
        assert not entry['reviews']
        app.review(entry,dict(candidate_number=1,whistle='uncertain',actual_clip_seconds=None,
            position_confirmed=False,same_whistle_as=None,note='automated software fixture; not human truth'),job)
        for scenario in ('waiting','running','closed','failed','relative'):
            demo=app.simulate(dict(scenario=scenario))
            assert demo['view']['ten_minute_acceptance']=='待实机验收'
        app.persist()
    finally:
        app.close()
    resumed=web.Workbench(output/'workspace',resume=True)
    try:
        assert resumed.entry(entry['id'])['reviews']['1']['whistle']=='uncertain'
        assert len(resumed.state['entries'])==6
    finally:
        resumed.close()
    assert all(web.sha(Path(p))==digest for p,digest in protected.items())
    web.save_new(output/'verification.json',dict(passed=True,
        scope='offline synthetic-candidate integration with real existing media; no hardware or user review',
        actual_clip_extracted=True,full_decode_verified=True,missing_alignment_blocked=True,
        feedback_is_automated_fixture=True,feedback_preserved_on_resume=True,
        simulated_states=5,source_hashes_unchanged=True,protected_sha256=protected,
        hardware_connected=False,new_capture=False,ten_minute_hardware_accepted=False))
    print('PASS: import / alignment gate / original clip extraction / full decode / fixture feedback / resume')


if __name__=='__main__':
    main()

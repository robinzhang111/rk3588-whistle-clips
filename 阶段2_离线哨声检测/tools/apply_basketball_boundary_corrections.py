# -*- coding: utf-8 -*-
"""Version two explicitly confirmed human corrections; never change old files."""
from pathlib import Path
from evaluate_testbench_events import read,write,sha

stage=Path(__file__).absolute().parent.parent
source=Path('C:/Users/YOUR_USER/Downloads/basketball_boundary_corrections.json')
truth_path=stage/'outputs/basketball-manual-review-20261012-02/manual_annotations_coverage_corrected.json'
out=stage/'outputs/basketball-manual-review-20261012-03'
out.mkdir(exist_ok=False)
(out/'boundary_corrections_received.json').write_bytes(source.read_bytes())
patch=read(source);truth=read(truth_path)
assert patch['schema']=='basketball-boundary-corrections-v1' and patch['source_annotation_sha256']==sha(truth_path)
allowed={'B04-1791772881994-3','B05-1791772939661-4'}
assert len(patch['corrections'])==2 and {e['event_id'] for e in patch['corrections']}==allowed
material=read(stage/'outputs/basketball-manual-20261012-01/material.json')
changes=[]
for c in patch['corrections']:
    e=next(e for e in truth['events'] if e['id']==c['event_id'])
    f=next(f for f in material['files'] if f['id']==e['file_id'])
    assert e['file_id']==c['file_id'] and c['rate']==f['rate']==44100
    assert e['start_sample']==c['old_start_sample'] and e['end_sample']==c['old_end_sample']
    assert type(c['start_sample']) is int and type(c['end_sample']) is int and 0<=c['start_sample']<c['end_sample']<=f['frames']
    old=e.copy();e.update(start_sample=c['start_sample'],end_sample=c['end_sample'],boundary_method=c['method'])
    changes.append(dict(event_id=e['id'],old=old,new=e.copy(),
        old_seconds=[old['start_sample']/44100,old['end_sample']/44100],
        new_seconds=[e['start_sample']/44100,e['end_sample']/44100]))
original=read(truth_path)
assert [e for e in truth['events'] if e['id'] not in allowed]==[e for e in original['events'] if e['id'] not in allowed]
assert truth['files']==original['files'] and len(truth['events'])==7
truth['derived_correction']=dict(source_sha256=sha(truth_path),patch_sha256=sha(source),record='receipt.json',final_truth_confirmation_pending=False)
write(out/'manual_annotations_corrected.json',truth)
write(out/'receipt.json',dict(user_reply='basketball_boundary_corrections.json 更正完成',
    source_patch_sha256=sha(source),previous_truth_sha256=sha(truth_path),
    corrected_truth_sha256=sha(out/'manual_annotations_corrected.json'),changes=changes,
    unchanged_other_five_events=True,unchanged_all_coverage=True,original_and_download_unmodified=True))
write(out/'user-final-confirmation.json',dict(user_reply='更正完成',
    confirmation_basis='two separately confirmed intervals in exported correction file',
    annotation_sha256=sha(out/'manual_annotations_corrected.json'),scope='seven audible blows; source identities remain unknown'))
print(changes)

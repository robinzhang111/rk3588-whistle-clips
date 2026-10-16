# -*- coding: utf-8 -*-
"""Versioned intake of user annotations; scope excludes non-basketball sources."""
from pathlib import Path
import json
import subprocess
import sys
from evaluate_testbench_events import read, write, sha

stage = Path(__file__).absolute().parent.parent
received = Path('C:/Users/YOUR_USER/Downloads/basketball_new_manual_annotations.json')
material_path = stage/'outputs/basketball-new-manual-20261012-01/material.json'
original_material = read(material_path)
annotation = read(received)
out = stage/'outputs/basketball-new-manual-review-20261012-01'
out.mkdir(exist_ok=False)
payload = received.read_bytes()
snapshot = out/'manual_annotations_received.json'
snapshot.write_bytes(payload)
assert snapshot.read_bytes() == payload
write(out/'receipt.json',dict(received_path=str(received),bytes=len(payload),sha256=sha(received),
    original_material_path=str(material_path),material_sha256=sha(material_path),
    user_scope_confirmation='First nine are N01-N09; N10-N17 are not basketball; user deleted their original videos',
    user_adjacent_confirmation='N04 touching intervals around 4.313-4.458 and 4.458-4.603 are two independent blows',
    no_audio_deleted_by_assistant=True))
def validate(material, annotations, output):
    command=[sys.executable,str(stage/'tools/validate_basketball_annotations.py'),
        '--material',str(material),'--annotations',str(annotations),'--output',str(output)]
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    (out/(output.stem+'-run.log')).write_bytes(result.stdout+result.stderr)
    assert result.returncode == 0
validate(material_path,snapshot,out/'received-validation.json')
selected = ['N'+str(i).zfill(2) for i in range(1,10)]
assert all(e['file_id'] in selected for e in annotation['events'])
material = dict(original_material)
material['files'] = [f for f in original_material['files'] if f['id'] in selected]
material['material_id'] = original_material['material_id']+'-basketball-N01-N09'
material['parent_material_id'] = original_material['material_id']
scoped = dict(annotation)
scoped.update(material_id=material['material_id'],
    source_files=[f for f in annotation['source_files'] if f['id'] in selected],
    files=[f for f in annotation['files'] if f['id'] in selected])
scoped['complete_review'] = all(all(f['reviewed_channels']) and f['context_reviewed'] for f in scoped['files'])
scoped['derived_scope'] = dict(parent_material_id=original_material['material_id'],
    events_and_positions_unchanged=True,excluded_ids=['N'+str(i) for i in range(10,18)],
    reason='User confirms not basketball; not negative training examples',
    n09_channel1_confirmation_pending=True)
write(out/'material-scoped.json',material)
write(out/'manual_annotations_scoped.json',scoped)
validate(out/'material-scoped.json',out/'manual_annotations_scoped.json',out/'scoped-validation.json')
validation=read(out/'scoped-validation.json')
inventory=[]
for f in original_material['files']:
    video=Path(f['video_path']);audio=Path(f['audio_path'])
    audio_ok=audio.exists() and sha(audio)==f['audio_sha256']
    assert audio_ok
    exists=video.exists()
    video_ok=sha(video)==f['video_sha256'] if exists else None
    if f['id'] in selected:assert exists and video_ok
    inventory.append(dict(id=f['id'],selected=f['id'] in selected,video_path=str(video),video_exists=exists,
        video_sha256_expected=f['video_sha256'],video_hash_matches_if_present=video_ok,
        audio_path=str(audio),audio_sha256=f['audio_sha256'],audio_exists=True,audio_hash_matches=True,
        excluded_reason=None if f['id'] in selected else 'User confirms not basketball; source video removed by user',
        duplicate_aliases=f['duplicate_aliases']))
write(out/'current-source-inventory.json',inventory)
touching=[]
for fid in selected:
    events=sorted([e for e in scoped['events'] if e['file_id']==fid],key=lambda e:e['start_sample'])
    for a,b in zip(events,events[1:]):
        if a['end_sample']==b['start_sample']:
            touching.append(dict(file_id=fid,event_ids=[a['id'],b['id']],
                boundary_sample=a['end_sample'],user_confirms_two_independent_blows=(fid=='N04'),
                action='Keep two events and original boundaries; no automatic merging'))
write(out/'touching-event-confirmation.json',touching)
assert scoped['events']==annotation['events'] and sha(received)==sha(snapshot)
summary=dict(selected_files=selected,excluded_nonbasketball_files=[r['id'] for r in inventory if not r['selected']],
    event_count=len(scoped['events']),events_per_file={fid:sum(e['file_id']==fid for e in scoped['events']) for fid in selected},
    positive_interval_seconds=sum((e['end_sample']-e['start_sample'])/next(f['rate'] for f in material['files'] if f['id']==e['file_id']) for e in scoped['events']),
    selected_audio_seconds=sum(f['duration'] for f in material['files']),
    coverage_complete=validation['coverage_complete'],unreviewed_files=validation['unreviewed_files'],
    overlaps=validation['overlaps_to_review'],uncertain_count=validation['uncertain_count'],
    unverified_identity_count=validation['unverified_identity_count'],
    n04_touching_events_user_confirmed=True,n09_channel1_confirmation_pending=True,
    all_17_original_extracted_audio_hashes_match=True,selected_9_videos_exist_and_hash_match=True,
    excluded_8_video_files_missing=all(not r['video_exists'] for r in inventory if not r['selected']),
    automatic_training_labels_created=False,model_not_run=True,ready_for_final_truth_confirmation=False)
write(out/'summary.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2))

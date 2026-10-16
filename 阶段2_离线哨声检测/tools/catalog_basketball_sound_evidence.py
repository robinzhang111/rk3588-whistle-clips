# -*- coding: utf-8 -*-
"""Catalog existing human evidence only, not a generated training dataset."""
from pathlib import Path
from evaluate_testbench_events import read,write,sha

stage=Path(__file__).absolute().parent.parent
out=stage/'outputs/basketball-data-plan-20261012-01'
out.mkdir(exist_ok=False)
review=stage/'outputs/basketball-failure-review-20261012-01'
truth_path=stage/'outputs/basketball-manual-review-20261012-04/manual_annotations_corrected.json'
truth=read(truth_path);cases=read(review/'cases-current.json')['cases']
material=read(stage/'outputs/basketball-manual-20261012-01/material.json')
protected=read(stage/'outputs/basketball-evaluation-20261012-03/verification.json')['source_sha256']
sources=[truth_path,review/'listening-feedback-01.json',review/'listening-feedback-02.json',review/'listening-feedback-03.json',review/'listening-feedback-04.json',review/'listening-feedback-05.json',review/'listening-feedback-06.json',review/'listening-summary-current.json']
protected.update({str(p):sha(p) for p in sources})
assert all(sha(Path(p))==h for p,h in protected.items())
records=[]
def add(record):
    f=next(f for f in material['files'] if f['id']==record['file_id'])
    record.update(original_rate=f['rate'],original_audio_path=f['audio_path'],original_audio_sha256=f['audio_sha256'],video_sha256=f['video_sha256'],
                  match_group_id=f['group_id'],usage='development diagnostic only if used for future improvement',
                  annotation_precision='human approximate; model frames are not natural sound boundaries')
    records.append(record)
for e in truth['events']:
    add(dict(evidence_id=e['id'],file_id=e['file_id'],binary_candidate_label=1,subtype='whistle',
        start_sample=e['start_sample'],end_sample=e['end_sample'],source_identity=e['source_identity'],
        evidence_kind='user_confirmed_event_interval',boundary_status='confirmed approximate',
        reference=str(truth_path)))
shoes=['FP02','FP03','FP06','FP07','FP08','FP09','FP10','FP11','FP12','FP13','FP14']
shouts=['FP04','FP05']
for cid in shoes+shouts:
    r=next(r for r in cases if r['id']==cid)
    add(dict(evidence_id=cid,file_id=r['file_id'],binary_candidate_label=0,
        subtype='shoe_friction' if cid in shoes else 'human_shout',
        start_sample=r['prediction']['estimated_start_sample'],end_sample=r['prediction']['estimated_end_sample'],
        evidence_kind='user_confirmed_nonwhistle_target',boundary_status='candidate-window region; not manually bounded natural noise event',
        reference=str(review/('listening-feedback-02.json' if cid in ['FP02','FP03','FP04'] else 'listening-feedback-04.json' if cid in ['FP05','FP06','FP07','FP08'] else 'listening-feedback-05.json'))))
for cid in ['L01','L02','L03','L05']:
    r=next(r for r in cases if r['id']==cid)
    add(dict(evidence_id=cid+'-extension',file_id=r['file_id'],binary_candidate_label=0,
        subtype='shoe_friction' if cid=='L05' else 'human_shout',
        start_sample=r['label']['end_sample'],end_sample=r['prediction']['estimated_end_sample'],
        evidence_kind='user_reported_post_whistle_nonwhistle',boundary_status='provisional post-label range; near-whistle boundary needs training guard',
        reference=str(review/'listening-feedback-06.json')))
by_file={f['id']:[] for f in material['files']}
for r in records:
    if r['binary_candidate_label']==0:by_file[r['file_id']].append(r)
unions=[]
for fid,items in by_file.items():
    groups=[]
    for r in sorted(items,key=lambda r:r['start_sample']):
        if groups and r['start_sample']<=groups[-1]['end_sample']:
            groups[-1]['end_sample']=max(groups[-1]['end_sample'],r['end_sample']);groups[-1]['evidence_ids'].append(r['evidence_id']);groups[-1]['subtypes']=sorted(set(groups[-1]['subtypes']+[r['subtype']]))
        else:groups.append(dict(file_id=fid,start_sample=r['start_sample'],end_sample=r['end_sample'],evidence_ids=[r['evidence_id']],subtypes=[r['subtype']]))
    unions.extend(groups)
for r in records:
    if r['binary_candidate_label']==0:
        overlaps=[e['id'] for e in truth['events'] if e['file_id']==r['file_id'] and r['start_sample']<e['end_sample'] and r['end_sample']>e['start_sample']]
        assert not overlaps
write(out/'sound-evidence.json',dict(schema='basketball-human-evidence-catalog-v1',records=records,
    known_positive_events=7,confirmed_FP_negative_regions=13,post_whistle_negative_regions=4,
    uncertainty_exclusions=[dict(id='FP01',reason='later no-second-blow clarification; exact response sound identity unspecified'),dict(id='L04-gap',reason='nonwhistle gap confirmed but identity/boundaries need dedicated annotation'),dict(id='B04-timer',reason='timer mentioned in excluded annotation tail; no independent timer interval annotation')],
    training_files_generated=False,scoring_or_model_run=False))
write(out/'negative-region-unions.json',dict(regions=unions,total_nonoverlapping_seconds=sum((r['end_sample']-r['start_sample'])/44100 for r in unions),
    scope='overlap/touching union only; not physical noise event counts or ready training frame labels'))
write(out/'verification.json',dict(all_protected_inputs_unchanged=True,protected_sha256=protected,
    positive_records_equal_latest_truth=True,negative_regions_do_not_overlap_confirmed_whistle_intervals=True,
    evidence_records=len(records),negative_union_regions=len(unions),no_new_audio_features_prediction_training=True,
    user_new_material_constraint='mainly clips seconds to tens of seconds',
    limitations='negative natural boundaries, guard regions and split assignment pending'))
assert all(sha(Path(p))==h for p,h in protected.items())
print('Evidence records',len(records),'negative union regions',len(unions),'union seconds',sum((r['end_sample']-r['start_sample'])/44100 for r in unions))

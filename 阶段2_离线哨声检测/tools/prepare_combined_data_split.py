# -*- coding: utf-8 -*-
"""Summarize source-bound evidence and propose groups without training/scoring."""
from pathlib import Path
from evaluate_testbench_events import read, write, sha

stage = Path(__file__).absolute().parent.parent
old_truth = stage/'outputs/basketball-manual-review-20261012-04/manual_annotations_corrected.json'
new_truth = stage/'outputs/basketball-new-manual-review-20261012-02/manual_annotations_coverage_confirmed.json'
old_material = stage/'outputs/basketball-manual-20261012-01/material.json'
new_material = stage/'outputs/basketball-new-manual-review-20261012-02/material-scoped.json'
negative_path = stage/'outputs/basketball-data-plan-20261012-01/negative-region-unions.json'
bhuman = stage/'outputs/bhuman-reuse-preparation-20261012-01'
inputs = [old_truth,new_truth,old_material,new_material,negative_path,
    bhuman/'positive-group-candidates.json',bhuman/'heard-negative-context-candidates.json',
    bhuman/'summary.json',stage/'evaluation/splits-v1.json']
protected = {str(p):sha(p) for p in inputs}
materials = [read(old_material),read(new_material)]
truths = [read(old_truth),read(new_truth)]
all_files = [f for m in materials for f in m['files']]
for f in all_files:
    protected[f['video_path']] = f['video_sha256']
    protected[f['audio_path']] = f['audio_sha256']
for p in read(bhuman/'positive-group-candidates.json'):
    protected[p['original_audio_path']] = p['original_audio_sha256']
assert all(sha(Path(p))==h for p,h in protected.items())
out = stage/'outputs/combined-data-split-plan-20261012-01'
out.mkdir(exist_ok=False)
proposal = {'development':['N01','N04','N06','N07','N09'],
            'validation':['N02','N03'], 'reserved_test':['N05','N08']}
assign = {fid:split for split,ids in proposal.items() for fid in ids}
assert len(assign)==9
file_rows,positive_rows,background = [],[],[]
for material,truth in zip(materials,truths):
    assert truth['material_id']==material['material_id']
    expected=[{k:f[k] for k in ['id','video_sha256','audio_sha256','rate','channels','frames']} for f in material['files']]
    assert truth['source_files']==expected
    for f in material['files']:
        fid=f['id'];split='development' if fid.startswith('B') else assign[fid]
        events=sorted([e for e in truth['events'] if e['file_id']==fid],key=lambda e:e['start_sample'])
        coverage=next(r for r in truth['files'] if r['id']==fid)
        assert all(coverage['reviewed_channels']) and coverage['context_reviewed']
        for e in events:
            assert e['kind']=='whistle' and 0<=e['start_sample']<e['end_sample']<=f['frames']
            positive_rows.append(dict(event=e,original_audio_path=f['audio_path'],audio_sha256=f['audio_sha256'],
                rate=f['rate'],group_id=f['group_id'],proposed_split=split,
                duration_seconds=(e['end_sample']-e['start_sample'])/f['rate'],
                annotation_source=str(old_truth if fid.startswith('B') else new_truth),
                ready_training_window=False,reason='Event reference; window labels and boundary guard not yet defined'))
        row={k:f[k] for k in ['id','group_id','video_filename','video_path','video_sha256','audio_path','audio_sha256','rate','channels','frames','duration']}
        row.update(proposed_split=split,split_status='proposal_only_not_frozen',event_count=len(events),
            positive_seconds=sum((e['end_sample']-e['start_sample'])/f['rate'] for e in events),
            source_basis='User states different matches; hashes only rule out exact duplicate files',
            prior_model_diagnostic_exposure=fid.startswith('B'))
        file_rows.append(row)
        cursor=0
        for e in events:
            assert e['start_sample']>=cursor
            if cursor<e['start_sample']:
                background.append(dict(file_id=fid,start_sample=cursor,end_sample=e['start_sample'],rate=f['rate'],
                    group_id=f['group_id'],proposed_split=split,binary_label=None,
                    status='Non-target complement after human review; negative suitability and boundary guard pending'))
            cursor=e['end_sample']
        if cursor<f['frames']:
            background.append(dict(file_id=fid,start_sample=cursor,end_sample=f['frames'],rate=f['rate'],
                group_id=f['group_id'],proposed_split=split,binary_label=None,
                status='Non-target complement after human review; negative suitability and boundary guard pending'))
assert len(positive_rows)==27 and len(file_rows)==15
assert len({f['group_id'] for f in file_rows})==15
assert len({f['video_sha256'] for f in file_rows})==15
assert len({f['audio_sha256'] for f in file_rows})==15
negatives=read(negative_path)['regions']
for n in negatives:
    f=next(f for f in file_rows if f['id']==n['file_id'])
    assert n['file_id'].startswith('B')
    assert all(not (n['start_sample']<p['event']['end_sample'] and p['event']['start_sample']<n['end_sample'])
               for p in positive_rows if p['event']['file_id']==n['file_id'])
    n.update(original_audio_path=f['audio_path'],audio_sha256=f['audio_sha256'],rate=f['rate'],
        group_id=f['group_id'],proposed_split='development',duration_seconds=(n['end_sample']-n['start_sample'])/f['rate'],
        ready_training_window=False,status='Human negative sound evidence; model-derived region bounds need review')
public_pos=read(bhuman/'positive-group-candidates.json')
public_neg=read(bhuman/'heard-negative-context-candidates.json')
public_stats=read(bhuman/'summary.json')
write(out/'basketball-source-split-proposal.json',dict(status='proposal_only_not_frozen',
    assignments=file_rows,selection_basis='Annotation counts and source diversity only; no new model outputs inspected',
    policy='Old B01-B06 diagnostic sources development only; same match/channel/transformation remains one group',
    excluded_N10_N17='Not basketball per user; preserved extracted audio not used',
    limitations='Different matches based on user confirmation; source overlap across edits/shared recordings not independently audited'))
write(out/'basketball-positive-event-index.json',positive_rows)
write(out/'basketball-background-review-candidates.json',background)
write(out/'basketball-known-negative-regions.json',negatives)
write(out/'public-candidate-use-index.json',dict(positive_groups=public_pos,heard_negative_contexts=public_neg,
    policy='Existing development/validation/test preserved; never add held-out sources to training',
    group_count_not_single_blow_count=True))
split_summary={}
for split in ['development','validation','reserved_test']:
    files=[f for f in file_rows if f['proposed_split']==split]
    split_summary[split]=dict(files=len(files),matches=len(files),file_ids=[f['id'] for f in files],
        events=sum(f['event_count'] for f in files),audio_seconds=sum(f['duration'] for f in files),
        positive_seconds=sum(f['positive_seconds'] for f in files))
summary=dict(basketball_files=15,basketball_events=27,
    basketball_audio_seconds=sum(f['duration'] for f in file_rows),
    basketball_positive_seconds=sum(p['duration_seconds'] for p in positive_rows),
    basketball_background_complement_seconds=sum((r['end_sample']-r['start_sample'])/r['rate'] for r in background),
    known_basketball_negative_union_regions=len(negatives),known_basketball_negative_seconds=sum(r['duration_seconds'] for r in negatives),
    known_negative_seconds_by_subtype={kind:sum(r['duration_seconds'] for r in negatives if r['subtypes']==[kind]) for kind in ['shoe_friction','human_shout']},
    split_proposal=split_summary,public_data=public_stats,
    old_and_new_events_kept_separate_from_publisher_groups=True,
    all_basketball_event_identities_unverified=all(p['event']['source_identity']=='unverified' for p in positive_rows),
    negatives_not_added_to_background_duration_to_avoid_double_count=True,
    new_input_44100_to22050_adaptation_pending=True,split_freeze_pending=True,
    ready_for_training=False,model_not_run=True)
assert abs(summary['basketball_positive_seconds']+summary['basketball_background_complement_seconds']-summary['basketball_audio_seconds'])<1e-8
assert all(sha(Path(p))==h for p,h in protected.items())
write(out/'summary.json',summary)
write(out/'verification.json',dict(protected_sha256=protected,all_hashes_unchanged=True,
    all_source_bindings_and_intervals_valid=True,all_15_groups_proposed_once=True,
    all_27_event_objects_preserved=True,negative_regions_not_overlapping_positive=True,
    no_exact_source_duplicate_across_groups=True,source_match_independence_basis='user confirmation only',
    complement_duration_conserved=True,no_training_or_inference=True))
print(__import__('json').dumps(summary,ensure_ascii=False,indent=2))

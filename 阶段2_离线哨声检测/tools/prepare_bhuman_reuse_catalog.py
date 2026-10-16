# -*- coding: utf-8 -*-
"""Catalog public-data reuse evidence; do not create training windows or labels."""
from pathlib import Path
import csv
import json
import wave
from evaluate_testbench_events import read, write, sha

stage = Path(__file__).absolute().parent.parent
prepared = stage/'data/bhuman2017-prepared-v1'
manifest = read(prepared/'manifest.json')
annotations = read(prepared/'annotations_seconds.json')
listening_path = stage/'outputs/bhuman2017-followup-20261011-01/listening-checklist.json'
listening = read(listening_path)
split_path = stage/'evaluation/splits-v1.json'
splits = read(split_path)['splits']
inputs = [prepared/'manifest.json', prepared/'annotations_seconds.json', prepared/'anomalies.json',
          listening_path, split_path, stage.parent/'data/whistle/whistledb.json',
          stage/'outputs/basketball-manual-review-20261012-04/manual_annotations_corrected.json',
          stage/'outputs/basketball-new-manual-20261012-01/material.json']
protected = {str(p):sha(p) for p in inputs}
protected.update({f['path']:f['sha256'] for f in manifest})
assert all(sha(Path(p)) == h for p,h in protected.items())
out = stage/'outputs/bhuman-reuse-preparation-20261012-01'
out.mkdir(exist_ok=False)
file_splits = {filename:name for name,s in splits.items() for filename in s['files']}
assert len(file_splits) == len(manifest) == 10
assert sum(len(s['groups']) for s in splits.values()) == len(set(g for s in splits.values() for g in s['groups']))
write(out/'plan-before-catalog.json', dict(protected_sha256=protected,
    existing_splits_preserved=True, permission_basis='User previously confirmed all needed uses permitted; official license not independently reverified',
    no_audio_conversion=True, no_training_windows=True, no_model_execution=True))
positives, backgrounds, negatives = [], [], []
for f in manifest:
    with wave.open(f['path'], 'rb') as w:
        assert (w.getframerate(),w.getnchannels(),w.getsampwidth(),w.getnframes()) == (f['sample_rate_hz'],2,1,f['sample_frames'])
    labels = sorted([a for a in annotations if a['filename'] == f['filename'] and a['channel_index'] == 0],
                    key=lambda a:a['effective_start'])
    for a in labels:
        assert a['valid'] and 0 <= a['effective_start'] < a['effective_end'] <= f['sample_frames']
        heard = [r for r in listening if r['source_file'] == f['filename'] and r['source_channel'] == 0
                 and 'original_label_start_seconds' in r
                 and round(r['original_label_start_seconds']*f['sample_rate_hz']) == a['effective_start']
                 and round(r['original_label_end_seconds']*f['sample_rate_hz']) == a['effective_end']]
        assert len(heard) <= 1
        report = heard[0]['user_report'] if heard else {}
        status = 'publisher_group_needs_listening'
        if report.get('whistle_count') == 1:
            status = 'representative_single_blow_heard_boundary_not_reannotated'
        if report.get('whistle_count',0) > 1:
            status = 'multiple_blows_needs_single_blow_boundaries'
        if report.get('audible_truncation'):
            status = 'truncated_blow_separate_edge_case'
        positives.append(dict(candidate_id=f['filename']+'#C0-label-'+str(a['label_index']),
            filename=f['filename'], original_audio_path=f['path'], original_audio_sha256=f['sha256'],
            channel_index=0, rate=f['sample_rate_hz'], start_sample=a['effective_start'],end_sample=a['effective_end'],
            duration_seconds=(a['effective_end']-a['effective_start'])/f['sample_rate_hz'],
            group_id=f['group_id'], split=file_splits[f['filename']], source_json_pointer=a['source_json_pointer'],
            source_json_sha256=a['source_json_sha256'], raw_label=a['raw_label'],
            status=status, user_heard_count=report.get('whistle_count'),
            listening_reference_id=heard[0]['id'] if heard else None,
            binary_candidate_label=1, automatic_training_use=False,
            target_identity='publisher target label; not separately verified for basketball field identity'))
    # Exact complement of publisher target groups, not a whistle-free assertion.
    cursor = 0
    for a in labels:
        if cursor < a['effective_start']:
            backgrounds.append(dict(filename=f['filename'],channel_index=0,rate=f['sample_rate_hz'],
                start_sample=cursor,end_sample=a['effective_start'],group_id=f['group_id'],split=file_splits[f['filename']],
                binary_label=None,status='unreviewed_complement_may_contain_neighbour_whistles_or_boundary_sound'))
        cursor = max(cursor,a['effective_end'])
    if cursor < f['sample_frames']:
        backgrounds.append(dict(filename=f['filename'],channel_index=0,rate=f['sample_rate_hz'],
            start_sample=cursor,end_sample=f['sample_frames'],group_id=f['group_id'],split=file_splits[f['filename']],
            binary_label=None,status='unreviewed_complement_may_contain_neighbour_whistles_or_boundary_sound'))
    for r in listening:
        if r['source_file'] == f['filename'] and r['content_type'].startswith('no_publisher'):
            assert r['user_report']['whistle_count'] == 0 and sha(Path(r['path'])) == r['sha256']
            assert not any(r['original_clip_start_sample'] < a['effective_end'] and a['effective_start'] < r['original_clip_end_sample'] for a in labels)
            negatives.append(dict(filename=f['filename'],original_audio_path=f['path'],original_audio_sha256=f['sha256'],
                channel_index=0,rate=f['sample_rate_hz'],start_sample=r['original_clip_start_sample'],end_sample=r['original_clip_end_sample'],
                group_id=f['group_id'],split=file_splits[f['filename']],binary_candidate_label=0,
                status='user_heard_no_whistle_context_clip; noise_subtype_not_confirmed',
                listening_reference_id=r['id'],automatic_training_use=False))
assert len(positives) == 51 and len(negatives) == 3
invalid = [a for a in annotations if not a['valid']]
assert len(invalid) == 1 and invalid[0]['channel_index'] == 1
queue = [p for p in positives if p['status'] != 'representative_single_blow_heard_boundary_not_reannotated']
queue.sort(key=lambda p:({'development':0,'validation':1,'test':2}[p['split']],p['filename'],p['start_sample']))
write(out/'positive-group-candidates.json',positives)
write(out/'background-unreviewed-ranges.json',backgrounds)
write(out/'heard-negative-context-candidates.json',negatives)
write(out/'review-queue.json',queue)
write(out/'quarantined-original-annotations.json',invalid)
with (out/'positive-group-candidates.csv').open('w',newline='',encoding='utf-8-sig') as stream:
    keys=['candidate_id','filename','channel_index','rate','start_sample','end_sample','duration_seconds','group_id','split','status','user_heard_count']
    writer = csv.DictWriter(stream,fieldnames=keys,extrasaction='ignore')
    writer.writeheader();writer.writerows(positives)
summary = dict(publisher_group_count=len(positives),publisher_group_duration_seconds=sum(p['duration_seconds'] for p in positives),
    single_blow_representatives_heard=sum(p['status']=='representative_single_blow_heard_boundary_not_reannotated' for p in positives),
    multi_blow_groups=sum(p['status']=='multiple_blows_needs_single_blow_boundaries' for p in positives),
    truncated_groups=sum(p['status']=='truncated_blow_separate_edge_case' for p in positives),
    unlistened_groups=sum(p['status']=='publisher_group_needs_listening' for p in positives),
    review_queue_count=len(queue),known_negative_contexts=len(negatives),
    known_negative_context_seconds=sum((p['end_sample']-p['start_sample'])/p['rate'] for p in negatives),
    unreviewed_background_range_count=len(backgrounds),
    unreviewed_background_seconds=sum((p['end_sample']-p['start_sample'])/p['rate'] for p in backgrounds),
    per_split={name:dict(group_candidates=sum(p['split']==name for p in positives),
        files=len(s['files']),groups=len(s['groups'])) for name,s in splits.items()},
    original_single_blow_total_unknown=True,automatic_training_labels_created=False,
    exact_training_readiness_pending=True,test_previously_used_for_diagnostics_not_blind=True)
assert all(sha(Path(p)) == h for p,h in protected.items())
assert abs(summary['publisher_group_duration_seconds']+summary['unreviewed_background_seconds']-
           sum(f['duration_seconds'] for f in manifest)) < 1e-8
write(out/'summary.json',summary)
write(out/'verification.json',dict(protected_sha256=protected,all_protected_hashes_unchanged=True,
    all_original_headers_match_manifest=True,all_positions_valid=True,group_splits_no_overlap=True,
    no_negative_overlap_positive=True,complement_duration_conserved=True,raw_right_anomaly_unmodified=True,
    model_not_run=True,training_not_started=True))
print(json.dumps(summary,ensure_ascii=False,indent=2))

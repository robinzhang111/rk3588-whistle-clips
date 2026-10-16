"""Independently check saved scores, event construction, mapping and scoring."""
import csv
from pathlib import Path
import numpy as np
from continuous_whistle import candidate_events,starts_for_samples
from evaluate_testbench_events import read,write,sha,match

STAGE=Path(__file__).absolute().parent.parent
out=STAGE/'outputs/basketball-inference-20261012-01'
details=read(out/'event-details.json');summary=read(out/'result.json')
manifest=read(STAGE/'outputs/basketball-input-adaptation-20261012-01/manifest.json')
truth=read(STAGE/'outputs/basketball-manual-review-20261012-02/manual_annotations_coverage_corrected.json')
checks=[];fn_analysis=[];rows=0
for f,r in zip(manifest['files'],details):
    assert f['id']==r['id']
    scores=np.load(str(out/(f['id']+'-scores.npy')),allow_pickle=False)
    assert scores.shape==(f['complete_windows'],f['channels']) and np.isfinite(scores).all()
    starts=starts_for_samples(f['output_frames'])
    events=read(out/(f['id']+'-events.json'))
    rebuilt=candidate_events(scores,starts,f['output_frames'],22050)
    assert rebuilt==[e['adapted_22050_event'] for e in events]
    for e in events:
        for key in ['estimated_start_sample','estimated_end_sample','decision_sample','closure_available_sample']:
            assert e[key]==min(f['input_frames'],2*e['adapted_22050_event'][key])
    with (out/(f['id']+'-windows.csv')).open(encoding='utf-8') as stream:
        table=list(csv.DictReader(stream))
    assert len(table)==scores.size
    for n,row in enumerate(table):
        i,c=divmod(n,f['channels']);assert int(row['window_index'])==i and int(row['channel_index'])==c
        assert np.float32(float(row['score']))==scores[i,c]
        assert int(row['above_0_65'])==int(float(scores[i,c])>=.65)
        assert int(row['original_start_sample'])==2*int(starts[i])
    labels=[e for e in truth['events'] if e['file_id']==f['id']]
    pairs=match(events,labels,8820)
    assert len(pairs)==r['TP'] and len(events)-len(pairs)==r['FP'] and len(labels)-len(pairs)==r['FN']
    assert {(events[i]['event_index'],labels[j]['id']) for i,j in pairs}=={(m['prediction']['event_index'],m['label']['id']) for m in r['matches']}
    for e in r['false_negatives']:
        selected=(starts*2<e['end_sample']) & ((starts+1024)*2>e['start_sample'])
        overlaps=[p for p in events if p['estimated_start_sample']<e['end_sample'] and p['estimated_end_sample']>e['start_sample']]
        fn_analysis.append(dict(file_id=f['id'],label=e,
            maximum_score_in_intersecting_windows=float(scores[selected].max()),
            positive_intersecting_windows=int((np.max(scores[selected].astype(float),axis=1)>=.65).sum()),
            overlapping_candidates=overlaps,
            scope='Objective score/interval evidence only; no new human listening'))
    checks.append(dict(file_id=f['id'],windows=len(starts),channels=f['channels'],csv_rows=len(table),
        score_event_mapping_scoring_checks=True))
    rows+=len(table)
    if f['id']=='B06':
        duplicated_channel_max_difference=float(np.max(np.abs(scores[:,0]-scores[:,1])))
        np.testing.assert_allclose(scores[:,0],scores[:,1],rtol=1e-5,atol=1e-6)
        np.testing.assert_array_equal(scores[:,0].astype(float)>=.65,scores[:,1].astype(float)>=.65)
tp,fp,fn=(sum(r[k] for r in details) for k in ['TP','FP','FN'])
assert (tp,fp,fn)==(summary['TP'],summary['FP'],summary['FN'])
assert tp+fn==7 and tp+fp==19
assert abs(summary['precision']-tp/(tp+fp))<1e-12
assert abs(summary['recall']-tp/7)<1e-12
assert all(sha(Path(p))==h for p,h in read(out/'integrity.json')['sha256'].items())
write(out/'FN-objective-analysis.json',fn_analysis)
write(out/'verification.json',dict(files=checks,checked_csv_rows=rows,
    all_scores_csv_roundtrip_equal_NPY=True,all_events_reconstructed_equal=True,
    original_sample_mapping_checked=True,one_to_one_matches_and_counts_recomputed=True,
    all_protected_inputs_unchanged=True,B06_duplicate_channels_numerically_consistent_and_same_decisions=True,
    B06_score_max_absolute_difference=duplicated_channel_max_difference,
    no_threshold_retuning=True,no_truth_edits=True,new_human_listening=False))
print([(r['file_id'],r['maximum_score_in_intersecting_windows'],r['positive_intersecting_windows'],len(r['overlapping_candidates'])) for r in fn_analysis])

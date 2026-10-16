"""Re-score saved predictions only against corrected human boundaries."""
import argparse
from pathlib import Path
import numpy as np
from evaluate_testbench_events import read,write,sha,match,stats

stage=Path(__file__).absolute().parent.parent
source=stage/'outputs/basketball-inference-20261012-01'
parser=argparse.ArgumentParser()
parser.add_argument('--annotations',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
args=parser.parse_args()
truth_path=args.annotations
out=args.output
out.mkdir(exist_ok=False)
truth=read(truth_path)
assert read(truth_path.parent/'validation.json')['coverage_complete']
assert sha(truth_path)==read(truth_path.parent/'user-final-confirmation.json')['annotation_sha256']
protected=read(source/'integrity.json')['sha256']
protected.update({str(p):sha(p) for p in source.iterdir() if p.is_file()})
protected[str(truth_path)]=sha(truth_path)
assert all(sha(Path(p))==h for p,h in protected.items())
write(out/'plan-before-scoring.json',dict(source_sha256=protected,tool_sha256=sha(Path(__file__)),
    threshold=.65,event_rule='unchanged v1, no prediction regeneration',
    scoring='same original44100Hz grid, inclusive0.2s8820,1:1 max count/min onset error',
    purpose='human-boundary revision diagnostic re-evaluation, not new blind test',model_loaded=False))
results=[];fn_windows=[]
for old in read(source/'event-details.json'):
    fid=old['id'];p=read(source/(fid+'-events.json'));g=[e for e in truth['events'] if e['file_id']==fid]
    pairs=match(p,g,8820);pi={i for i,j in pairs};gi={j for i,j in pairs}
    matched=[dict(prediction=p[i],label=g[j],onset_error_seconds=(p[i]['estimated_start_sample']-g[j]['start_sample'])/44100,
        offset_error_seconds=None if p[i]['closed_by']=='end_of_file' else (p[i]['estimated_end_sample']-g[j]['end_sample'])/44100,
        decision_delay_seconds=(p[i]['decision_sample']-g[j]['start_sample'])/44100) for i,j in pairs]
    fp=[dict(prediction=e,duplicate_within_onset_tolerance=any(abs(e['estimated_start_sample']-g[j]['start_sample'])<=8820 for j in gi)) for i,e in enumerate(p) if i not in pi]
    fn=[e for j,e in enumerate(g) if j not in gi]
    results.append(dict(id=fid,duration_seconds=old['duration_seconds'],manual_count=len(g),candidate_count=len(p),TP=len(pairs),FP=len(fp),FN=len(fn),matches=matched,false_positives=fp,false_negatives=fn))
    scores=np.load(str(source/(fid+'-scores.npy')),allow_pickle=False);starts=np.arange(len(scores))*1024
    for e in fn:
        mask=(starts<e['end_sample'])&(starts+2048>e['start_sample'])
        fn_windows.append(dict(file_id=fid,label=e,max_score=float(scores[mask].max()),
            positive_intersecting_windows=int((scores[mask].astype(float).max(axis=1)>=.65).sum()),
            overlapping_candidates=[x for x in p if x['estimated_start_sample']<e['end_sample'] and x['estimated_end_sample']>e['start_sample']]))
tp,fp,fn=(sum(r[k] for r in results) for k in ['TP','FP','FN'])
matched=[m for r in results for m in r['matches']]
result=dict(TP=tp,FP=fp,FN=fn,manual_count=7,candidate_count=sum(r['candidate_count'] for r in results),
    precision=tp/(tp+fp),recall=tp/7,F1=2*tp/(2*tp+fp+fn),
    duration_seconds=sum(r['duration_seconds'] for r in results),
    onset_error_seconds=stats([m['onset_error_seconds'] for m in matched]),
    offset_error_seconds=stats([m['offset_error_seconds'] for m in matched if m['offset_error_seconds'] is not None]),
    decision_delay_seconds=stats([m['decision_delay_seconds'] for m in matched]),
    duplicate_FP=sum(e['duplicate_within_onset_tolerance'] for r in results for e in r['false_positives']),
    scope='same saved scores/predictions, corrected human boundaries; no inference')
result['FP_per_hour']=fp*3600/result['duration_seconds']
assert tp+fn==7 and tp+fp==19
assert all(sha(Path(p))==h for p,h in protected.items())
write(out/'event-details.json',results);write(out/'result.json',result)
write(out/'FN-objective-analysis.json',fn_windows)
write(out/'verification.json',dict(all_protected_inputs_unchanged=True,source_sha256=protected,
    all_19_predictions_reused_without_change=True,no_model_load_or_forward=True,no_retuning=True))
print(result)

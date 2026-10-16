"""Evaluate frozen TestBench candidates against user-confirmed single blows."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import time

STAGE = Path(__file__).absolute().parent.parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               allow_nan=False)+'\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def match(predictions, labels, tolerance):
    """Chronological DP: maximize count, then minimize total onset error."""
    p = sorted(enumerate(predictions), key=lambda x: (x[1]['estimated_start_sample'], x[0]))
    g = sorted(enumerate(labels), key=lambda x: (x[1]['start_sample'], x[0]))
    dp = [[(0, 0, ()) for _ in range(len(g)+1)] for _ in range(len(p)+1)]
    key = lambda state: (-state[0], state[1], state[2])
    for i in range(1, len(p)+1):
        for j in range(1, len(g)+1):
            choices = [dp[i-1][j], dp[i][j-1]]
            error = abs(p[i-1][1]['estimated_start_sample']-g[j-1][1]['start_sample'])
            if error <= tolerance:
                count, cost, pairs = dp[i-1][j-1]
                choices.append((count+1, cost+error, pairs+((p[i-1][0], g[j-1][0]),)))
            dp[i][j] = min(choices, key=key)
    return list(dp[-1][-1][2])


def stats(values):
    if not values:
        return None
    ordered = sorted(values)
    n = len(values)
    return dict(count=n, mean=sum(values)/n,
                median=(ordered[(n-1)//2]+ordered[n//2])/2,
                min=min(values), max=max(values),
                mean_absolute=sum(abs(v) for v in values)/n)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--annotations', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--confirmed-sha256', required=True)
    parser.add_argument('--confirmation', required=True)
    parser.add_argument('--candidate-directory', type=Path, default=STAGE/'outputs/naodevils-continuous-20261011-01')
    parser.add_argument('--event-logic', choices=['v1', 'v2A'], default='v1')
    args = parser.parse_args()
    started = time.perf_counter()
    out = Path(args.output)
    out.mkdir(exist_ok=False)
    annotation_path = Path(args.annotations)
    validation = read(annotation_path.parent/'validation.json')
    assert validation['ready_for_user_final_confirmation']
    a = read(annotation_path)
    assert sha(annotation_path) == args.confirmed_sha256
    write(out/'user-final-confirmation.json', dict(
        user_reply=args.confirmation,
        annotation_sha256=sha(annotation_path), scope='single audible whistle blow; source identity unverified'))
    candidate_dir = args.candidate_directory
    source_plan = read(STAGE/'outputs/naodevils-continuous-20261011-01/plan-before-prediction.json')
    paths = [annotation_path] + [candidate_dir/(Path(f['filename']).stem+'-events.json') for f in a['source_files']]
    paths += [Path(p) for p in source_plan['source_hashes']]
    before = {str(p): sha(p) for p in paths}
    upstream = source_plan['source_hashes']
    assert all(before[p] == h for p, h in upstream.items())
    protocol = dict(version='testbench-single-blow-event-v1',
        frozen_utc=datetime.now(timezone.utc).isoformat(),
        status='frozen_before_event_scoring_after_candidates_and_manual_review',
        input_sha256=before, evaluator_sha256=sha(Path(__file__)),
        threshold=0.65, event_logic=args.event_logic,
        candidate_rule='four-channel OR, >=2 consecutive windows; '+('merge qualified candidates separated by exactly one negative window' if args.event_logic=='v2A' else 'no gap bridging'),
        event_unit='single audible whistle blow across channels, source identity unverified',
        interval='original sample frames [start,end)',
        matching='same file; chronological one-to-one; maximize count, minimize summed absolute onset error; ties lexicographic original prediction/label indices',
        onset_tolerance_seconds=0.2, boundary='inclusive <=4410 at 22050Hz',
        tolerance_basis='retain existing 0.2s project onset scoring tolerance; not selected from these scores; new single-blow protocol distinct from publisher-group protocol',
        unmatched='all unmatched predictions FP; all unmatched labels FN; no evaluator merging',
        fp_hour_denominator='sum full recording durations, channels not multiplied; includes unprocessed EOF tail',
        error_scope='matched events only; offsets are descriptive window estimates against approximate manual boundaries; EOF offsets excluded',
        latency='decision sample minus manual onset, audio position only; not wall time or UTC',
        independence='published recording re-evaluation; pretrained weight source unknown, manually reviewed with candidate access available; not blind test',
        acceptance_target='not established; scores do not establish engineering acceptance')
    write(out/'protocol-before-scoring.json', protocol)
    results = []
    for f in a['source_files']:
        filename, rate = f['filename'], f['sample_rate']
        assert rate == 22050
        p = read(candidate_dir/(Path(filename).stem+'-events.json'))
        g = [e for e in a['events'] if e['filename'] == filename]
        pairs = match(p, g, 4410)
        pi, gi = {i for i,j in pairs}, {j for i,j in pairs}
        matches = []
        for i,j in pairs:
            matches.append(dict(prediction=p[i], label=g[j],
                onset_error_seconds=(p[i]['estimated_start_sample']-g[j]['start_sample'])/rate,
                offset_error_seconds=None if p[i]['closed_by']=='end_of_file' else (p[i]['estimated_end_sample']-g[j]['end_sample'])/rate,
                decision_delay_seconds=(p[i]['decision_sample']-g[j]['start_sample'])/rate))
        fp = [dict(prediction=e, duplicate_within_onset_tolerance=any(abs(e['estimated_start_sample']-g[j]['start_sample'])<=4410 for j in gi)) for i,e in enumerate(p) if i not in pi]
        fn = [e for j,e in enumerate(g) if j not in gi]
        r = dict(filename=filename, duration_seconds=f['sample_frames']/rate,
                 manual_count=len(g), candidate_count=len(p), TP=len(pairs), FP=len(fp), FN=len(fn),
                 matches=matches, false_positives=fp, false_negatives=fn)
        results.append(r)
    tp, fp, fn = (sum(r[k] for r in results) for k in ('TP','FP','FN'))
    duration = sum(r['duration_seconds'] for r in results)
    matched = [m for r in results for m in r['matches']]
    summary = dict(TP=tp, FP=fp, FN=fn, precision=tp/(tp+fp) if tp+fp else None,
        recall=tp/(tp+fn) if tp+fn else None, F1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
        duration_seconds=duration, FP_per_hour=fp*3600/duration,
        duplicate_FP=sum(e['duplicate_within_onset_tolerance'] for r in results for e in r['false_positives']),
        onset_error_seconds=stats([m['onset_error_seconds'] for m in matched]),
        offset_error_seconds=stats([m['offset_error_seconds'] for m in matched if m['offset_error_seconds'] is not None]),
        decision_delay_seconds=stats([m['decision_delay_seconds'] for m in matched]),
        files=[{k:r[k] for k in ('filename','duration_seconds','manual_count','candidate_count','TP','FP','FN')} for r in results],
        evaluation_wall_seconds=time.perf_counter()-started,
        timing_scope='evaluation including hashing/reading, excluding inference and subsequent report writing')
    assert tp+fp == sum(r['candidate_count'] for r in results)
    assert tp+fn == len(a['events'])
    assert all(sha(Path(p))==h for p,h in before.items())
    write(out/'event-details.json', results)
    write(out/'result.json', summary)
    write(out/'integrity.json', dict(all_inputs_unchanged=True, sha256=before))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

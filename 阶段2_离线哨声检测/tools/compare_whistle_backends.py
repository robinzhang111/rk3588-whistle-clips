"""Report file/shared-feature CPU-NPU differences without adjusting thresholds."""
import argparse
import json
from pathlib import Path

import numpy as np
import detect_file as d


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu', type=Path, required=True)
    parser.add_argument('--npu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    cpu, npu = read(args.cpu/'benchmark.json'), read(args.npu/'benchmark.json')
    assert cpu['status'] == npu['status'] == 'completed'
    for key in ['source_sha256','adapted_frames','window_count','sample_rate','channels','shared_feature_shape']:
        assert cpu[key] == npu[key], key
    starts = d.c.starts_for_samples(cpu['adapted_frames'])
    position = lambda es: [(e['first_window'],e['last_window'],e['estimated_start_sample'],
                           e['estimated_end_sample'],e['closed_by'],e['active_channels']) for e in es]
    comparisons = {}
    for scope in ['file','shared']:
        left = np.load(str(args.cpu/(scope+'-scores.npy')),allow_pickle=False)
        right = np.load(str(args.npu/(scope+'-scores.npy')),allow_pickle=False)
        assert left.shape == right.shape and np.isfinite(right).all()
        old = d.c.candidate_events(left,starts,cpu['adapted_frames'],22050)
        new = d.c.candidate_events(right,starts,cpu['adapted_frames'],22050)
        changed = np.flatnonzero(np.any((left.astype(float)>=.65)!=(right.astype(float)>=.65),axis=1))
        comparisons[scope] = dict(max_abs_score_difference=float(np.max(np.abs(left-right))),
            mean_abs_score_difference=float(np.mean(np.abs(left-right))),
            changed_threshold_windows=changed.tolist(), cpu_candidates=len(old),npu_candidates=len(new),
            event_positions_equal=position(old)==position(new),
            cpu_score_range=[float(left.min()),float(left.max())],
            npu_score_range=[float(right.min()),float(right.max())])
    report = dict(status='same_file_behavior_matches' if all(not v['changed_threshold_windows'] and
        v['event_positions_equal'] for v in comparisons.values()) else 'same_file_behavior_differs',
        comparisons=comparisons, cpu=cpu,npu=npu, threshold=.65,rule='v1',
        no_quality_or_online_latency_claim=True,
        single_window_api_median_ratio_cpu_over_npu=cpu['shared_repeat_median_seconds']/npu['shared_repeat_median_seconds'],
        timing_scope='three warm repeats of one short mono file; API includes transfer/host overhead; no hardware-only time')
    d.write(args.output,report)
    print(json.dumps(dict(status=report['status'],comparisons=comparisons),indent=2))


if __name__ == '__main__':
    main()

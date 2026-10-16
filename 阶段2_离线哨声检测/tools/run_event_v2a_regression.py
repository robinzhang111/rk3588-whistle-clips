"""Reuse existing scores, verify v1, save v2A in a new diagnostic session."""
from pathlib import Path
from datetime import datetime, timezone
import time
import numpy as np
from continuous_whistle import candidate_events, starts_for_samples
from event_logic_v2a import merge_qualified_candidates
from evaluate_testbench_events import read, write, sha

STAGE = Path(__file__).absolute().parent.parent


def main():
    out = STAGE/'outputs/testbench-v2a-regression-20261011-01'
    out.mkdir(exist_ok=False)
    continuous = STAGE/'outputs/naodevils-continuous-20261011-01'
    truth = STAGE/'outputs/testbench-manual-review-20261011-04/manual_annotations_corrected.json'
    protected = read(STAGE/'outputs/event-v2-plan-20261011-01/verification.json')['sha256']
    assert all(sha(Path(p)) == h for p,h in protected.items())
    sources = read(truth)['source_files']
    tools = [Path(__file__), STAGE/'tools/event_logic_v2a.py', STAGE/'tools/continuous_whistle.py', STAGE/'tools/evaluate_testbench_events.py']
    write(out/'plan-before-merge.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),
        rule='v1-qualified candidates only; merge exact one-negative-window gaps; no singleton admission',
        threshold=.65, no_model_inference=True, diagnostic_reuse_not_independent=True,
        protected_input_sha256=protected, tool_sha256={str(p):sha(p) for p in tools}))
    records=[]
    for source in sources:
        start=time.perf_counter()
        stem=Path(source['filename']).stem
        scores=np.load(str(continuous/(stem+'-scores.npy')),allow_pickle=False)
        starts=starts_for_samples(source['sample_frames'])
        original=read(continuous/(stem+'-events.json'))
        assert candidate_events(scores,starts,source['sample_frames'],source['sample_rate'])==original
        merged=merge_qualified_candidates(original,source['sample_frames'])
        assert [c for e in merged for c in e['components']]==original
        write(out/(stem+'-events.json'),merged)
        records.append(dict(filename=source['filename'],v1_count=len(original),v2A_count=len(merged),
            merged_groups=[dict(new_event_index=e['event_index'],components=e['component_event_indices'],
                                bridged_negative_windows=e['bridged_negative_windows']) for e in merged if len(e['components'])>1],
            reused_score_rule_processing_seconds=time.perf_counter()-start))
    assert all(sha(Path(p))==h for p,h in protected.items())
    write(out/'regression-generation.json',dict(files=records,all_v1_reconstructed_identically=True,
        all_components_accounted_once=True,all_protected_inputs_unchanged=True,
        timing_scope='NPY read, v1 reconstruction, offline v2A merge and JSON save; no inference',
        closure_scope='whole-recording offline merge only; online closure state machine not implemented'))
    print(records)


if __name__ == '__main__':
    main()

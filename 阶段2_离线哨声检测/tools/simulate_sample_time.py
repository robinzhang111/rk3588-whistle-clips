"""Map existing synthetic A candidates using synthetic C anchors only."""
import argparse
import hashlib
import json
from pathlib import Path
from sample_time import SamplingAnchor, SegmentTime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    original = args.input.read_bytes()
    args.output.mkdir(parents=True, exist_ok=False)
    count = 0
    with (args.output/'time-diagnostics.jsonl').open('x', encoding='utf-8') as output:
        for line in original.decode('utf-8').splitlines():
            event = json.loads(line)
            segment = event['segment_id']
            anchor = SamplingAnchor(segment, 0, 48000, 0, '2000-01-01T00:00:00Z',
                                    'synthetic fixture; unrelated to actual recording UTC', 0)
            mapping = SegmentTime(segment, anchor, original_at_target_zero=0,
                                  relation_evidence='synthetic 48000/22050 ratio, zero offset')
            mapped = mapping.candidate_time(event)
            assert mapped['estimated_utc'] is None and mapped['synthetic_utc'] is not None
            output.write(json.dumps(dict(case='synthetic_anchor', timing=mapped))+'\n')
            mapping.invalidate('simulated_input_gap')
            rejected = mapping.candidate_time(event)
            assert rejected['status'] == 'relative_only'
            output.write(json.dumps(dict(case='invalid_after_gap', timing=rejected))+'\n')
            count += 1
    assert args.input.read_bytes() == original
    report = dict(status='passed', candidates=count, diagnostics=count*2,
                  input_sha256=hashlib.sha256(original).hexdigest(), input_unchanged=True,
                  evidence_scope='local synthetic time conversion only',
                  real_sampling_anchor_verified=False, formal_utc_upload=False,
                  original_candidates_modified=False)
    with (args.output/'summary.json').open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
    print(json.dumps(report))


if __name__ == '__main__':
    main()

"""Local synthetic status replay; never loads ALSA or opens a device."""
import argparse
import json
from pathlib import Path

from sample_time import SamplingAnchor, SegmentTime
from sampling_probe import SamplingProbe


def synthetic_snapshot(**changes):
    snapshot = dict(state=3, avail=256, delay=256, htstamp_ns=10**9,
        audio_htstamp_ns=0, driver_htstamp_ns=10**9,
        audio_report=dict(valid=False, actual_type=1, accuracy_report=False, accuracy_ns=None))
    snapshot.update(changes)
    return snapshot


def synthetic_probe(segment):
    anchor = SamplingAnchor(segment, 0, 48000, 0, '2000-01-01T00:00:00Z',
                            'synthetic fixture only; no capture evidence', 0)
    mapping = SegmentTime(segment, anchor, original_at_target_zero=0,
                          relation_evidence='synthetic ratio; no real resampler evidence')
    return SamplingProbe(segment, mapping=mapping, tolerance_ns=1000)


def observe(probe, sample=48000, mono=10**9, wall='2000-01-01T00:00:01Z', **changes):
    return probe.observe(sample, synthetic_snapshot(**changes), mono, wall,
        clock_evidence='synthetic paired clocks; not NTP or ADC evidence',
        timestamp_clock='monotonic', timestamp_mode='enabled')


def simulate():
    records = []
    unknown = SamplingProbe('unknown')
    records.append(observe(unknown))
    assert unknown.mapping.candidate_time(dict(segment_id='unknown', estimated_start_sample=0))['status'] == 'relative_only'
    for case in ['continuous', 'xrun', 'suspend', 'disconnect', 'gap', 'restart',
                 'sample_regression', 'monotonic_regression', 'wall_jump', 'type_fallback']:
        probe = synthetic_probe(case)
        records.append(observe(probe))
        if case in ['xrun', 'suspend', 'disconnect']:
            records.append(observe(probe, state={'xrun': 4, 'suspend': 7, 'disconnect': 8}[case]))
        elif case in ['gap', 'restart']:
            records.append(probe.interrupt(case))
        elif case == 'sample_regression':
            records.append(observe(probe, sample=0))
        elif case == 'monotonic_regression':
            records.append(observe(probe, mono=10**9-1))
        elif case == 'wall_jump':
            records.append(observe(probe, mono=2*10**9, wall='2000-01-01T00:00:04Z'))
        elif case == 'type_fallback':
            records.append(observe(probe, audio_report=dict(valid=True, actual_type=1,
                accuracy_report=False, accuracy_ns=None)))
        else:
            records.append(observe(probe, sample=96000, mono=2*10**9, wall='2000-01-01T00:00:02Z'))
        timing = probe.mapping.candidate_time(dict(segment_id=case, estimated_start_sample=22050))
        invalid = case not in ['continuous', 'type_fallback']
        assert (timing['status'] == 'relative_only') == invalid
        assert timing['estimated_utc'] is None and not timing['formal_utc_upload']
        records.append(dict(kind='mapping_check', case=case, timing=timing))
    # A new segment starts without the old anchor; recovery is never implicit.
    fresh = SamplingProbe('after_restart')
    records.append(observe(fresh, sample=0))
    assert fresh.mapping.anchor is None
    assert all(not row.get('formal_utc_upload', False) for row in records)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = simulate()
    with (args.output/'probe-diagnostics.jsonl').open('x', encoding='utf-8') as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + '\n')
    summary = dict(status='passed', diagnostic_records=len(records), scenarios=12,
        scope='local synthetic status replay only', alsa_library_loaded=False,
        device_connected=False, real_sampling_verified=False, formal_utc_upload=False)
    with (args.output/'summary.json').open('x', encoding='utf-8') as output:
        json.dump(summary, output, indent=2)
    print(json.dumps(summary))


if __name__ == '__main__':
    main()

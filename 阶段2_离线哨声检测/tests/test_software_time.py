import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parent.parent/'tools'))
from software_time import SoftwareTime
from simulate_sampling_probe import synthetic_snapshot


def observed(clock, sample=48000, mono=2*10**9, wall=946684801000000000, **changes):
    snapshot = synthetic_snapshot(htstamp_ns=mono, delay=480, avail=480)
    snapshot.update(changes)
    clock.observe(sample, snapshot, mono-1000, wall, mono, mono+1000)


class SoftwareTimeTests(unittest.TestCase):
    def test_software_estimate_delay_once_onset_not_decision(self):
        clock = SoftwareTime('s', 'fixed synthetic resampler fixture')
        observed(clock)
        result = clock.candidate_time(dict(segment_id='s', estimated_start_sample=22050,
                                          decision_sample=999999))
        self.assertEqual(result['status'], 'software_estimate')
        self.assertEqual(result['estimated_utc'], '2000-01-01T00:00:00.990000Z')
        self.assertFalse(result['sampling_utc_verified'])
        self.assertFalse(result['formal_utc_upload'])
        self.assertIsNone(result['total_uncertainty_ns'])

    def test_no_observation_keeps_relative(self):
        clock = SoftwareTime('s', 'fixture')
        self.assertEqual(clock.candidate_time(dict(segment_id='s', estimated_start_sample=0))['status'], 'relative_only')

    def test_input_gap_clock_jump_and_counter_regression(self):
        for changes in [dict(state=4), dict(delay=-1), dict(sample=1, mono=3*10**9),
                        dict(mono=10**9), dict(wall=946684805000000000, mono=3*10**9)]:
            clock = SoftwareTime('s', 'fixture')
            observed(clock)
            observed(clock, **changes)
            self.assertIsNotNone(clock.invalid_reason)
            self.assertEqual(clock.candidate_time(dict(segment_id='s', estimated_start_sample=0))['status'], 'relative_only')

    def test_continuous_observation_and_explicit_gap(self):
        clock = SoftwareTime('s', 'fixture')
        observed(clock)
        observed(clock, sample=96000, mono=3*10**9, wall=946684802000000000)
        self.assertIsNone(clock.invalid_reason)
        clock.invalidate('gap')
        observed(clock)
        self.assertEqual(clock.invalid_reason, 'gap')

    def test_wrong_segment_and_stale_status(self):
        clock = SoftwareTime('s', 'fixture')
        clock.observe(10, synthetic_snapshot(htstamp_ns=1), 10**10, 946684801000000000,
                      10**10+1, 10**10+2)
        self.assertEqual(clock.invalid_reason, 'stale_or_invalid_status_timestamp')
        with self.assertRaises(ValueError):
            clock.candidate_time(dict(segment_id='another', estimated_start_sample=0))

    def test_transport_rejects_software_time(self):
        sys.path.insert(0, str(Path(__file__).absolute().parents[2]/'阶段3_实时传输'))
        from auto_events import candidate_event
        clock = SoftwareTime('s', 'fixture')
        observed(clock)
        candidate = dict(segment_id='s', estimated_start_sample=0)
        with self.assertRaises(ValueError):
            candidate_event(candidate, clock.candidate_time(candidate), 'session', 1)


if __name__ == '__main__':
    unittest.main()

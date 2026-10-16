import sys
import unittest
from pathlib import Path
from fractions import Fraction

sys.path.insert(0, str(Path(__file__).absolute().parent.parent/'tools'))
from sample_time import SamplingAnchor, SegmentTime


def anchor(segment='s', **changes):
    values = dict(segment_id=segment, original_sample=48000, sample_rate=48000,
                  monotonic_ns=10**9, utc_time='2026-01-01T00:00:01Z',
                  evidence='synthetic fixture, not capture', uncertainty_ns=1000)
    values.update(changes)
    return SamplingAnchor(**values)


def clock(segment='s', **changes):
    values = dict(anchor=anchor(segment), original_at_target_zero=0,
                  relation_evidence='synthetic exact rate relation')
    values.update(changes)
    return SegmentTime(segment, **values)


def candidate(sample=22050, segment='s'):
    return dict(segment_id=segment, estimated_start_sample=sample)


class SampleTimeTests(unittest.TestCase):
    def test_rate_conversion_and_onset_not_decision(self):
        event = dict(candidate(44100), decision_sample=999999)
        result = clock().candidate_time(event)
        self.assertEqual(result['synthetic_utc'], '2026-01-01T00:00:02Z')
        self.assertEqual(result['original_sample_numerator'], 96000)
        self.assertIsNone(result['estimated_utc'])
        self.assertFalse(result['formal_utc_upload'])
        self.assertNotIn('event_time_utc', result)

    def test_fractional_position_and_explicit_delay(self):
        result = clock(original_at_target_zero=Fraction(1, 2)).candidate_time(candidate(1))
        self.assertEqual(Fraction(result['original_sample_numerator'],
                                  result['original_sample_denominator']), Fraction(1, 2)+Fraction(320, 147))

    def test_missing_anchor_and_relation_stay_relative(self):
        for mapping, reason in [(SegmentTime('s'), 'unknown_sampling_anchor'),
                (clock(original_at_target_zero=None), 'unknown_resampling_relation'),
                (clock(relation_evidence=''), 'unknown_resampling_relation')]:
            result = mapping.candidate_time(candidate())
            self.assertEqual(result['reason'], reason)
            self.assertIsNone(result['synthetic_utc'])

    def test_gap_and_new_segment_require_new_anchor(self):
        previous = clock()
        saved = previous.candidate_time(candidate())
        previous.invalidate('input_gap')
        self.assertEqual(previous.candidate_time(candidate())['reason'], 'input_gap')
        self.assertEqual(saved['status'], 'synthetic')
        self.assertEqual(SegmentTime('new').candidate_time(candidate(segment='new'))['status'], 'relative_only')
        with self.assertRaises(ValueError):
            SegmentTime('new', anchor=anchor())
        with self.assertRaises(ValueError):
            previous.candidate_time(candidate(segment='new'))

    def test_clock_jump_and_backwards_monotonic_invalidate(self):
        for mono, wall in [(2*10**9, '2026-01-01T00:00:04Z'),
                           (0, '2026-01-01T00:00:00Z')]:
            mapping = clock()
            self.assertEqual(mapping.check_clock(mono, wall, 1000)['status'], 'invalid')
            self.assertEqual(mapping.candidate_time(candidate())['status'], 'relative_only')

    def test_clock_tolerance_and_drift_report(self):
        mapping = clock()
        report = mapping.check_clock(2*10**9, '2026-01-01T00:00:02.000001Z', 1000)
        self.assertEqual(report['offset_change_ns'], 1000)
        self.assertEqual(report['status'], 'within_tolerance')
        self.assertFalse(report['proves_sampling_continuity'])
        self.assertEqual(mapping.check_clock(3*10**9, '2026-01-01T00:00:03.000002Z', 1000)['status'], 'invalid')

    def test_invalid_anchor_and_sample_rejected(self):
        for changes in [dict(sample_rate=0), dict(original_sample=-1), dict(monotonic_ns=True),
                        dict(uncertainty_ns=-1), dict(evidence=''), dict(kind='arrival'),
                        dict(utc_time='2026-01-01T00:00:01')]:
            with self.assertRaises(ValueError):
                anchor(**changes)
        for value in [-1, True, 1.5]:
            with self.assertRaises(ValueError):
                clock().candidate_time(candidate(value))

    def test_externally_verified_anchor_still_not_transport(self):
        result = clock(anchor=anchor(kind='verified_sampling', evidence='external fixture only')).candidate_time(candidate())
        self.assertEqual(result['estimated_utc'], '2026-01-01T00:00:01Z')
        self.assertIsNone(result['synthetic_utc'])
        self.assertFalse(result['formal_utc_upload'])


if __name__ == '__main__':
    unittest.main()

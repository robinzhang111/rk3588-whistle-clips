import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("protocol", Path(__file__).resolve().parents[1]/"tools/step2_protocol.py")
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)


def event(start, end, **extra):
    return dict(start_sample=start, end_sample=end, boundary_kind="human_natural_approximate", **extra)


def score(p, g, **kwargs):
    options = dict(rate=1000, frames=4000, coverage=[(0, 4000)], tolerance_samples=200)
    options.update(kwargs)
    return protocol.score_segment(p, g, **options)


class ProtocolBoundaries(unittest.TestCase):
    def test_maximum_matching_beats_nearest_greedy(self):
        # First prediction can hit either target; second can hit only the latter.
        result = score([event(1100, 1150), event(1300, 1350)], [event(1000, 1050), event(1200, 1250)])
        self.assertEqual(result["matches"], [(0, 0), (1, 1)])

    def test_tolerance_inclusive_and_duplicate_tail(self):
        result = score([event(800, 850), event(1500, 1550), event(2500, 2600)], [event(1000, 1800)])
        self.assertEqual((result["TP"], result["FP"], result["duplicate_FP"]), (1, 2, 1))
        self.assertEqual(result["duplicate_rate"], 1/3)
        self.assertIsNone(result["FP_per_hour"])
        self.assertEqual(score([event(799, 850)], [event(1000, 1800)])["TP"], 0)

    def test_nearby_independent_blows_not_merged(self):
        result = score([event(1000, 1150), event(1150, 1250)], [event(1000, 1150), event(1150, 1250)])
        self.assertEqual((result["TP"], result["FP"], result["FN"]), (2, 0, 0))

    def test_long_prediction_cannot_hit_two_and_minimum_error_wins(self):
        result = score([event(1000, 1800)], [event(1000, 1100), event(1150, 1250)])
        self.assertEqual((result["TP"], result["FP"], result["FN"]), (1, 0, 1))
        result = score([event(900, 950), event(1020, 1100)], [event(1000, 1050)])
        self.assertEqual(result["matches"], [(1, 0)])
        self.assertEqual(result["TP"]+result["FP"], 2)

    def test_mask_closure_is_prediction_independent(self):
        labels = [event(700, 1200), event(2500, 2600)]
        one = score([], labels, exclusions=[(1000, 1100)])
        two = score([event(700, 800), event(2500, 2600)], labels, exclusions=[(1000, 1100)])
        self.assertEqual(one["effective_ranges"], two["effective_ranges"])
        self.assertEqual(two["excluded_label_indices"], [0])
        self.assertEqual(two["ignored_prediction_indices"], [0])
        self.assertEqual(two["TP"], 1)

    def test_no_matching_across_unreviewed_gap(self):
        result = score([event(900, 950)], [event(1100, 1150)], coverage=[(0, 950), (1050, 4000)], tolerance_samples=20)
        self.assertEqual((result["TP"], result["FP"], result["FN"]), (0, 1, 1))

    def test_crossing_prediction_retained_but_no_offset_accuracy(self):
        result = score([event(500, 2000)], [event(500, 600, offset_reliable=True)], exclusions=[(1200, 1300)])
        self.assertEqual(result["TP"], 1)
        self.assertEqual(result["boundary_crossing_prediction_indices"], [0])
        self.assertEqual(result["offset_errors_seconds"], [])

    def test_clipped_label_excluded_and_fph_denominator_once(self):
        result = score([event(0, 50), event(2000, 2100)], [event(0, 100, boundary_clipped=True)], fp_hour_eligible=True)
        self.assertEqual(result["excluded_label_indices"], [0])
        self.assertEqual(result["ignored_prediction_indices"], [0])
        self.assertEqual(result["FP_per_hour"], 3600/3.7)

    def test_zero_denominators_and_no_eligible_time(self):
        result = score([], [])
        self.assertTrue(all(result[k] is None for k in ["precision", "recall", "F1", "duplicate_rate", "FP_per_hour"]))
        result = score([event(0, 50)], [], coverage=[], fp_hour_eligible=True)
        self.assertEqual(result["effective_seconds"], 0)
        self.assertIsNone(result["FP_per_hour"])

    def test_points_and_invalid_samples_refused(self):
        with self.assertRaises(ValueError):
            score([], [dict(start_sample=10, end_sample=20, boundary_kind="algorithm_anchor")])
        for p in [event(-1, 5), event(10, 5000), event(1.5, 20)]:
            with self.assertRaises(ValueError):
                score([p], [])

    def test_ties_and_sorting_deterministic(self):
        predictions = [event(1100, 1120), event(900, 920)]
        labels = [event(1000, 1050)]
        self.assertEqual(score(predictions, labels)["matches"], score(predictions, labels)["matches"])
        self.assertEqual(score(predictions, labels)["matches"], [(0, 0)])


if __name__ == "__main__":
    unittest.main()

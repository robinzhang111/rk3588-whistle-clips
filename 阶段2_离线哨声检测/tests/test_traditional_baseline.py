import importlib.util
from pathlib import Path
import unittest
import itertools
import random

import numpy as np

spec = importlib.util.spec_from_file_location(
    "baseline", Path(__file__).parent.parent / "traditional_baseline.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


class BaselineTests(unittest.TestCase):
    def config(self, gap=.1):
        return dict(rms_threshold=.05, band_ratio_threshold=.6,
                    min_positive_seconds=.08, merge_gap_seconds=gap)

    def test_unsigned_pcm_center(self):
        np.testing.assert_array_equal(baseline.decode_u8(bytes([0, 128, 255])), [-1, 0, 127/128])

    def test_silence_and_out_of_band_tone_do_not_trigger(self):
        rate = 48000
        for signal in (np.zeros(rate), .3*np.sin(2*np.pi*440*np.arange(rate)/rate)):
            features = baseline.extract_features(signal, rate, (2000, 3500))
            self.assertEqual(baseline.detect_events(features, self.config(), len(signal)), [])

    def test_sine_onset_tracks_audio_position_at_both_native_rates(self):
        for rate in (44100, 48000):
            signal = np.zeros(rate)
            start, end = int(.2*rate), int(.6*rate)
            signal[start:end] = .3*np.sin(2*np.pi*2500*np.arange(end-start)/rate)
            events = baseline.detect_events(baseline.extract_features(signal, rate, (2000, 3500)),
                                            self.config(), len(signal))
            self.assertEqual(len(events), 1)
            self.assertLessEqual(abs(events[0]["estimated_start_sample"]-start), int(.02*rate))
            self.assertGreater(events[0]["decision_sample"], events[0]["estimated_start_sample"])

    def test_short_pulse_rejected(self):
        rate = 48000
        signal = np.zeros(rate)
        signal[9600:10560] = .4*np.sin(2*np.pi*2500*np.arange(960)/rate)
        self.assertEqual(baseline.detect_events(baseline.extract_features(signal, rate, (2000,3500)),
                                               self.config(), len(signal)), [])

    def test_merge_gap_controls_grouping_without_ground_truth(self):
        rate = 48000
        signal = np.zeros(rate)
        for start, end in ((9600, 19200), (31200, 40800)):
            signal[start:end] = .3*np.sin(2*np.pi*2500*np.arange(end-start)/rate)
        features = baseline.extract_features(signal, rate, (2000,3500))
        self.assertEqual(len(baseline.detect_events(features, self.config(.1), len(signal))), 2)
        self.assertEqual(len(baseline.detect_events(features, self.config(.4), len(signal))), 1)

    def test_matching_maximizes_count_before_nearest_error(self):
        result = baseline.score_events([{"estimated_start_sample":14}, {"estimated_start_sample":41}],
                                       [{"original_start":0}, {"original_start":25}], 100, 100)
        self.assertEqual((result["TP"],result["FP"],result["FN"]), (2,0,0))

    def test_duplicate_is_fp_and_stereo_is_not_duration_multiplier(self):
        result = baseline.score_events([{"estimated_start_sample":10}, {"estimated_start_sample":11}],
                                       [{"original_start":10}], 100, 100)
        self.assertEqual(result["duplicate_FP"],1)
        self.assertEqual(result["FP_per_hour"],3600)

    def test_equal_cost_ties_use_original_indices_not_time_order(self):
        result = baseline.score_events([{"estimated_start_sample":10}, {"estimated_start_sample":0}],
                                       [{"original_start":20}, {"original_start":25}], 1000, 1000)
        self.assertEqual([(m["prediction_index"], m["label_index"]) for m in result["matches"]],
                         [(0, 0), (1, 1)])

    def test_matching_agrees_with_exhaustive_small_cases(self):
        rng = random.Random(20261011)
        for _ in range(60):
            positions_p, positions_g = [rng.randrange(60) for _ in range(3)], [rng.randrange(60) for _ in range(3)]
            possibilities = []
            for choices in itertools.product(range(-1, 3), repeat=3):
                pairs = tuple((i,j) for i,j in enumerate(choices) if j >= 0)
                if len({j for i,j in pairs}) != len(pairs):
                    continue
                errors = [abs(positions_p[i]-positions_g[j]) for i,j in pairs]
                if all(error <= 20 for error in errors):
                    possibilities.append((-len(pairs), sum(errors), pairs))
            expected = min(possibilities)[2]
            result = baseline.score_events([{"estimated_start_sample":p} for p in positions_p],
                                           [{"original_start":g} for g in positions_g], 100, 100)
            self.assertEqual(tuple((m["prediction_index"],m["label_index"]) for m in result["matches"]), expected)

    def test_exact_tolerance_boundary_and_one_sample_outside(self):
        gt = [{"original_start":10000}]
        result = baseline.score_events([{"estimated_start_sample":18820}],gt,44100,44100)
        self.assertEqual(result["TP"],1)
        result = baseline.score_events([{"estimated_start_sample":18821}],gt,44100,44100)
        self.assertEqual((result["TP"],result["FP"],result["FN"]),(0,1,1))

    def test_undefined_precision_is_not_perfect_score(self):
        result = baseline.score_events([], [{"original_start":100}], 1000, 1000)
        self.assertIsNone(result["precision"])
        self.assertEqual(result["recall"],0)
        self.assertEqual(result["F1"],0)


if __name__ == "__main__":
    unittest.main()

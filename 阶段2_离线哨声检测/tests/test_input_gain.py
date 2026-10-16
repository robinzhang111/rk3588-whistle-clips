import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / 'tools'))
from continuous_input import run_segment
from simulate_continuous_input import DeterministicPredictor
import start_space


class Source:
    def __init__(self, audio):
        self.raw = np.asarray(audio, dtype='<f4').tobytes()
        self.closed = False
    def read(self, size, stop):
        raw, self.raw = self.raw[:size], self.raw[size:]
        return raw
    def close(self):
        self.closed = True


class GainTests(unittest.TestCase):
    def run_audio(self, audio, gain, chunk):
        source, predictor = Source(audio), DeterministicPredictor()
        features, events = [], []
        report = run_segment(source, predictor, events.append, 2, 'same', chunk,
                             observe=lambda i, f, s, quiet: features.append(f.copy()),
                             input_gain=gain)
        self.assertTrue(source.closed and predictor.closed)
        self.assertEqual(report['status'], 'closed')
        self.assertEqual(report['retained_after_finish'], 0)
        return report, np.array(features), events

    def test_gain_matches_prescaled_input_across_blocks(self):
        audio = np.random.RandomState(7).uniform(-.03, .03, (4096, 2)).astype(np.float32)
        expected, features, events = self.run_audio(audio * 16, 1, 512)
        for chunk in (127, 777, 4096):
            report, actual_features, actual_events = self.run_audio(audio, 16, chunk)
            np.testing.assert_array_equal(actual_features, features)
            self.assertEqual(actual_events, events)
            self.assertEqual(report['input_gain'], 16)
            self.assertAlmostEqual(report['peak_after_gain'], report['peak_before_gain'] * 16)
            self.assertEqual(report['gain_over_full_scale_samples'], 0)

    def test_over_full_scale_is_counted_without_clipping(self):
        audio = np.full((2048, 2), .125, dtype=np.float32)
        report, features, events = self.run_audio(audio, 16, 512)
        _, expected, _ = self.run_audio(audio * 16, 1, 777)
        np.testing.assert_array_equal(features, expected)
        self.assertEqual(report['peak_after_gain'], 2)
        self.assertEqual(report['gain_over_full_scale_samples'], 4096)
        self.assertFalse(report['gain_clipped'])

    def test_invalid_gain_rejected(self):
        for gain in (0, 2, True, float('nan')):
            with self.assertRaises(ValueError):
                run_segment(None, None, None, 2, 's', input_gain=gain)

    def test_launcher_passes_default16_and_explicit1(self):
        with tempfile.TemporaryDirectory() as temp:
            for args, gain in [([], 16), (['--input-gain', '1'], 1)]:
                with patch.object(sys, 'argv', ['start_space.py', '--simulate', '--session', 's',
                                              '--output', str(Path(temp)/'session')] + args), \
                        patch.object(start_space, 'keyboard'), \
                        patch.object(start_space, 'control', return_value=0) as controller:
                    self.assertEqual(start_space.main(), 0)
                    command = controller.call_args.args[0]
                    self.assertEqual(command[command.index('--input-gain')+1], str(gain))


if __name__ == '__main__':
    unittest.main()

import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "prepare_bhuman2017", Path(__file__).parent.parent / "tools" / "prepare_bhuman2017.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AnnotationContractTests(unittest.TestCase):
    def row(self, raw, channel=0, rate=48000, frames=41286000):
        return module.annotation_row("sample.wav", channel, 0, raw, rate, frames, "source-hash")

    def test_per_channel_position_not_interleaved_byte_offset(self):
        left = self.row({"start": 48000, "end": 96000})
        right = self.row({"start": 48000, "end": 96000}, channel=1)
        self.assertEqual(left["start_seconds"], 1)
        self.assertEqual(right["start_seconds"], 1)
        self.assertTrue(left["primary_evaluation"])
        self.assertFalse(right["primary_evaluation"])

    def test_half_open_endpoint_at_eof_is_valid(self):
        row = self.row({"start": 28075000, "end": 28116000}, frames=28116000)
        self.assertTrue(row["valid"])
        self.assertEqual(row["effective_end"], 28116000)
        self.assertEqual(row["interval_convention"], "[start,end)")

    def test_known_bad_right_label_is_not_repaired(self):
        raw = {"start": 37570000, "end": 376700000}
        row = self.row(raw, channel=1)
        self.assertFalse(row["valid"])
        self.assertEqual(row["raw_label"], raw)
        self.assertEqual(row["original_end"], 376700000)
        self.assertIsNone(row["end_seconds"])
        self.assertIsNone(row["effective_end"])
        self.assertFalse(row["correction_applied"])

    def test_native_rate_is_used(self):
        row = self.row({"start": 44100, "end": 88200}, rate=44100)
        self.assertEqual(row["start_seconds"], 1)
        self.assertEqual(row["end_seconds"], 2)

    def test_invalid_intervals_are_quarantined(self):
        for raw in ({"start": -1, "end": 10}, {"start": 10, "end": 10},
                    {"start": 20, "end": 10}, {"start": True, "end": 10}):
            with self.subTest(raw=raw):
                self.assertFalse(self.row(raw)["valid"])

    def test_zero_core_is_preserved_but_not_used(self):
        raw = {"start": 3065000, "end": 3110000, "coreStart": 0, "coreEnd": 0}
        row = self.row(raw)
        self.assertTrue(row["valid"])
        self.assertEqual(row["raw_label"], raw)
        self.assertIsNone(row["core_start_seconds"])

    def test_related_recordings_share_group_and_split(self):
        pairs = (("LUnitedvsUPenn20170728.wav", "LUnitedvsUPenn20170728Half2.wav"),
                 ("HTWK_Dutch_05_04_2017.wav", "HTWK_Dutch_05_04_2017-2.wav"),
                 ("WhistleTest_tuhhnao12.wav", "WhistleTest_tuhhnao16.wav"))
        for first, second in pairs:
            self.assertEqual(module.GROUPS[first], module.GROUPS[second])


if __name__ == "__main__":
    unittest.main()

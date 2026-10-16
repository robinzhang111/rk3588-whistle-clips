import unittest

from basketclip.timing import clip_window, event_position, make_alignment, utc_time


class TimingTests(unittest.TestCase):
    def test_offset_locates_independent_event(self):
        alignment = make_alignment("g", "2026-10-10T12:00:10Z", 5)
        event = {"session_id": "g", "event_time_utc": "2026-10-10T12:00:30Z"}
        self.assertEqual(event_position(event, alignment), 25)

    def test_two_points_correct_linear_drift(self):
        alignment = make_alignment("g", "2026-10-10T12:00:00Z", 0,
                                   "2026-10-10T12:10:00Z", 601)
        event = {"session_id": "g", "event_time_utc": "2026-10-10T12:05:00Z"}
        self.assertAlmostEqual(event_position(event, alignment), 300.5)

    def test_mixed_session_rejected(self):
        alignment = make_alignment("g", "2026-10-10T12:00:00Z", 0)
        with self.assertRaises(ValueError):
            event_position({"session_id": "other", "event_time_utc": "2026-10-10T12:00:01Z"}, alignment)

    def test_timezone_must_be_explicit_utc(self):
        for value in ("2026-10-10T12:00:00", "2026-10-10T20:00:00+08:00"):
            with self.assertRaises(ValueError):
                utc_time(value)

    def test_invalid_sync_points(self):
        for kwargs in ({"end_event_utc": "2026-10-10T12:01:00Z"},
                       {"end_event_utc": "2026-10-10T12:01:00Z", "end_video_seconds": 0},
                       {"end_event_utc": "2026-10-10T11:59:00Z", "end_video_seconds": 10}):
            with self.assertRaises(ValueError):
                make_alignment("g", "2026-10-10T12:00:00Z", 0, **kwargs)

    def test_clamp_edges_and_skip_outside_events(self):
        self.assertEqual(clip_window(2, 8, 5, 100), (0, 7))
        self.assertEqual(clip_window(98, 8, 5, 100), (90, 100))
        for position in (-1, 100, 120):
            self.assertIsNone(clip_window(position, 8, 5, 100))
        self.assertIsNone(clip_window(0, 8, 0, 100))

    def test_invalid_window_values(self):
        for args in ((2, -1, 5, 100), (2, 0, 0, 100), (2, 8, 5, 0),
                     (float("nan"), 8, 5, 100), (2, 8, float("inf"), 100)):
            with self.assertRaises(ValueError):
                clip_window(*args)

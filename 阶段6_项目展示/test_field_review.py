import unittest
from make_field_review import plan


class FieldReviewTests(unittest.TestCase):
    def test_software_estimate_position_and_review_marker(self):
        row={'timing':{'status':'software_estimate','estimated_utc':'2026-10-15T08:24:54.639784Z'}}
        result=plan(row,'2026-10-15T08:24:43Z',1055.60455)
        self.assertAlmostEqual(result['provisional_video_seconds'],11.639784)
        self.assertAlmostEqual(result['start_seconds'],6.639784)
        self.assertAlmostEqual(result['end_seconds'],14.639784)
        self.assertAlmostEqual(result['provisional_marker_seconds'],5)

    def test_unknown_time_not_silently_mapped(self):
        with self.assertRaises(ValueError):
            plan({'timing':{'status':'unknown'}},'2026-10-15T08:24:43Z',10)

    def test_position_outside_video_rejected(self):
        row={'timing':{'status':'software_estimate','estimated_utc':'2026-10-15T08:24:54Z'}}
        with self.assertRaises(ValueError):
            plan(row,'2026-10-15T08:24:43Z',10)

import copy
import unittest
from basketclip.start_only import candidate_window


class StartOnlyTests(unittest.TestCase):
    def setUp(self):
        self.alignment=dict(kind='user_selected_start_only',session_id='s',segment_id='s:1',
                            sample_rate=22050,video_seconds_at_sample_zero=.447766)
        self.row=dict(session_id='s',candidate=dict(segment_id='s:1',estimated_start_sample=246784,
                      closed_by='negative_window'),timing=dict(status='relative_only',estimated_utc=None))

    def test_relative_candidate_without_utc_uses_only_sample_and_start(self):
        job=candidate_window(self.row,self.alignment,1055.6)
        self.assertAlmostEqual(job['video_seconds'],11.63978414058957)
        self.assertEqual(job['marker_seconds'],5)
        self.assertFalse(job['absolute_utc_used'])
        altered=copy.deepcopy(self.row)
        altered['timing']['estimated_utc']='2099-01-01T00:00:00Z'
        self.assertEqual(candidate_window(altered,self.alignment,1055.6),job)

    def test_wrong_segment_and_input_gap_rejected(self):
        for change in (dict(segment_id='different'),dict(closed_by='source_or_processing_error')):
            row=copy.deepcopy(self.row);row['candidate'].update(change)
            with self.assertRaises(ValueError):candidate_window(row,self.alignment,1055.6)

    def test_invalid_sample_offset_or_outside_recording_rejected(self):
        row=copy.deepcopy(self.row);row['candidate']['estimated_start_sample']=True
        with self.assertRaises(ValueError):candidate_window(row,self.alignment,1055.6)
        with self.assertRaises(ValueError):candidate_window(self.row,self.alignment,1)
        self.alignment['video_seconds_at_sample_zero']=-1
        with self.assertRaises(ValueError):candidate_window(self.row,self.alignment,1055.6)

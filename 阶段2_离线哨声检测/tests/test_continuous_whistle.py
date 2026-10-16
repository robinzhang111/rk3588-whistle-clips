import sys
from pathlib import Path
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).parent.parent/'tools'))
import continuous_whistle as c


class ContinuousTests(unittest.TestCase):
    def test_complete_windows_only(self):
        self.assertEqual(c.starts_for_samples(1023).tolist(),[])
        self.assertEqual(c.starts_for_samples(1024).tolist(),[0])
        self.assertEqual(c.starts_for_samples(1600).tolist(),[0,512])

    def test_silence_finite_and_marked(self):
        features,silent,zeros=c.features_for_starts(np.zeros((1536,2)),np.array([0,512]))
        self.assertTrue(np.isfinite(features).all())
        self.assertTrue(silent.all())
        self.assertEqual(zeros,2*2*513)

    def test_upstream_feature_formula_on_nonzero_signal(self):
        audio=np.random.RandomState(1).normal(size=(2048,4)).astype(np.float32)
        starts=c.starts_for_samples(len(audio))
        actual,_,_=c.features_for_starts(audio,starts)
        frames=np.lib.stride_tricks.sliding_window_view(audio,1024,axis=0)[::512]*np.hamming(1024)
        expected=(20*np.log10(np.abs(np.fft.fft(frames)[:,:,:513]))).astype(np.float32)
        np.testing.assert_array_equal(actual[...,0],expected)

    def test_short_response_rejected_and_gap_not_bridged(self):
        scores=np.array([[.8],[.1],[.9],[.9],[.1],[.9],[.9]])
        events=c.candidate_events(scores,np.arange(7)*512,4096,22050)
        self.assertEqual([(e['first_window'],e['last_window']) for e in events],[(2,3),(5,6)])
        self.assertEqual(events[-1]['closed_by'],'end_of_file')
        self.assertEqual(events[-1]['estimated_end_sample'],4096)

    def test_multichannel_or_and_decision_not_onset(self):
        scores=np.array([[.9,.1],[.1,.9],[.1,.1]])
        event=c.candidate_events(scores,np.arange(3)*512,2100,22050)[0]
        self.assertEqual(event['active_channels'],[0,1])
        self.assertEqual(event['estimated_start_sample'],0)
        self.assertEqual(event['decision_sample'],1536)
        self.assertEqual(event['closure_available_sample'],2048)
        self.assertTrue(event['onset_clipped_at_file_start'])

    def test_decimal_threshold_and_float32_value_below_it(self):
        starts=np.array([0,512])
        self.assertEqual(len(c.candidate_events(np.array([[.65],[.65]]),starts,1536,22050)),1)
        self.assertEqual(c.candidate_events(np.array([[.65],[.65]],dtype=np.float32),starts,1536,22050),[])

    def test_eof_tail_is_not_invented_as_positive_audio(self):
        event=c.candidate_events(np.array([[.8],[.8]]),np.array([0,512]),1600,22050)[0]
        self.assertEqual(event['estimated_end_sample'],1536)
        self.assertEqual(event['closure_available_sample'],1600)
        self.assertFalse(event['natural_offset_verified'])


if __name__=='__main__':
    unittest.main()

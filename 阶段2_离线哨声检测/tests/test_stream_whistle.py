import sys
from pathlib import Path
import unittest
import weakref

import numpy as np

sys.path.insert(0,str(Path(__file__).absolute().parent.parent/'tools'))
import continuous_whistle as c
from stream_whistle import ChunkDetector


class ChunkTests(unittest.TestCase):
    def exercise(self,scores,chunk_frames,tail=37):
        scores = np.asarray(scores,dtype=np.float32)
        if scores.ndim==1:
            scores = scores[:,None]
        count = c.WINDOW+(len(scores)-1)*c.HOP+tail
        audio = np.random.default_rng(18).normal(size=(count,scores.shape[1])).astype(np.float32)
        features,_,_ = c.features_for_starts(audio,c.starts_for_samples(count))
        calls = 0
        def predict(feature):
            nonlocal calls
            window,channel = divmod(calls,scores.shape[1])
            np.testing.assert_array_equal(feature,features[window,channel])
            calls += 1
            return scores[window,channel]
        actual=[]
        detector = ChunkDetector(scores.shape[1],predict,actual.append)
        for first in range(0,len(audio),chunk_frames):
            detector.feed(audio[first:first+chunk_frames])
            self.assertLess(detector.fill,c.WINDOW)
        report=detector.finish()
        expected=c.candidate_events(scores,c.starts_for_samples(count),count,22050)
        self.assertEqual(actual,expected)
        self.assertEqual(calls,scores.size)
        self.assertEqual(report['uncovered_tail_samples'],tail)
        self.assertEqual(report['retained_after_finish'],0)
        self.assertIsNone(detector.buffer)
        return report

    def test_arbitrary_chunks_fft_overlap_and_cross_chunk_events(self):
        for size in [1,127,511,512,777,1024,4096]:
            with self.subTest(size=size):
                self.exercise([[.8,.2],[.9,.4],[.3,.1],[.1,.8],[.2,.9],[.1,.1],[.7,.1],[.8,.2]],size)

    def test_single_window_no_bridge_threshold_and_eof(self):
        self.exercise([.9,.1,.8,.9,.1,.8,.9,.65,.8,.9],777,tail=0)
        self.exercise([.9,.9,.1],511,tail=511)

    def test_silence_short_input_and_no_padding(self):
        def reject(_):
            raise AssertionError('Silence must skip model')
        events=[]
        detector=ChunkDetector(2,reject,events.append)
        detector.feed(np.zeros((1023,2),dtype=np.float32))
        report=detector.finish()
        self.assertEqual(report['windows'],0)
        self.assertEqual(report['uncovered_tail_samples'],1023)
        self.assertEqual(events,[])
        detector=ChunkDetector(2,reject,events.append)
        detector.feed(np.zeros((4096,2),dtype=np.float32))
        self.assertEqual(detector.finish()['windows'],7)
        self.assertEqual(events,[])

    def test_long_active_event_keeps_fixed_audio_state(self):
        events=[]
        detector=ChunkDetector(4,lambda _: .9,events.append)
        for _ in range(100):
            detector.feed(np.ones((4096,4),dtype=np.float32))
            self.assertEqual(detector.buffer.nbytes,16384)
            self.assertEqual(len(events),0)
        report=detector.finish()
        self.assertEqual(len(events),1)
        self.assertEqual(report['samples'],409600)
        self.assertEqual(report['max_buffer_frames'],1024)
        self.assertEqual(report['retained_after_finish'],0)

    def test_processed_input_and_finished_buffer_are_released(self):
        detector=ChunkDetector(2,lambda _: .1,lambda _: None)
        chunk=np.ones((4096,2),dtype=np.float32)
        chunk_ref=weakref.ref(chunk)
        detector.feed(chunk)
        del chunk
        self.assertIsNone(chunk_ref())
        buffer_ref=weakref.ref(detector.buffer)
        detector.finish()
        self.assertIsNone(buffer_ref())

    def test_invalid_chunks_and_lifecycle_rejected(self):
        detector=ChunkDetector(1,lambda _: .8,lambda _: None)
        for invalid in [np.zeros((4097,1)),np.zeros((8,2)),np.full((1,1),np.nan)]:
            with self.assertRaises(ValueError):
                detector.feed(invalid)
        detector.finish()
        with self.assertRaises(ValueError):
            detector.feed(np.zeros((1,1)))
        with self.assertRaises(ValueError):
            detector.finish()


if __name__=='__main__':
    unittest.main()

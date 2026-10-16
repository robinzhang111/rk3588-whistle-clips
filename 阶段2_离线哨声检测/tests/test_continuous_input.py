import sys
import threading
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).absolute().parent.parent / 'tools'))
import continuous_whistle as c
from continuous_input import ProcessSource, run_segment


class Predictor:
    def __init__(self):
        self.closed = False
    def __call__(self, feature):
        return .9
    def close(self):
        self.closed = True


class Source:
    def __init__(self, raw, fault=None):
        self.raw, self.fault, self.closed = raw, fault, False
    def read(self, size, stop):
        if not self.raw and self.fault:
            raise self.fault
        raw, self.raw = self.raw[:min(size, 13)], self.raw[min(size, 13):]
        return raw
    def close(self):
        self.closed = True


class LifecycleTests(unittest.TestCase):
    def test_short_reads_and_reference(self):
        audio = np.ones((4096, 2), dtype='<f4')
        for size in [1, 127, 512, 777, 4096]:
            events, predictor = [], Predictor()
            source = Source(audio.tobytes())
            report = run_segment(source, predictor, events.append, 2, 's', size)
            expected = c.candidate_events(np.full((7, 2), .9, dtype=np.float32),
                                          c.starts_for_samples(4096), 4096, 22050)
            for key, value in expected[0].items():
                self.assertEqual(events[0][key], 'source_end' if key == 'closed_by' else value)
            self.assertEqual(report['samples'], 4096)
            self.assertLessEqual(report['max_input_frames'], size)
            self.assertTrue(source.closed and predictor.closed)

    def test_error_partial_and_interrupt(self):
        for suffix, fault, reason in [(b'x', None, 'incomplete_frame'),
                (b'', RuntimeError('lost input'), 'source_or_processing_error'),
                (b'', KeyboardInterrupt(), 'interrupted')]:
            source = Source(np.ones((1536, 1), dtype='<f4').tobytes()+suffix, fault)
            events, predictor = [], Predictor()
            report = run_segment(source, predictor, events.append, 1, 'fault')
            self.assertEqual(report['close_reason'], reason)
            self.assertEqual(events[0]['closed_by'], reason)
            self.assertFalse(events[0]['natural_offset_verified'])
            self.assertEqual(report['retained_after_finish'], 0)
            self.assertTrue(source.closed and predictor.closed)

    def test_gap_rebuild_never_combines_single_windows(self):
        events = []
        for segment in ['before', 'after']:
            report = run_segment(Source(np.ones((1024, 1), dtype='<f4').tobytes()),
                                 Predictor(), events.append, 1, segment)
            self.assertEqual(report['windows'], 1)
        self.assertEqual(events, [])

    def test_blocked_process_stop_reaps(self):
        stop = threading.Event()
        source = ProcessSource([sys.executable, '-c', 'import time; time.sleep(30)'], 1)
        timer = threading.Timer(.1, stop.set)
        timer.start()
        try:
            report = run_segment(source, Predictor(), lambda _: None, 1, 'stop', stop=stop.is_set)
        finally:
            timer.cancel()
        self.assertEqual(report['close_reason'], 'normal_stop')
        self.assertIsNotNone(source.process.poll())
        self.assertFalse(source.thread.is_alive())

    def test_process_success_and_nonzero(self):
        for code, reason in [('import sys; sys.stdout.buffer.write(bytes(8192))', 'source_end'),
                             ('import sys; sys.exit(3)', 'source_or_processing_error')]:
            source = ProcessSource([sys.executable, '-c', code], 1)
            report = run_segment(source, Predictor(), lambda _: None, 1, 'process')
            self.assertEqual(report['close_reason'], reason)
            self.assertFalse(source.thread.is_alive())


if __name__ == '__main__':
    unittest.main()

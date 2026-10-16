import ctypes as C
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).absolute().parent.parent/'tools'))
sys.path.insert(0, str(Path(__file__).absolute().parents[2]/'阶段1_离线手动触发'))
import board_runtime as runtime
from timed_input import TimedCaptureSource
from verify_resampling_relation import check_relation, validate_relation
from simulate_continuous_input import DeterministicPredictor
from simulate_sampling_probe import synthetic_snapshot

FFMPEG = Path(__file__).absolute().parents[2]/'tools/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe'


class TestClock:
    monotonic_ns = staticmethod(time.perf_counter_ns)
    sleep = staticmethod(time.sleep)
    @staticmethod
    def time_ns():
        return 946684800000000000+time.perf_counter_ns()


class RawPCM:
    configuration = dict(rate=48000, channels=2, format='S16_LE')
    def __init__(self, failure=False, empty=False):
        self.failure, self.empty, self.closed = failure, empty, False
        self.count = 0
        self.buffer = C.create_string_buffer(np.full((1024, 2), 1000, dtype='<i2').tobytes())
    def start(self):
        self.start_ns = time.perf_counter_ns()
    def read(self):
        if self.failure:
            raise OSError('injected raw-source xrun')
        if self.empty:
            return 0
        time.sleep(.024)
        self.count += 1024
        return 1024
    def status(self):
        return synthetic_snapshot(delay=0, avail=0,
            htstamp_ns=self.start_ns+self.count*10**9//48000)
    def close(self):
        self.closed = True


class DriftPCM(RawPCM):
    def __init__(self, later_failure=None):
        super().__init__()
        self.later_failure = later_failure

    def status(self):
        snapshot = super().status()
        snapshot['htstamp_ns'] += (self.count//1024)*200000
        if self.count >= 40*1024:
            if self.later_failure == 'jump':
                snapshot['htstamp_ns'] += 20000000
            elif self.later_failure == 'state':
                snapshot['state'] = 4
        return snapshot

    def read(self):
        if self.later_failure == 'read' and self.count >= 40*1024:
            raise OSError('injected later raw-source xrun')
        return super().read()


class TimedInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.relation = Path(cls.temp.name)/'relation.json'
        cls.relation.write_text(json.dumps(check_relation(FFMPEG)), encoding='utf-8')
    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_same_source_runtime_software_time_and_preserved_gate(self):
        self.run_runtime(RawPCM(), 1.5, True)

    def test_explicit_label_sink_receives_saved_software_candidate(self):
        sink = SimpleNamespace(allow_software_time=True, pump=Mock(), save=Mock(),
                               close=Mock(return_value={'saved':1,'all_acknowledged':True}))
        state = self.run_runtime(RawPCM(), 1.5, True, sink)
        sink.save.assert_called_once()
        self.assertEqual(sink.save.call_args[0][1]['status'], 'software_estimate')
        self.assertTrue(state['automatic_labels']['all_acknowledged'])

    def test_raw_source_failure_closes_and_invalidates(self):
        state = self.run_runtime(RawPCM(failure=True), .4, False)
        self.assertEqual(state['status'], 'failed')

    def test_default_still_stops_on_accumulated_time_failure(self):
        state = self.run_runtime(DriftPCM(), 1.8, False)
        self.assertEqual(state['status'], 'failed')

    def test_offline_drift_continues_with_relative_candidates_and_no_utc(self):
        state = self.run_runtime(DriftPCM(), 1.8, True, allow_relative=True)
        self.assertEqual(state['status'], 'closed')
        self.assertTrue(state['resources_released'])
        self.assertEqual(state['sampling_time_invalid_reason'], 'sample_clock_discontinuity')
        self.assertEqual(state['time_basis'], 'relative samples; software time invalid')

    def test_offline_later_jump_xrun_or_pcm_failure_still_stops(self):
        for failure in ('jump', 'state', 'read'):
            with self.subTest(failure=failure):
                state = self.run_runtime(DriftPCM(failure), 1.8, False, allow_relative=True)
                self.assertEqual(state['status'], 'failed')

    def test_empty_source_cooperative_stop_releases(self):
        state = self.run_runtime(RawPCM(empty=True), .1, False)
        self.assertEqual(state['status'], 'closed')
        self.assertTrue(state['resources_released'])

    def test_wrong_binary_or_filter_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'relation.json'
            report = json.loads(self.relation.read_text())
            report['filter'] = 'different filter'
            path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                validate_relation(path, FFMPEG)

    def run_runtime(self, pcm, seconds, expect_candidate, sink=None, allow_relative=False):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'session'
            stopped = threading.Event()
            timer = threading.Timer(seconds, stopped.set)
            config = runtime.preflight(output, 'fixture', 2, 512, True, input_gain=16)
            diagnostic = []
            class Sink:
                def write(self, text):
                    diagnostic.append(text)
                def flush(self):
                    pass
            source = []
            def factory(log):
                made = TimedCaptureSource(FFMPEG, self.relation, lambda: pcm,
                                          'fixture:segment-0001', Sink(), log=log, clock=TestClock,
                                          allow_relative_on_clock_drift=allow_relative)
                source.append(made)
                timer.start()
                return made
            try:
                state = runtime.run_session(output, config, factory, DeterministicPredictor,
                    stopped, timing_factory=lambda segment, source: source.timing,
                    label_factory=(lambda path: sink) if sink is not None else None)
            finally:
                timer.cancel()
                stopped.set()
            self.assertTrue(pcm.closed)
            self.assertFalse(source[0].capture_thread.is_alive())
            self.assertFalse(source[0].thread.is_alive())
            self.assertIsNotNone(source[0].process.poll())
            if expect_candidate:
                rows = [json.loads(line) for line in (output/'candidates.relative.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertGreater(len(rows), 0, json.dumps(state))
                self.assertEqual(state['status'], 'closed', json.dumps(state))
                self.assertTrue(state['resources_released'])
                expected_status = 'relative_only' if allow_relative else 'software_estimate'
                self.assertTrue(all(row['timing']['status']==expected_status for row in rows))
                if allow_relative:
                    self.assertTrue(all(row['timing']['estimated_utc'] is None for row in rows))
                self.assertTrue(all(row['production_path_blocked']==(sink is None) for row in rows))
                self.assertEqual(state['input_gain'], 16)
            return state


if __name__ == '__main__':
    unittest.main()

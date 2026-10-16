import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / 'tools'))
import board_runtime as runtime
from continuous_input import ProcessSource
from simulate_continuous_input import DeterministicPredictor


class Source:
    def __init__(self, suffix=b'', fault=None):
        self.raw = np.ones((4096, 1), dtype='<f4').tobytes() + suffix
        self.fault, self.closed = fault, False

    def read(self, size, stop):
        if not self.raw and self.fault:
            raise self.fault
        data, self.raw = self.raw[:size], self.raw[size:]
        return data

    def close(self):
        self.closed = True


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'session'

    def config(self, output=None, session='synthetic-test'):
        return runtime.preflight(output or self.output, session, 1, 512, True)

    def run_source(self, source):
        predictor = DeterministicPredictor()
        result = runtime.run_session(self.output, self.config(), lambda log: source,
                                     lambda: predictor)
        self.assertTrue(source.closed and predictor.closed)
        return result

    def test_preflight_no_launch_and_rejection_no_output(self):
        with patch.object(runtime, 'ProcessSource') as launch, patch.object(runtime, 'NpuPredictor') as npu:
            self.config()
            cases = [dict(channels=0), dict(chunk_frames=4097), dict(session=' '),
                     dict(duration=float('nan')), dict(duration=0),
                     dict(command=['unverified-producer']), dict(model=Path('missing.rknn'))]
            for changes in cases:
                args = dict(output=self.output, session='s', channels=1,
                            chunk_frames=512, simulate=True)
                args.update(changes)
                with self.assertRaises(ValueError):
                    runtime.preflight(**args)
            launch.assert_not_called()
            npu.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_real_preflight_requires_explicit_producer_and_fixed_model(self):
        wrong_model = Path(self.temp.name) / 'wrong.rknn'
        wrong_model.write_bytes(b'not the fixed model')
        for command, model in [(None, None), ([sys.executable], None), ([sys.executable], wrong_model)]:
            with self.assertRaises(ValueError):
                runtime.preflight(self.output, 's', 1, 512, False, command, model)
        self.assertFalse(self.output.exists())

    def test_signal_handler_requests_cooperative_stop_and_restores(self):
        handlers, installed = {}, []
        def install(sig, handler):
            previous = handlers.get(sig)
            handlers[sig] = handler
            installed.append(sig)
            return previous
        source = Source()
        def factory(channels, duration, stopped):
            handlers[runtime.signal.SIGTERM](runtime.signal.SIGTERM, None)
            return source
        with patch.object(runtime.signal, 'signal', side_effect=install), \
                patch.object(runtime, 'SimulationSource', side_effect=factory):
            result = runtime.main(['run', '--simulate', '--channels', '1',
                                   '--session', 'signal-test', '--output', str(self.output)])
        self.assertEqual(result, 0)
        state = json.loads((self.output / 'status.json').read_text())
        self.assertEqual(state['segment_report']['close_reason'], 'normal_stop')
        self.assertTrue(source.closed and state['resources_released'])
        self.assertEqual(len(installed), 4)

    def test_relative_candidates_durable_and_no_upload(self):
        result = self.run_source(Source())
        self.assertEqual(result['status'], 'closed')
        self.assertTrue(result['resources_released'])
        self.assertEqual(result['production_gate_rejections'], 1)
        rows = [json.loads(line) for line in (self.output / 'candidates.relative.jsonl').read_text().splitlines()]
        self.assertEqual(rows[0]['timing']['status'], 'relative_only')
        self.assertIsNone(rows[0]['candidate']['absolute_utc_start'])
        self.assertNotIn('event_time_utc', rows[0])
        self.assertFalse(result['formal_utc_upload'])
        self.assertEqual(result['segment_report']['retained_after_finish'], 0)
        self.assertEqual({p.name for p in self.output.iterdir()},
                         {'status.json', 'producer.log', 'candidates.relative.jsonl'})

    def test_exclusive_session_and_fresh_segment(self):
        first = self.run_source(Source())
        before = (self.output / 'candidates.relative.jsonl').read_bytes()
        with self.assertRaises(ValueError):
            self.config()
        with self.assertRaises(FileExistsError):
            runtime.run_session(self.output, first, lambda log: self.fail('must not open'),
                                lambda: self.fail('must not open'))
        self.assertEqual(before, (self.output / 'candidates.relative.jsonl').read_bytes())
        second_path = self.output.parent / 'second'
        second = runtime.run_session(second_path, self.config(second_path, 'second'),
                                     lambda log: Source(), DeterministicPredictor)
        self.assertNotEqual(first['segment_id'], second['segment_id'])
        self.assertEqual(first['segment_report']['samples'], second['segment_report']['samples'])

    def test_startup_source_failure_closes_predictor(self):
        predictor = DeterministicPredictor()
        def fail(log):
            raise OSError('producer startup failed')
        result = runtime.run_session(self.output, self.config(), fail, lambda: predictor)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(predictor.closed and result['resources_released'])
        self.assertIn('producer startup failed', result['error'])

    def test_residual_frame_and_source_error_fail_not_eof(self):
        for index, source in enumerate([Source(b'x'), Source(fault=OSError('input lost'))]):
            self.output = Path(self.temp.name) / str(index)
            result = self.run_source(source)
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(result['resources_released'])
            rows = json.loads((self.output / 'candidates.relative.jsonl').read_text())
            self.assertEqual(rows['timing']['reason'], result['segment_report']['close_reason'])
            self.assertNotEqual(result['segment_report']['close_reason'], 'source_end')

    def test_keyboard_interrupt_preserves_candidate_and_closes(self):
        result = self.run_source(Source(fault=KeyboardInterrupt()))
        self.assertEqual(result['segment_report']['close_reason'], 'interrupted')
        self.assertTrue(result['resources_released'])
        row = json.loads((self.output / 'candidates.relative.jsonl').read_text())
        self.assertEqual(row['timing']['reason'], 'interrupted')

    def test_blocked_producer_stop_and_reap(self):
        source = ProcessSource([sys.executable, '-B', '-c', 'import time; time.sleep(30)'], 1)
        def stop():
            (self.output / 'STOP').touch()
        timer = threading.Timer(.15, stop)
        timer.start()
        try:
            result = self.run_source(source)
        finally:
            timer.cancel()
            timer.join()
        self.assertEqual(result['segment_report']['close_reason'], 'normal_stop')
        self.assertFalse(source.thread.is_alive())
        self.assertIsNotNone(source.process.poll())

    def test_cleanup_failure_is_failed(self):
        source = Source()
        def fail_close():
            source.closed = True
            raise OSError('source close failed')
        source.close = fail_close
        result = self.run_source(source)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('source close failed', result['error'])
        self.assertFalse(result['resources_released'])

    def test_candidate_fsync_failure_never_counted_success(self):
        real_fsync = runtime.os.fsync
        def fail_candidates(fd):
            # status.tmp and producer.log are binary; candidate writer is identifiable
            # by the existing nonempty candidate file at the first save attempt.
            path = self.output / 'candidates.relative.jsonl'
            if path.exists() and path.stat().st_size and not failed[0]:
                failed[0] = True
                raise OSError('candidate fsync failed')
            return real_fsync(fd)
        failed = [False]
        with patch.object(runtime.os, 'fsync', side_effect=fail_candidates):
            result = self.run_source(Source())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['candidates_saved'], 0)
        self.assertIn('candidate fsync failed', result['error'])

    def test_midstream_save_failure_poisoned_no_finish_retry_write(self):
        real_fsync = runtime.os.fsync
        calls = [0]
        class PulsePredictor(DeterministicPredictor):
            def __call__(self, feature):
                calls[0] += 1
                return .9 if calls[0] <= 2 else .1
        source, predictor = Source(), PulsePredictor()
        def fail(fd):
            path = self.output / 'candidates.relative.jsonl'
            if path.exists() and path.stat().st_size:
                raise OSError('midstream fsync failure')
            return real_fsync(fd)
        with patch.object(runtime.os, 'fsync', side_effect=fail):
            # Fail only candidate persistence; final status is written after the
            # poisoned writer has rejected the finish retry.
            with patch.object(runtime, 'save_status'):
                state = runtime.run_session(self.output, self.config(), lambda log: source,
                                            lambda: predictor)
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['candidates_saved'], 0)
        self.assertEqual(state['candidate_save_error'], 'midstream fsync failure')
        self.assertEqual(len((self.output / 'candidates.relative.jsonl').read_text().splitlines()), 1)
        self.assertTrue(source.closed and predictor.closed)

    def test_cli_continuous_status_stop_and_no_overwrite(self):
        script = str(Path(runtime.__file__))
        command = [sys.executable, '-B', script]
        logfile = (Path(self.temp.name) / 'cli.log').open('wb')
        self.addCleanup(logfile.close)
        process = subprocess.Popen(command + ['run', '--simulate', '--channels', '1',
                                   '--session', 'cli-synthetic', '--output', str(self.output)],
                                   stdout=logfile, stderr=subprocess.STDOUT)
        try:
            deadline = time.perf_counter() + 10
            while time.perf_counter() < deadline:
                if (self.output / 'status.json').exists():
                    state = json.loads((self.output / 'status.json').read_text())
                    if state['windows'] > 2:
                        break
                if process.poll() is not None:
                    self.fail('CLI exited before running')
                time.sleep(.05)
            else:
                self.fail('No running status')
            status = subprocess.run(command + ['status', '--output', str(self.output)], capture_output=True)
            self.assertEqual(status.returncode, 0)
            self.assertEqual(json.loads(status.stdout)['status'], 'running')
            stop = subprocess.run(command + ['stop', '--output', str(self.output)], capture_output=True)
            self.assertEqual(stop.returncode, 0)
            self.assertEqual(process.wait(10), 0)
            state = json.loads((self.output / 'status.json').read_text())
            self.assertTrue(state['resources_released'])
            self.assertEqual(state['segment_report']['close_reason'], 'normal_stop')
            refused = subprocess.run(command + ['run', '--simulate', '--channels', '1',
                                     '--session', 'cli-synthetic', '--output', str(self.output)], capture_output=True)
            self.assertEqual(refused.returncode, 1)
            self.assertEqual(state, json.loads((self.output / 'status.json').read_text()))
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(10)


if __name__ == '__main__':
    unittest.main()

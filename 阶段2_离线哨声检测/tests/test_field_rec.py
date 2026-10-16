import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / 'tools'))
from board_runtime import preflight, run_session, SimulationSource
from simulate_continuous_input import DeterministicPredictor
from field_rec import controlled_run


class FieldTests(unittest.TestCase):
    def trial(self, duration=.2, early=False, fail=False):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)/'trial'/'capture'
            output.parent.mkdir()
            config = preflight(output, 'synthetic-field', 2, 512, True, input_gain=16)
            stopped = threading.Event()
            reads = [0]
            def rec(timeout):
                time.sleep(.01)
                reads[0] += 1
                if reads[0] < 3:
                    self.assertFalse(output.exists())
                    return False
                if reads[0] == 3:
                    return True
                if fail:
                    raise OSError('unplugged synthetic REC')
                return early
            def run(event):
                return run_session(output, config,
                    lambda log: SimulationSource(2, None, event),
                    DeterministicPredictor, event)
            result = controlled_run(output, config, rec, run, stopped,
                                    duration, announce=lambda *a, **k: None)
            control = json.loads((output.parent/'status.json').read_text())
            state = json.loads((output/'status.json').read_text())
            self.assertTrue(state['resources_released'])
            self.assertFalse(state['raw_audio_saved'])
            self.assertFalse(state['formal_utc_upload'])
            self.assertEqual(state['segment_report']['close_reason'], 'normal_stop')
            self.assertEqual(set(p.name for p in output.iterdir()),
                             {'status.json', 'producer.log', 'candidates.relative.jsonl'})
            return result, control

    def test_deadline_normal_cleanup_without_receiver_or_recording(self):
        code, state = self.trial()
        self.assertEqual(code, 0)
        self.assertEqual(state['stop_reason'], 'duration_limit')

    def test_second_press_stops_after_debounce(self):
        code, state = self.trial(duration=2, early=True)
        self.assertEqual(code, 0)
        self.assertEqual(state['stop_reason'], 'rec_stop')

    def test_rec_failure_requests_cleanup_and_fails_trial(self):
        code, state = self.trial(fail=True)
        self.assertEqual(code, 1)
        self.assertEqual(state['stop_reason'], 'rec_input_failure')

    def test_cancel_waiting_never_calls_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            stopped = threading.Event()
            def read(timeout):
                stopped.set()
                return False
            def run(event):
                self.fail('runtime called before REC')
            self.assertEqual(controlled_run(Path(temp)/'capture', {'session_id':'s'},
                read, run, stopped, announce=lambda *a, **k: None), 130)
            self.assertFalse((Path(temp)/'capture').exists())


if __name__ == '__main__':
    unittest.main()

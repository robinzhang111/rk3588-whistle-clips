import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / 'tools'))
import start_space as space


class SpaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'new-session'
        self.command = [sys.executable, '-B', str(Path(space.__file__).with_name('board_runtime.py')),
                        'run', '--simulate', '--channels', '2', '--session', 'synthetic-space',
                        '--output', str(self.output)]

    def test_no_start_before_space_and_stop_during_running(self):
        messages, presses = [], [0]
        def key(timeout):
            time.sleep(.02)
            if presses[0] == 0:
                self.assertFalse(self.output.exists())
                presses[0] = 1
                return 'x'
            if presses[0] == 1:
                self.assertFalse(self.output.exists())
                presses[0] = 2
                return ' '
            path = self.output / 'status.json'
            if path.exists() and json.loads(path.read_text())['windows'] >= 10:
                presses[0] += 1
                return ' '
        self.assertEqual(space.control(self.command, self.output, key, messages.append), 0)
        state = json.loads((self.output / 'status.json').read_text())
        self.assertEqual(state['segment_report']['close_reason'], 'normal_stop')
        self.assertTrue(state['resources_released'])
        self.assertFalse(state['formal_utc_upload'])
        self.assertGreater(state['windows'], 0)

    def test_cancel_before_start(self):
        def key(timeout):
            raise KeyboardInterrupt()
        with patch.object(space.subprocess, 'Popen') as launch:
            self.assertEqual(space.control(self.command, self.output, key, lambda _: None), 130)
            launch.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_stop_before_status_created(self):
        self.assertEqual(space.control(self.command, self.output, lambda _: ' ', lambda _: None), 0)
        state = json.loads((self.output/'status.json').read_text())
        self.assertTrue(state['resources_released'])

    def test_check_only_has_no_output_or_launch(self):
        with patch.object(sys, 'argv', ['start_space.py', '--simulate', '--check-only',
                                       '--session', 's', '--output', str(self.output)]), \
                patch.object(space.subprocess, 'Popen') as launch:
            self.assertEqual(space.main(), 0)
            launch.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()

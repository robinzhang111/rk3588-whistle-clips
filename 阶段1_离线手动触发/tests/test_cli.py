from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from basketclip.storage import load_events, load_json


class CliTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)

    def run_cli(self, *args, **kwargs):
        environment = dict(os.environ, PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, "-m", "basketclip"] + list(args),
                              capture_output=True, text=True, encoding="utf-8",
                              env=environment, **kwargs)

    def test_record_and_reject_overwrite(self):
        path = self.path / "events.jsonl"
        result = self.run_cli("record", "--session", "g", "--output", str(path), input="m\nm\nq\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        events = load_events(path)
        self.assertEqual([item["event_id"] for item in events], [1, 2])
        self.assertLessEqual(events[0]["event_time_utc"], events[1]["event_time_utc"])
        retry = self.run_cli("record", "--session", "g", "--output", str(path), input="q\n")
        self.assertNotEqual(retry.returncode, 0)
        self.assertEqual(load_events(path), events)

    def test_align_command_saves_mapping(self):
        path = self.path / "alignment.json"
        result = self.run_cli("align", "--session", "g", "--event-utc",
                              "2026-10-10T12:00:00Z", "--video-seconds", "5", "--output", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(load_json(path)["anchor_video_seconds"], 5)

    def test_record_simulation_produces_usable_alignment(self):
        events_path = self.path / "events.jsonl"
        alignment_path = self.path / "alignment.json"
        result = self.run_cli("record", "--session", "g", "--output", str(events_path),
                              "--alignment-output", str(alignment_path), "--video-seconds", "10",
                              input="m\nq\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        from basketclip.timing import event_position
        position = event_position(load_events(events_path)[0], load_json(alignment_path))
        self.assertGreaterEqual(position, 10)
        self.assertLess(position, 12)

    def test_simulation_rejects_conflicting_paths(self):
        path = self.path / "same.json"
        result = self.run_cli("record", "--session", "g", "--output", str(path),
                              "--alignment-output", str(path), input="q\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(path.exists())

    def test_invalid_mapping_does_not_create_file(self):
        path = self.path / "alignment.json"
        result = self.run_cli("align", "--session", "g", "--event-utc",
                              "2026-10-10T12:00:00Z", "--video-seconds", "-1", "--output", str(path))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(path.exists())

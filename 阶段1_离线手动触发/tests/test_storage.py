import json
from pathlib import Path
import tempfile
import unittest

from basketclip.storage import load_events, save_json


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "events.jsonl"
        self.event = {"session_id": "g", "event_id": 1, "event_type": "manual_trigger",
                      "event_time_utc": "2026-10-10T12:00:00Z"}

    def write_events(self, events):
        self.path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")

    def test_duplicate_import_deduplicates(self):
        self.write_events([self.event, self.event])
        self.assertEqual(load_events(self.path), [self.event])

    def test_conflicting_event_rejected(self):
        changed = dict(self.event, event_time_utc="2026-10-10T12:00:01Z")
        self.write_events([self.event, changed])
        with self.assertRaises(ValueError):
            load_events(self.path)

    def test_invalid_event_id_rejected(self):
        for value in (0, -1, True, "../file"):
            self.write_events([dict(self.event, event_id=value)])
            with self.assertRaises(ValueError):
                load_events(self.path)

    def test_empty_events_rejected(self):
        self.write_events([])
        with self.assertRaises(ValueError):
            load_events(self.path)

    def test_write_does_not_overwrite_existing_file(self):
        save_json(self.path, {"original": True})
        with self.assertRaises(FileExistsError):
            save_json(self.path, {"original": False})
        self.assertTrue(json.loads(self.path.read_text(encoding="utf-8"))["original"])

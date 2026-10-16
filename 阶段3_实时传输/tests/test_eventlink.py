import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eventlink as link
link.stage1_path(os.environ.get("STAGE1_PATH", Path(__file__).resolve().parents[2] / "阶段1_离线手动触发"))
from basketclip.cli import record
from basketclip.storage import load_events


class CommunicationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.event = dict(session_id="test", event_id=1, event_type="manual_trigger",
                          event_time_utc="2026-10-10T12:00:00Z")
        self.store = link.Store(self.path / "labels.sqlite3", "test")
        self.addCleanup(self.store.db.close)

    def start_server(self, port=0):
        http = link.server(("127.0.0.1", port), self.store)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        return "http://127.0.0.1:%d/events" % http.server_port

    def wait_empty(self, uploader):
        deadline = time.monotonic() + 4
        while uploader.snapshot() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(uploader.snapshot(), [])

    def test_http_upload_preserves_fields_and_delayed_time(self):
        ack = link.send(self.start_server(), self.event, 1)
        saved, received = self.store.db.execute("SELECT payload,received FROM events").fetchone()
        self.assertEqual(json.loads(saved), self.event)
        self.assertEqual(ack["received_time_utc"], received)
        self.assertNotEqual(received, self.event["event_time_utc"])
        self.assertEqual(ack["status"], "label_saved")

    def test_duplicate_ack_is_identical(self):
        url = self.start_server()
        self.assertEqual(link.send(url, self.event, 1), link.send(url, self.event, 1))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events").fetchone()[0], 1)

    def test_invalid_fields_and_session_rejected_without_save(self):
        invalid = [dict(self.event, event_id=v) for v in (0, True, "1", 2**63)]
        invalid += [dict(self.event, session_id="wrong"), dict(self.event, session_id=" "),
                    dict(self.event, event_type="unknown"), dict(self.event, event_time_utc="bad"),
                    dict(self.event, event_time_utc="2026-10-10T12:00:00"),
                    dict(self.event, extra=1), {}, []]
        url = self.start_server()
        for event in invalid:
            with self.subTest(event=event), self.assertRaises(link.Rejected):
                link.send(url, event, 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events").fetchone()[0], 0)

    def test_conflict_keeps_original(self):
        url = self.start_server()
        ack = link.send(url, self.event, 1)
        with self.assertRaises(link.Rejected):
            link.send(url, dict(self.event, event_type="whistle"), 1)
        self.assertEqual(link.send(url, self.event, 1), ack)

    def test_failed_persistence_never_returns_ack(self):
        self.store.db.execute("PRAGMA query_only=ON")
        with self.assertRaises(sqlite3.Error):
            self.store.save(self.event)

    def test_ack_is_visible_to_independent_connection(self):
        self.store.save(self.event)
        with sqlite3.connect(str(self.path / "labels.sqlite3")) as other:
            self.assertEqual(json.loads(other.execute("SELECT payload FROM events").fetchone()[0]), self.event)

    def test_receiver_reopen_deduplicates(self):
        ack = self.store.save(self.event)
        other = link.Store(self.path / "labels.sqlite3", "test")
        try:
            self.assertEqual(other.save(self.event), ack)
        finally:
            other.db.close()

    def test_disconnect_before_save_recovers(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        uploader = link.Uploader("http://127.0.0.1:%d/events" % port, timeout=0.2, retry=0.1)
        self.addCleanup(uploader.close, 0)
        uploader.submit(self.event)
        time.sleep(0.2)
        self.assertEqual(uploader.snapshot(), [self.event])
        self.start_server(port)
        self.wait_empty(uploader)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events").fetchone()[0], 1)

    def test_saved_but_ack_lost_retries_without_duplicate(self):
        original_send = link.send
        attempts = []
        url = self.start_server()
        def lose_first_ack(url, event, timeout):
            ack = original_send(url, event, timeout)
            attempts.append(ack)
            if len(attempts) == 1:
                raise OSError("injected ACK loss after save")
            return ack
        with patch.object(link, "send", side_effect=lose_first_ack):
            uploader = link.Uploader(url, retry=0.05)
            try:
                uploader.submit(self.event)
                self.wait_empty(uploader)
            finally:
                uploader.close(0)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events").fetchone()[0], 1)

    def test_rec_saved_callback_after_fsync_and_before_sending(self):
        path = self.path / "events.jsonl"
        args = argparse.Namespace(session="test", output=str(path), board_button=False)
        fsynced = []
        original = os.fsync
        def sync(fd):
            original(fd)
            fsynced.append(True)
        captured = []
        def saved(event):
            self.assertTrue(fsynced)
            self.assertEqual(load_events(path)[-1], event)
            captured.append(event)
        with patch("basketclip.cli.trigger_times", return_value=iter([
                link.datetime.now(link.timezone.utc), link.datetime.now(link.timezone.utc)])), \
                patch("basketclip.cli.os.fsync", side_effect=sync):
            record(args, on_saved=saved)
        self.assertEqual([e["event_id"] for e in captured], [1, 2])

    def test_slow_network_does_not_block_recorder(self):
        path = self.path / "events.jsonl"
        args = argparse.Namespace(session="test", output=str(path), board_button=False)
        blocked, release = threading.Event(), threading.Event()
        def slow_send(url, event, timeout):
            blocked.set()
            release.wait(2)
            return link.receipt(event, "2026-10-10T12:01:00Z")
        with patch.object(link, "send", side_effect=slow_send):
            uploader = link.Uploader("unused")
            try:
                with patch("basketclip.cli.trigger_times", return_value=iter([
                        link.datetime.now(link.timezone.utc)] * 5)):
                    record(args, on_saved=uploader.submit)
                self.assertTrue(blocked.wait(1))
                self.assertEqual(len(load_events(path)), 5)
                self.assertTrue(uploader.snapshot())
            finally:
                release.set()
                uploader.close(2)


if __name__ == "__main__":
    unittest.main()

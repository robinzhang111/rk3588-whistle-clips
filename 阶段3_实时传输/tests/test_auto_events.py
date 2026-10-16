import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).absolute().parents[1]))
import eventlink as link
link.stage1_path(Path(__file__).absolute().parents[2]/'阶段1_离线手动触发')
from auto_events import candidate_event, EventJournal, DurableReplay


def event(index=1):
    return dict(session_id='synthetic-D', event_id=index, event_type='whistle',
                event_time_utc='2000-01-01T00:00:00Z')


class FakeUploader:
    def __init__(self):
        self.pending = []
    def snapshot(self):
        return self.pending[:]
    def submit(self, event):
        self.pending.append(event)


class AutoTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/'events.jsonl'

    def test_time_gate_and_matching(self):
        candidate = dict(segment_id='s', estimated_start_sample=100)
        timing = dict(status='verified_sampling', reason=None, synthetic_utc=None,
                      estimated_utc='2000-01-01T00:00:00Z', segment_id='s', target_start_sample=100,
                      anchor_evidence='external fixture only', relation_evidence='fixture')
        self.assertEqual(candidate_event(candidate, timing, 'synthetic-D', 1), event())
        for changes in [dict(status='synthetic'), dict(status='relative_only'), dict(reason='gap'),
                        dict(synthetic_utc='2000-01-01T00:00:00Z'), dict(segment_id='new'),
                        dict(target_start_sample=101), dict(anchor_evidence=''), dict(estimated_utc=None)]:
            with self.assertRaises(ValueError):
                candidate_event(candidate, dict(timing, **changes), 'synthetic-D', 1)
        with self.assertRaises(ValueError):
            candidate_event(dict(candidate, estimated_start_sample=True), timing, 'synthetic-D', 1)

    def test_sync_before_submit_and_active_read_limit(self):
        journal = EventJournal(self.path, 'synthetic-D')
        self.addCleanup(journal.close)
        uploader = FakeUploader()
        replay = DurableReplay(self.path, 'synthetic-D', uploader, lambda: journal.saved)
        self.addCleanup(replay.close)
        synced = []
        original = os.fsync
        def sync(fd):
            original(fd)
            synced.append(True)
        with patch('auto_events.os.fsync', side_effect=sync):
            self.assertEqual(replay.pump(), 'caught_up')
            journal.save(event())
        self.assertTrue(synced)
        self.assertEqual(replay.pump(), 'submitted')
        self.assertEqual(uploader.pending, [event()])

    def test_write_flush_sync_failures_do_not_submit(self):
        for method in ['write', 'flush', 'fsync']:
            path = self.path.with_name(method+'.jsonl')
            journal = EventJournal(path, 'synthetic-D')
            uploader = FakeUploader()
            replay = DurableReplay(path, 'synthetic-D', uploader, lambda: journal.saved)
            try:
                if method == 'fsync':
                    context = patch('auto_events.os.fsync', side_effect=OSError('fixture sync failure'))
                else:
                    original = journal.stream
                    journal.stream = Mock(wraps=original)
                    context = patch.object(journal.stream, method, side_effect=OSError('fixture write failure'))
                with context, self.assertRaises(OSError):
                    journal.save(event())
                self.assertEqual(journal.saved, 0)
                self.assertEqual(replay.pump(), 'caught_up')
                self.assertEqual(uploader.pending, [])
                with self.assertRaises(ValueError):
                    journal.save(event())
            finally:
                replay.close()
                journal.close()

    def test_backlog_bounded_and_restart_replays(self):
        journal = EventJournal(self.path, 'synthetic-D')
        for i in range(1, 101):
            journal.save(event(i))
        journal.close()
        for _ in range(2):
            uploader = FakeUploader()
            replay = DurableReplay(self.path, 'synthetic-D', uploader)
            try:
                for i in range(1, 101):
                    self.assertEqual(replay.pump(), 'submitted')
                    for __ in range(3):
                        self.assertEqual(replay.pump(), 'pending')
                    self.assertEqual(len(uploader.pending), 1)
                    self.assertEqual(uploader.pending[0], event(i))
                    uploader.pending.clear()
                self.assertEqual(replay.pump(), 'eof')
            finally:
                replay.close()

    def test_no_overwrite_and_invalid_number(self):
        journal = EventJournal(self.path, 'synthetic-D')
        try:
            with self.assertRaises(ValueError):
                journal.save(event(2))
            with self.assertRaises(FileExistsError):
                EventJournal(self.path, 'synthetic-D')
        finally:
            journal.close()

    def test_corrupt_replay_rejected_before_submission(self):
        self.path.write_text(json.dumps(event())+'\n{bad', encoding='utf-8')
        uploader = FakeUploader()
        with self.assertRaises(ValueError):
            DurableReplay(self.path, 'synthetic-D', uploader)
        self.assertEqual(uploader.pending, [])
        self.path.write_text('x'*5000+'\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            DurableReplay(self.path, 'synthetic-D', uploader)


if __name__ == '__main__':
    unittest.main()

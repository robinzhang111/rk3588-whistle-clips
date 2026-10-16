import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).absolute().parents[1]))
import eventlink
eventlink.stage1_path(Path(__file__).absolute().parents[2]/'阶段1_离线手动触发')
from automatic_labels import AutomaticLabels
from auto_events import candidate_event


def fixture():
    candidate = dict(segment_id='s', estimated_start_sample=100)
    timing = dict(status='software_estimate', reason=None, synthetic_utc=None,
        estimated_utc='2000-01-01T00:00:00Z', segment_id='s', target_start_sample=100,
        anchor_evidence='synthetic fixture for policy only', relation_evidence='fixture',
        time_quality='software_estimate', sampling_utc_verified=False, total_uncertainty_ns=None)
    return candidate, timing


class FakeUploader:
    def __init__(self):
        self.pending = []
        self.submitted = []
    def snapshot(self):
        return self.pending[:]
    def submit(self, event):
        self.pending.append(event)
        self.submitted.append(event)
    def close(self, seconds):
        return self.snapshot()


class AutomaticLabelTests(unittest.TestCase):
    def test_software_policy_explicit_and_matching(self):
        candidate, timing = fixture()
        with self.assertRaises(ValueError):
            candidate_event(candidate, timing, 'session', 1)
        self.assertEqual(set(candidate_event(candidate, timing, 'session', 1, True)),
                         {'session_id','event_id','event_type','event_time_utc'})
        for changes in [dict(status='synthetic'), dict(status='relative_only'),
                        dict(time_quality='verified_sampling'), dict(sampling_utc_verified=True),
                        dict(reason='gap'), dict(target_start_sample=101)]:
            with self.assertRaises(ValueError):
                candidate_event(candidate, dict(timing, **changes), 'session', 1, True)

    def test_metadata_and_label_sync_before_single_pending_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            upload = FakeUploader()
            labels = AutomaticLabels(directory, 'session', 'unused', True, upload)
            candidate, timing = fixture()
            synced = []
            original = os.fsync
            def sync(fd):
                synced.append(fd)
                original(fd)
            with patch('automatic_labels.os.fsync', side_effect=sync):
                labels.save(candidate, timing)
            self.assertEqual(synced, [labels.metadata.fileno(), labels.journal.stream.fileno()])
            self.assertEqual(upload.submitted, [])
            labels.pump()
            labels.save(candidate, timing)
            labels.pump()
            self.assertEqual(len(upload.pending), 1)
            upload.pending.clear()
            labels.pump()
            upload.pending.clear()
            result = labels.close(0)
            self.assertTrue(result['all_acknowledged'])
            self.assertEqual(result['saved'], 2)
            metadata = [json.loads(s) for s in (Path(directory)/'event-metadata.jsonl').read_text().splitlines()]
            self.assertTrue(all(row['time_quality']=='software_estimate' for row in metadata))

    def test_metadata_sync_failure_never_creates_or_submits_label(self):
        with tempfile.TemporaryDirectory() as directory:
            upload = FakeUploader()
            labels = AutomaticLabels(directory, 'session', 'unused', True, upload)
            with patch('automatic_labels.os.fsync', side_effect=OSError('metadata fsync failed')):
                with self.assertRaises(OSError):
                    labels.save(*fixture())
            self.assertEqual(labels.journal.saved, 0)
            self.assertEqual(upload.submitted, [])
            with self.assertRaises(ValueError):
                labels.save(*fixture())
            result = labels.close(0)
            self.assertFalse(result['all_acknowledged'])


if __name__ == '__main__':
    unittest.main()

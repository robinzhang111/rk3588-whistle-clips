"""Explicit software-time policy; metadata and four-field labels synced before send."""
import json
import os
from pathlib import Path
import time

from auto_events import candidate_event, EventJournal, DurableReplay
from eventlink import Uploader


class AutomaticLabels:
    def __init__(self, output, session, url, allow_software_time=False, uploader=None):
        self.session, self.allow_software_time = session, allow_software_time
        self.failed = False
        self.journal = EventJournal(Path(output)/'events.jsonl', session)
        self.metadata = None
        self.uploader = None
        try:
            self.metadata = (Path(output)/'event-metadata.jsonl').open('x', encoding='utf-8')
            self.uploader = uploader or Uploader(url)
            self.replay = DurableReplay(self.journal.path, session, self.uploader,
                                        available=lambda: self.journal.saved)
        except BaseException:
            if self.uploader is not None:
                self.uploader.close(0)
            if self.metadata is not None:
                self.metadata.close()
            self.journal.close()
            raise

    def save(self, candidate, timing):
        if self.failed:
            raise ValueError('Automatic label writer failed; preserve for inspection')
        event = candidate_event(candidate, timing, self.session, self.journal.saved+1,
                                allow_software_time=self.allow_software_time)
        metadata = dict(session_id=self.session, event_id=event['event_id'],
            event_time_utc=event['event_time_utc'], time_quality=timing['status'],
            source_segment=candidate['segment_id'], target_start_sample=candidate['estimated_start_sample'],
            timing=timing, prediction_human_confirmed=False,
            software_time_policy_enabled=self.allow_software_time)
        try:
            self.metadata.write(json.dumps(metadata, ensure_ascii=False, allow_nan=False)+'\n')
            self.metadata.flush()
            os.fsync(self.metadata.fileno())
            self.journal.save(event)
        except BaseException:
            self.failed = True
            raise
        return event

    def pump(self):
        if self.failed:
            raise ValueError('Automatic label writer failed; do not submit more labels')
        return self.replay.pump()

    def close(self, seconds=5):
        deadline = time.perf_counter()+seconds
        try:
            while not self.failed and time.perf_counter() < deadline:
                self.pump()
                if self.replay.submitted == self.journal.saved and not self.uploader.snapshot():
                    break
                time.sleep(.02)
        finally:
            self.replay.close()
            try:
                pending = self.uploader.close(0)
            finally:
                self.metadata.close()
                self.journal.close()
        return dict(saved=self.journal.saved, submitted=self.replay.submitted,
            unread_saved=self.journal.saved-self.replay.submitted,
            pending_event_ids=[event['event_id'] for event in pending],
            all_acknowledged=(not self.failed and self.replay.submitted==self.journal.saved and not pending),
            software_time_policy_enabled=self.allow_software_time,
            meaning='ACK means label committed, not clip generated or time precision verified')

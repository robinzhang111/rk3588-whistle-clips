"""Automatic label boundary and one-at-a-time replay of durable JSONL."""
import os
import json
from pathlib import Path

import eventlink as link


def candidate_event(candidate, timing, session, event_id, allow_software_time=False):
    sample = candidate.get('estimated_start_sample')
    permitted = timing.get('status') == 'verified_sampling'
    if allow_software_time is True and timing.get('status') == 'software_estimate':
        permitted = (timing.get('time_quality') == 'software_estimate'
                     and timing.get('sampling_utc_verified') is False)
    if (isinstance(sample, bool) or not isinstance(sample, int) or sample < 0
            or not isinstance(candidate.get('segment_id'), str) or not candidate['segment_id']
            or not permitted or timing.get('reason') is not None
            or timing.get('synthetic_utc') is not None or not timing.get('estimated_utc')
            or not timing.get('anchor_evidence') or not timing.get('relation_evidence')
            or candidate.get('segment_id') != timing.get('segment_id')
            or candidate.get('estimated_start_sample') != timing.get('target_start_sample')):
        raise ValueError('Verified matching sampling time required')
    event = dict(session_id=session, event_id=event_id, event_type='whistle',
                 event_time_utc=timing['estimated_utc'])
    link.validate(event, session)
    return event


class EventJournal:
    """New session only; failed writes poison the writer, preserving the scene."""
    def __init__(self, path, session):
        self.path, self.session = Path(path), session
        if not isinstance(session, str) or not session.strip():
            raise ValueError('Session required')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('x', encoding='utf-8')
        self.saved = 0
        self.failed = False

    def save(self, event):
        if self.failed:
            raise ValueError('Journal failed; do not append or replay without inspection')
        link.validate(event, self.session)
        if event['event_id'] != self.saved + 1:
            raise ValueError('New-session IDs must be consecutive')
        line = link.canonical(event)+'\n'
        if len(line.encode('utf-8')) > 4096:
            raise ValueError('Saved label exceeds transport limit')
        try:
            self.stream.write(line)
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except Exception:
            self.failed = True
            raise
        self.saved += 1

    def close(self):
        self.stream.close()


class DurableReplay:
    """Exclusive owner of Uploader submissions; at most one outstanding label.

    Call pump regularly outside the audio callback. For an active journal supply
    available=lambda: journal.saved; closed-file replay validates the whole file
    first without loading all events. No durable ACK cursor: restart replays all.
    """
    def __init__(self, path, session, uploader, available=None):
        self.session, self.uploader, self.available = session, uploader, available
        self.read = self.submitted = 0
        self.eof = False
        if available is None:
            with Path(path).open(encoding='utf-8') as source:
                expected = 1
                while True:
                    line = source.readline(4097)
                    if not line:
                        break
                    if len(line.encode('utf-8')) > 4096 or not line.endswith('\n'):
                        raise ValueError('Oversized or incomplete saved label')
                    event = self._event(line)
                    if event['event_id'] != expected:
                        raise ValueError('Replay IDs must be consecutive')
                    expected += 1
        self.stream = Path(path).open(encoding='utf-8')

    def _event(self, line):
        event = json.loads(line)
        link.validate(event, self.session)
        return event

    def pump(self):
        if self.uploader.snapshot():
            return 'pending'
        if self.available is not None and self.read >= self.available():
            return 'caught_up'
        line = self.stream.readline(4097)
        if not line:
            self.eof = True
            return 'eof'
        if len(line.encode('utf-8')) > 4096 or not line.endswith('\n'):
            raise ValueError('Oversized or incomplete saved label')
        event = self._event(line)
        if event['event_id'] != self.read + 1:
            raise ValueError('Replay IDs must be consecutive')
        self.uploader.submit(event)
        self.read += 1
        self.submitted += 1
        return 'submitted'

    def close(self):
        self.stream.close()

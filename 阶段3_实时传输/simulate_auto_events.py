"""Explicit synthetic D session; loopback receiver, no sampling UTC promotion."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

import eventlink as link
from auto_events import EventJournal, DurableReplay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    link.stage1_path(Path(__file__).absolute().parent.parent/'阶段1_离线手动触发')
    args.output.mkdir(parents=True, exist_ok=False)
    session = 'synthetic-D-20000101'
    journal = EventJournal(args.output/'events.jsonl', session)
    events = [dict(session_id=session, event_id=i, event_type='whistle',
                   event_time_utc='2000-01-01T00:00:00Z') for i in range(1, 4)]
    try:
        for event in events:
            journal.save(event)
    finally:
        journal.close()
    protected = hashlib.sha256(journal.path.read_bytes()).hexdigest()
    store = link.Store(args.output/'labels.sqlite3', session)
    http = link.server(('127.0.0.1', 0), store)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:%d/events' % http.server_port
    original_send = link.send
    attempts = []
    max_pending = 0
    def send(url, event, timeout):
        attempts.append(event['event_id'])
        if len(attempts) == 1:
            raise OSError('synthetic disconnection before save')
        ack = original_send(url, event, timeout)
        if len(attempts) == 2:
            raise OSError('synthetic ACK loss after committed save')
        return ack
    try:
        with patch.object(link, 'send', side_effect=send):
            # New sender instances replay all saved labels; receiver deduplicates.
            for _ in range(2):
                uploader = link.Uploader(url, timeout=.5, retry=.05)
                replay = DurableReplay(journal.path, session, uploader)
                try:
                    deadline = time.perf_counter()+5
                    while time.perf_counter() < deadline:
                        state = replay.pump()
                        max_pending = max(max_pending, len(uploader.snapshot()))
                        if state == 'eof':
                            break
                        time.sleep(.01)
                    else:
                        raise RuntimeError('Synthetic replay timed out')
                finally:
                    replay.close()
                    pending = uploader.close(1)
                assert not pending and not uploader.thread.is_alive()
        saved = [json.loads(row[0]) for row in store.db.execute('SELECT payload FROM events ORDER BY id')]
        assert saved == events and max_pending <= 1
        assert hashlib.sha256(journal.path.read_bytes()).hexdigest() == protected
        report = dict(status='passed', synthetic_session=session, receiver_bind='127.0.0.1',
                      events=3, sqlite_rows=len(saved), fields_equal=True, max_pending=max_pending,
                      attempts=attempts, disconnect_injected=True, ack_loss_injected=True,
                      sender_reopened_replay=True, original_events_unchanged=True,
                      sampling_anchor_used=False, formal_real_event_upload=False,
                      audio_saved=False, event_file_sha256=protected)
        with (args.output/'summary.json').open('x', encoding='utf-8') as output:
            json.dump(report, output, indent=2)
        print(json.dumps(report))
    finally:
        http.shutdown()
        http.server_close()
        thread.join(2)
        store.db.close()


if __name__ == '__main__':
    main()

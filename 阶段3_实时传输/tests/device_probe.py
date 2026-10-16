"""Explicit synthetic board probe; invoked by PC in separate controlled steps."""
import argparse
import json
from pathlib import Path
import socket
import sys
import time
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eventlink as link

parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=("normal", "outage", "final"), required=True)
parser.add_argument("--events", required=True)
parser.add_argument("--url", required=True)
parser.add_argument("--stage1", required=True)
args = parser.parse_args()
link.stage1_path(args.stage1)
from basketclip.storage import load_events
events = load_events(args.events)
result = {"mode": args.mode, "source": "synthetic terminal m input, not physical REC"}
if args.mode == "normal":
    acks = [link.send(args.url, e, 2) for e in events[:2]]
    assert link.send(args.url, events[0], 2) == acks[0]
    result["acks"] = acks
elif args.mode == "outage":
    uploader = link.Uploader(args.url, timeout=1, retry=0.3)
    uploader.submit(events[2])
    time.sleep(0.5)
    assert uploader.snapshot() == [events[2]], "receiver must be offline for this check"
    print("OUTAGE_PENDING_VERIFIED", flush=True)
    assert not uploader.close(10), "retry did not recover"
    result["recovered"] = True
else:
    # Close before reading any response: saved-but-ACK-unobserved case.
    target = urllib.parse.urlsplit(args.url)
    body = link.canonical(events[3]).encode("utf-8")
    with socket.create_connection((target.hostname, target.port), timeout=2) as sock:
        headers = ("POST /events HTTP/1.1\r\nHost: %s\r\nContent-Length: %d\r\n"
                   "Content-Type: application/json\r\nConnection: close\r\n\r\n" %
                   (target.hostname, len(body))).encode("ascii")
        sock.sendall(headers + body)
        sock.shutdown(socket.SHUT_WR)
        # No recv call, so sender cannot claim ACK received.
    time.sleep(1)
    print("ACK_NOT_READ_EVENT_4", flush=True)
    # PC coordinator verifies persisted event 4 before allowing retry.
    assert sys.stdin.readline().strip() == "continue"
    result["retry_ack"] = link.send(args.url, events[3], 2)
    time.sleep(1)
    result["delayed_ack"] = link.send(args.url, events[4], 2)
    bad = [dict(events[0], event_type="whistle"), dict(events[0], session_id="wrong"),
           dict(events[0], event_id=True), dict(events[0], event_time_utc="bad")]
    rejected = 0
    for e in bad:
        try:
            link.send(args.url, e, 2)
        except link.Rejected:
            rejected += 1
    assert rejected == len(bad)
    result["rejected"] = rejected
    result["duplicate_ack"] = link.send(args.url, events[0], 2)
print(json.dumps(result, ensure_ascii=False), flush=True)

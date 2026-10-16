"""PC orchestration of synthetic RK3588-to-PC HTTP acceptance; no network config changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eventlink as link

parser = argparse.ArgumentParser()
parser.add_argument("--key", required=True)
parser.add_argument("--host", required=True)
parser.add_argument("--bind", required=True)
parser.add_argument("--port", required=True, type=int)
parser.add_argument("--session", required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
link.stage1_path(root.parent / "阶段1_离线手动触发")
out = root / "outputs" / args.session
out.mkdir(parents=True, exist_ok=False)
ssh = ["ssh", "-i", args.key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
       "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", args.host]
remote = "/home/basketclip-project/stage3"
source = remote + "/data/" + args.session + "/events.jsonl"
url = "http://%s:%d/events" % (args.bind, args.port)
env = dict(os.environ, PYTHONIOENCODING="utf-8")
logs = []
def run(command, input=None):
    logs.append(command)
    p = subprocess.run(ssh + [command], input=input, capture_output=True, text=True,
                       encoding="utf-8", timeout=20, env=env)
    logs.append(p.stdout + p.stderr)
    print(p.stdout + p.stderr, flush=True)
    if p.returncode:
        raise RuntimeError("SSH command failed: %d" % p.returncode)
    return p.stdout
def probe(mode):
    return "/usr/bin/python3 %s/tests/device_probe.py --stage1 /home/basketclip-project/stage1 --mode %s --events %s --url %s" % (
        remote, mode, shlex.quote(source), shlex.quote(url))

store = link.Store(out / "labels.sqlite3", args.session)
http = None
thread = None
def start():
    global http, thread
    http = link.server((args.bind, args.port), store)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
def stop():
    global http
    if http:
        http.shutdown()
        http.server_close()
        thread.join(2)
        http = None
result = {"session": args.session, "source": "synthetic terminal inputs", "passed": False}
try:
    start()
    run("cd /home/basketclip-project/stage1 && /usr/bin/python3 -m basketclip record --session %s --output %s" %
        (shlex.quote(args.session), shlex.quote(source)), "m\nm\nm\nm\nm\nq\n")
    run(probe("normal"))
    stop()
    command = probe("outage")
    logs.append(command)
    p = subprocess.Popen(ssh + [command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", env=env)
    # Wait for explicit sender pending evidence, not a guessed network state.
    while True:
        line = p.stdout.readline()
        logs.append(line)
        print(line, end="", flush=True)
        if "OUTAGE_PENDING_VERIFIED" in line:
            break
        if not line:
            raise RuntimeError("outage probe ended before pending evidence")
    start()
    tail = p.communicate(timeout=15)[0]
    logs.append(tail)
    print(tail, flush=True)
    assert p.returncode == 0
    command = probe("final")
    logs.append(command)
    p = subprocess.Popen(ssh + [command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", env=env)
    while True:
        line = p.stdout.readline()
        logs.append(line)
        print(line, end="", flush=True)
        if "ACK_NOT_READ_EVENT_4" in line:
            break
        if not line:
            raise RuntimeError("ACK-loss probe ended before marker")
    with sqlite3.connect(str(out / "labels.sqlite3")) as db:
        assert db.execute("SELECT count(*) FROM events WHERE id=4").fetchone()[0] == 1
    result["saved_before_ack_retry"] = True
    tail = p.communicate("continue\n", timeout=15)[0]
    logs.append(tail)
    print(tail, flush=True)
    assert p.returncode == 0
    downloaded = run("cat " + shlex.quote(source))
    (out / "board_events.jsonl").write_text(downloaded, encoding="utf-8")
    board = [json.loads(line) for line in downloaded.splitlines() if line.strip()]
    rows = store.db.execute("SELECT payload,received FROM events ORDER BY id").fetchall()
    received = [json.loads(row[0]) for row in rows]
    assert len(board) == len(received) == 5
    assert board == received
    result.update(passed=True, count=5, exact_fields_equal=True,
                  received_times=[row[1] for row in rows],
                  source_sha256=hashlib.sha256(downloaded.encode("utf-8")).hexdigest())
    (out / "received_events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in received), encoding="utf-8")
except Exception as error:
    result["error"] = str(error)
    raise
finally:
    stop()
    store.db.close()
    (out / "commands.log").write_text("\n".join(logs), encoding="utf-8")
    (out / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

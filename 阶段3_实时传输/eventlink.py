"""Label-only HTTP transport. Uses stage1 recorder; no media dependencies."""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import queue
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request


def stage1_path(path):
    sys.path.insert(0, str(Path(path).resolve()))


def canonical(event):
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def validate(event, session):
    from basketclip.timing import utc_time
    fields = {"session_id", "event_id", "event_type", "event_time_utc"}
    if not isinstance(event, dict) or set(event) != fields:
        raise ValueError("必须且仅包含阶段一的四个事件字段")
    if not isinstance(event["session_id"], str) or not event["session_id"].strip():
        raise ValueError("会话标识不能为空")
    if event["session_id"] != session:
        raise ValueError("会话不匹配")
    value = event["event_id"]
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 9223372036854775807:
        raise ValueError("event_id 必须是 SQLite 可保存的正整数")
    if event["event_type"] not in ("manual_trigger", "whistle"):
        raise ValueError("事件类型无效")
    utc_time(event["event_time_utc"])
    canonical(event)


def receipt(event, received):
    return {"status": "label_saved", "session_id": event["session_id"],
            "event_id": event["event_id"], "received_time_utc": received,
            "event_sha256": hashlib.sha256(canonical(event).encode("utf-8")).hexdigest()}


class Store:
    def __init__(self, path, session):
        self.session = session
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (session TEXT, id INTEGER, "
                        "payload TEXT NOT NULL, received TEXT NOT NULL, PRIMARY KEY(session,id))")
        self.db.commit()

    def save(self, event):
        validate(event, self.session)
        payload = canonical(event)
        with self.db:
            old = self.db.execute("SELECT payload,received FROM events WHERE session=? AND id=?",
                                  (self.session, event["event_id"])).fetchone()
            if old:
                if old[0] != payload:
                    raise ValueError("同一会话与编号内容冲突")
                received = old[1]
            else:
                received = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                self.db.execute("INSERT INTO events VALUES (?,?,?,?)",
                                (self.session, event["event_id"], payload, received))
        # Return only after transaction commit, including retries.
        return receipt(event, received)


def server(address, store):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.connection.settimeout(3)
            code = 200
            try:
                if self.path != "/events":
                    raise ValueError("仅支持 /events")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("消息长度必须在 1～4096 字节")
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("消息不完整")
                result = store.save(json.loads(body.decode("utf-8")))
            except (ValueError, TypeError, KeyError, UnicodeError) as error:
                code, result = 400, {"error": str(error)}
            except (OSError, sqlite3.Error) as error:
                code, result = 503, {"error": str(error)}
            data = json.dumps(result, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                pass  # Saved label remains available when ACK delivery fails.
    return HTTPServer(address, Handler)


class Rejected(Exception):
    pass


def send(url, event, timeout):
    request = urllib.request.Request(url, canonical(event).encode("utf-8"),
                                     {"Content-Type": "application/json"})
    # Local board-to-PC communication must not go through environment proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            ack = json.loads(response.read(4096))
    except urllib.error.HTTPError as error:
        if 400 <= error.code < 500:
            raise Rejected(error.read(4096).decode("utf-8", errors="replace"))
        raise
    expected = receipt(event, ack.get("received_time_utc"))
    if ack != expected or not isinstance(ack["received_time_utc"], str):
        raise OSError("确认内容无效，保留待确认事件")
    return ack


class Uploader:
    def __init__(self, url, timeout=2, retry=1):
        self.url, self.timeout, self.retry = url, timeout, retry
        self.items = queue.Queue()
        self.stop = threading.Event()
        self.pending = {}
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def submit(self, event):
        with self.lock:
            self.pending[(event["session_id"], event["event_id"])] = event
        self.items.put(event)

    def snapshot(self):
        with self.lock:
            return list(self.pending.values())

    def run(self):
        while not self.stop.is_set():
            try:
                event = self.items.get(timeout=0.1)
            except queue.Empty:
                continue
            while not self.stop.is_set():
                try:
                    ack = send(self.url, event, self.timeout)
                    print(json.dumps(ack, ensure_ascii=False), flush=True)
                    with self.lock:
                        self.pending.pop((event["session_id"], event["event_id"]), None)
                    break
                except Rejected as error:
                    print("拒绝，保留本地待处理标签：" + str(error), file=sys.stderr, flush=True)
                    break
                except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
                    print("待确认，将重试：" + str(error), file=sys.stderr, flush=True)
                    self.stop.wait(self.retry)

    def close(self, seconds):
        deadline = time.monotonic() + seconds
        while self.snapshot() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.stop.set()
        self.thread.join(self.timeout + 1)
        pending = self.snapshot()
        print(json.dumps({"pending_event_ids": [e["event_id"] for e in pending],
                          "note": "确认仅表示标签保存；退出后可用 upload 重发完整本地事件文件"},
                         ensure_ascii=False), flush=True)
        return pending


def main():
    parser = argparse.ArgumentParser(description="阶段三事件标签通信")
    parser.add_argument("--stage1", required=True, help="阶段一源码目录")
    commands = parser.add_subparsers(dest="command", required=True)
    receiver = commands.add_parser("receive")
    receiver.add_argument("--bind", required=True)
    receiver.add_argument("--port", required=True, type=int)
    receiver.add_argument("--session", required=True)
    receiver.add_argument("--database", required=True)
    for name in ("record", "upload"):
        sender = commands.add_parser(name)
        sender.add_argument("--url", required=True)
        sender.add_argument("--session", required=True)
        sender.add_argument("--events", required=True)
        sender.add_argument("--drain-seconds", type=float, default=5)
        if name == "record":
            sender.add_argument("--board-button", action="store_true")
    args = parser.parse_args()
    stage1_path(args.stage1)
    if not args.session.strip():
        parser.error("会话不能为空")
    try:
        if args.command == "receive":
            store = Store(args.database, args.session)
            http = server((args.bind, args.port), store)
            print("标签接收：%s:%s 会话 %s" % (args.bind, args.port, args.session), flush=True)
            try:
                http.serve_forever()
            finally:
                http.server_close()
                store.db.close()
        else:
            if args.drain_seconds < 0:
                parser.error("等待时间不能为负")
            uploader = Uploader(args.url)
            try:
                if args.command == "record":
                    from basketclip.cli import record
                    args.output = args.events
                    record(args, on_saved=uploader.submit)
                else:
                    from basketclip.storage import load_events
                    events = load_events(args.events)
                    for event in events:
                        validate(event, args.session)
                    for event in events:
                        uploader.submit(event)
            except KeyboardInterrupt:
                print("记录已停止，等待在途确认", flush=True)
            finally:
                pending = uploader.close(args.drain_seconds)
            return 1 if pending else 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print("错误：" + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Short user-started playback observations beside the existing label receiver."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT/'阶段3_实时传输'))
import eventlink


def validate_observation(row):
    kinds = {'begin', 'sample', 'playing', 'waiting', 'stalled', 'seeking', 'seeked',
             'ratechange', 'pause', 'ended', 'limit_stop', 'user_stop', 'media_error',
             'play_error', 'page_closed'}
    if not isinstance(row, dict) or row.get('kind') not in kinds:
        raise ValueError('Unknown playback observation')
    for key in ('wall_ms', 'performance_ms', 'media_seconds', 'rate'):
        value = row.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('Invalid '+key)
    if row['media_seconds'] > 11 or row['rate'] != 1:
        raise ValueError('Only the first ten seconds at unit rate are allowed')
    if type(row.get('paused')) is not bool or type(row.get('ready_state')) is not int or not 0 <= row['ready_state'] <= 4:
        raise ValueError('Invalid player state')
    if 'sequence' in row and (type(row['sequence']) is not int or row['sequence'] < 1):
        raise ValueError('Invalid observation sequence')


def playback_server(address, preview, output, stop, session=None):
    page = Path(__file__).with_suffix('.html').read_bytes()
    log = (output/'playback-observations.jsonl').open('x', encoding='utf-8')
    count = [0]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/session-status':
                body, mime = json.dumps(dict(session=session, output=str(output.absolute()),
                    playback_records=count[0], stopping=stop.is_set())).encode(), 'application/json'
            elif self.path == '/':
                body, mime = page, 'text/html; charset=utf-8'
            elif self.path == '/preview.mp4':
                body, mime = preview.read_bytes(), 'video/mp4'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.connection.settimeout(3)
            try:
                if self.path == '/finish-session':
                    stop.set()
                    self.send_response(204)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if self.path != '/playback' or count[0] >= 1000:
                    raise ValueError('Unsupported request or observation limit reached')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('Invalid message length')
                row = json.loads(self.rfile.read(length).decode('utf-8'))
                validate_observation(row)
                record = dict(observation=row, received_utc=datetime.now(timezone.utc).isoformat(),
                              received_monotonic_ns=time.perf_counter_ns(),
                              time_quality='software_playback_observation',
                              audio_output_latency_seconds=None, session_id=session)
                log.write(json.dumps(record, allow_nan=False)+'\n')
                log.flush()
                os.fsync(log.fileno())
                count[0] += 1
            except (ValueError, TypeError, UnicodeError) as error:
                self.send_error(400, str(error))
                return
            except OSError:
                self.send_error(503, 'Observation could not be saved')
                return
            self.send_response(204)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *_):
            pass

    try:
        return HTTPServer(address, Handler), log
    except BaseException:
        log.close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--preview', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--open-browser', action='store_true')
    args = parser.parse_args(argv)
    if not args.preview.is_file():
        parser.error('Prepared playback preview required')
    args.output.mkdir(parents=True, exist_ok=False)
    eventlink.stage1_path(ROOT/'阶段1_离线手动触发')
    store = eventlink.Store(args.output/'labels.sqlite3', args.session)
    receiver = player = log = None
    thread = None
    timer = threading.Event()
    close_reason = 'startup_error'
    try:
        receiver = eventlink.server((args.bind, 8765), store)
        player, log = playback_server(('127.0.0.1', 8766), args.preview, args.output, timer, args.session)
        receiver.timeout = .5
        player.timeout = .5
        def receive():
            while not timer.is_set():
                receiver.handle_request()
        thread = threading.Thread(target=receive)
        thread.start()
        (args.output/'service-ready.json').write_text(json.dumps(dict(
            pid=os.getpid(), session=args.session, started_utc=datetime.now(timezone.utc).isoformat(),
            lifetime_seconds=900, video_bind='127.0.0.1:8766', label_bind=args.bind+':8765'
        )), encoding='utf-8')
        print('READY http://127.0.0.1:8766/; session='+args.session, flush=True)
        if args.open_browser:
            import webbrowser
            webbrowser.open('http://127.0.0.1:8766/')
        deadline = time.perf_counter()+900
        close_reason = 'service_error'
        while not timer.is_set() and time.perf_counter() < deadline:
            player.handle_request()
        close_reason = 'user_requested_stop' if timer.is_set() else 'expired_after_900_seconds'
    except KeyboardInterrupt:
        close_reason = 'keyboard_interrupt'
    finally:
        timer.set()
        if thread is not None:
            thread.join()
        for server in (player, receiver):
            if server is not None:
                server.server_close()
        if log is not None:
            log.close()
        with (args.output/'received-events.jsonl').open('x', encoding='utf-8') as saved:
            for row in store.db.execute('SELECT payload FROM events ORDER BY id'):
                saved.write(row[0]+'\n')
        store.db.close()
        (args.output/'service-closed.json').write_text(json.dumps(dict(
            pid=os.getpid(), session=args.session, close_reason=close_reason,
            closed_utc=datetime.now(timezone.utc).isoformat(),
            meaning='Service lifecycle only; not label ACK, clip generation or human acceptance'
        )), encoding='utf-8')
        print('CLOSED; labels and playback observations preserved', flush=True)


if __name__ == '__main__':
    main()

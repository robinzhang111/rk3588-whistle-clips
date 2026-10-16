import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from controlled_playback import playback_server, validate_observation, eventlink, ROOT, main


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(kind='playing', wall_ms=1000, performance_ms=5,
                        media_seconds=.1, rate=1, paused=False, ready_state=4)

    def test_invalid_or_outside_short_playback_rejected(self):
        validate_observation(self.row)
        for change in (dict(rate=2), dict(media_seconds=12), dict(wall_ms=True),
                       dict(performance_ms=float('nan')), dict(kind='unknown'),
                       dict(paused=0), dict(ready_state=5)):
            with self.assertRaises(ValueError):
                validate_observation(dict(self.row, **change))

    def test_http_ack_follows_persist_and_preserves_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            preview = directory/'preview.mp4'
            preview.write_bytes(b'fixture media only')
            stop = threading.Event()
            server, log = playback_server(('127.0.0.1', 0), preview, directory, stop, 'fixture-session')
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            address = 'http://127.0.0.1:%d' % server.server_port
            request = urllib.request.Request(address+'/playback', json.dumps(self.row).encode())
            try:
                with opener.open(address+'/session-status') as response:
                    state=json.load(response)
                    self.assertEqual(state['session'], 'fixture-session')
                    self.assertEqual(state['output'], str(directory.absolute()))
                with opener.open(address+'/preview.mp4') as response:
                    self.assertEqual(response.read(), preview.read_bytes())
                with opener.open(request) as response:
                    self.assertEqual(response.status, 204)
                    saved = json.loads((directory/'playback-observations.jsonl').read_text())
                    self.assertEqual(saved['observation'], self.row)
                    self.assertIsNone(saved['audio_output_latency_seconds'])
                with patch('os.fsync', side_effect=OSError('disk failure')):
                    with self.assertRaises(urllib.error.HTTPError) as failure:
                        opener.open(request)
                    self.assertEqual(failure.exception.code, 503)
                bad = urllib.request.Request(address+'/playback', b'{"kind":"unknown"}')
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    opener.open(bad)
                self.assertEqual(failure.exception.code, 400)
                request = urllib.request.Request(address+'/finish-session', b'')
                with opener.open(request) as response:
                    self.assertEqual(response.status, 204)
                self.assertTrue(stop.is_set())
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
                log.close()

    def test_lan_receiver_has_no_video_or_playback_routes(self):
        eventlink.stage1_path(ROOT/'阶段1_离线手动触发')
        with tempfile.TemporaryDirectory() as tmp:
            store = eventlink.Store(Path(tmp)/'labels.sqlite3', 'fixture-session')
            server = eventlink.server(('127.0.0.1', 0), store)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            address = 'http://127.0.0.1:%d' % server.server_port
            try:
                for path in ('/', '/preview.mp4', '/playback-observations.jsonl'):
                    with self.assertRaises(urllib.error.HTTPError) as failure:
                        opener.open(address+path)
                    self.assertEqual(failure.exception.code, 501)
                request = urllib.request.Request(address+'/playback', json.dumps(self.row).encode())
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    opener.open(request)
                self.assertEqual(failure.exception.code, 400)
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
                store.db.close()

    def test_browser_opens_after_ready_and_normal_close_keeps_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            preview=directory/'preview.mp4'
            preview.write_bytes(b'fixture')
            output=directory/'pc'
            original=eventlink.server
            def player(address, preview, output, stop, session):
                server, log=playback_server(('127.0.0.1', 0), preview, output, stop, session)
                server.handle_request=stop.set
                return server, log
            def browser(url):
                ready=json.loads((output/'service-ready.json').read_text())
                self.assertEqual(ready['session'], 'fixture-session')
                self.assertEqual(url, 'http://127.0.0.1:8766/')
            with patch('controlled_playback.eventlink.server', side_effect=lambda address, store:original(('127.0.0.1', 0), store)), \
                    patch('controlled_playback.playback_server', side_effect=player), \
                    patch('webbrowser.open', side_effect=browser) as opened:
                main(['--bind', '127.0.0.1', '--session', 'fixture-session', '--preview', str(preview),
                      '--output', str(output), '--open-browser'])
            opened.assert_called_once()
            self.assertEqual(json.loads((output/'service-closed.json').read_text())['close_reason'], 'user_requested_stop')
            self.assertTrue((output/'received-events.jsonl').exists())

    def test_startup_failure_is_logged_as_failure_not_normal_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            preview=directory/'preview.mp4'
            preview.write_bytes(b'fixture')
            output=directory/'pc'
            with patch('controlled_playback.eventlink.server', side_effect=OSError('occupied port')), \
                    patch('webbrowser.open') as opened:
                with self.assertRaises(OSError):
                    main(['--bind', '127.0.0.1', '--session', 'fixture-session', '--preview', str(preview),
                          '--output', str(output), '--open-browser'])
            opened.assert_not_called()
            self.assertFalse((output/'service-ready.json').exists())
            self.assertEqual(json.loads((output/'service-closed.json').read_text())['close_reason'], 'startup_error')


if __name__ == '__main__':
    unittest.main()

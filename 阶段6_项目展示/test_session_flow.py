"""Delivery contracts; fixtures are software evidence, never a new capture."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import session_flow as flow


class SessionFlowTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        for i in range(12):
            row = dict(kind='begin' if i==0 else 'playing' if i==1 else 'user_stop' if i==11 else 'sample',
                sequence=i+1, wall_ms=1000000+max(0, i-1)*100,
                performance_ms=100+max(0, i-1)*100, media_seconds=max(0, i-1)*.1,
                rate=1, paused=i in (0, 11), ready_state=4)
            self.rows.append(dict(observation=row, session_id='fixture',
                                  time_quality='software_playback_observation'))
        self.info = dict(format=dict(start_time='0'), streams=[dict(codec_type='video', start_time='0')])

    def mapping(self, rows):
        with patch.object(flow, 'sha', return_value='a'*64), patch.object(flow, 'inspect_video', return_value=(self.info, 10)):
            return flow.playback_mapping(rows, 'fixture', Path('video'), Path('preview'), Path('observations'))

    def test_continuous_interval_uses_observed_zero_and_unknown_total_error(self):
        mapping, check = self.mapping(self.rows)
        self.assertEqual(mapping['intervals'][0]['video_end'], 1)
        self.assertIsNone(mapping['total_uncertainty_seconds'])
        self.assertFalse(mapping['real_capture_utc_verified'])
        self.assertLess(check['max_internal_residual_seconds'], 1e-6)

    def test_pause_seek_wait_stall_rate_and_missing_stop_rejected(self):
        for kind in ('pause', 'seeking', 'seeked', 'waiting', 'stalled', 'ratechange', 'page_closed', 'playing'):
            rows = copy.deepcopy(self.rows)
            rows[5]['observation']['kind'] = kind
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.mapping(rows)
        with self.assertRaises(ValueError):
            self.mapping(self.rows[:-1])

    def test_missing_duplicate_out_of_order_and_wrong_session_rejected(self):
        for change in ('missing', 'duplicate', 'reorder', 'session', 'no_sequence'):
            rows = copy.deepcopy(self.rows)
            if change=='missing':
                del rows[5]
            elif change=='duplicate':
                rows.insert(5, copy.deepcopy(rows[5]))
            elif change=='reorder':
                rows[5], rows[6] = rows[6], rows[5]
            elif change=='session':
                rows[5]['session_id']='other'
            else:
                del rows[5]['observation']['sequence']
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.mapping(rows)

    def test_clock_frozen_media_unready_and_nonzero_origin_rejected(self):
        for field, value, index in (('wall_ms', 1, 5), ('performance_ms', 1, 5),
                ('wall_ms', 1000460, 5), ('media_seconds', .3, 5),
                ('ready_state', 2, 5), ('paused', True, 5), ('media_seconds', .01, 1)):
            rows = copy.deepcopy(self.rows)
            rows[index]['observation'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.mapping(rows)

    def test_prepare_rejects_existing_or_unsafe_session_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)/'fixture'
            directory.mkdir()
            for session in ('../bad', 'other'):
                with self.assertRaises(ValueError):
                    flow.prepare(directory, session, '127.0.0.1', Path('unused'))
            with patch.object(flow, 'inspect_video', return_value=(self.info, 10)), patch.object(flow, 'sha', return_value='a'*64):
                with self.assertRaises(FileExistsError):
                    flow.prepare(directory, 'fixture', '127.0.0.1', Path('unused'))
            self.assertEqual(list(directory.iterdir()), [])

    def test_export_readonly_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            with self.assertRaises(sqlite3.OperationalError):
                flow.pc_events(directory, 'fixture')
            self.assertFalse((directory/'pc').exists())
            store=flow.eventlink.Store(directory/'pc'/'labels.sqlite3', 'fixture')
            row=dict(session_id='fixture', event_id=1, event_type='whistle', event_time_utc='2026-10-15T00:00:00Z')
            store.save(row)
            store.db.close()
            self.assertEqual(flow.export(directory, 'fixture'), [row])
            with self.assertRaises(FileExistsError):
                flow.export(directory, 'fixture')
            with self.assertRaises(ValueError):
                flow.pc_events(directory, 'other')

    def test_empty_queue_and_closed_do_not_imply_delivery_or_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            (directory/'board-originals').mkdir()
            flow.save_json(directory/'board-originals'/'status.json', dict(session_id='fixture', status='closed',
                automatic_labels=dict(submitted=0, pending_event_ids=[], all_acknowledged=True)))
            state=flow.status(directory, dict(session='fixture'))
            self.assertFalse(state['pc_confirmed'])
            self.assertEqual(state['clips_generated'], 0)
            self.assertEqual(state['human_confirmation'], 'pending')
            self.assertFalse(state['full_product_accepted'])

    def test_finish_failure_preserves_diagnostics_and_blocks_retry_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'fixture'
            directory.mkdir()
            flow.save_json(directory/'session.json', dict(session='fixture'))
            (directory/'pc').mkdir()
            flow.save_json(directory/'pc'/'service-closed.json', {})
            store=flow.eventlink.Store(directory/'pc'/'labels.sqlite3', 'fixture')
            store.db.close()
            flow.save_json(directory/'download-verification.json', dict(matched=False, remote_sha256={}))
            with self.assertRaises(ValueError):
                flow.main(['finish', '--directory', str(directory)])
            self.assertTrue((directory/'failure.json').exists())
            self.assertTrue((directory/'finish.log').exists())
            self.assertFalse((directory/'playback-mapping.json').exists())
            with self.assertRaises(FileExistsError):
                flow.main(['finish', '--directory', str(directory)])

    def test_stop_refuses_different_live_session(self):
        class Response:
            def __enter__(self):
                from io import BytesIO
                return BytesIO(json.dumps(dict(session='other', output='other')).encode())
            def __exit__(self, *_):
                pass
        with patch.object(flow.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value=Response()
            with self.assertRaises(ValueError):
                flow.service_request(Path('fixture'), dict(session='fixture'), stop=True)
            self.assertEqual(opener.return_value.open.call_count, 1)

    def test_download_uses_only_explicit_read_operations_and_checks_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            def run(argv, **kwargs):
                if argv[0]=='scp':
                    target=Path(argv[-1])
                    if target.name=='console.log':
                        target.write_text('fixture console', encoding='utf-8')
                    else:
                        for name in flow.FILES:
                            (target/name).write_text(json.dumps(dict(session_id='fixture', status='closed')),
                                                     encoding='utf-8')
                    return None
                import hashlib
                digest=hashlib.sha256(json.dumps(dict(session_id='fixture', status='closed')).encode()).hexdigest()
                console=hashlib.sha256(b'fixture console').hexdigest()
                from types import SimpleNamespace
                return SimpleNamespace(stdout=''.join(digest+'  '+name+'\n' for name in flow.FILES)+
                    console+'  /board/fixture.console.log\n')
            with patch.object(flow.subprocess, 'run', side_effect=run) as calls:
                flow.download(directory, dict(session='fixture', board_output='/board/fixture'),
                              'user@fixture', Path('key-not-read'))
                self.assertEqual([c.args[0][0] for c in calls.call_args_list], ['ssh', 'scp', 'scp', 'ssh'])
                self.assertTrue(flow.load_json(directory/'download-verification.json')['matched'])
                with self.assertRaises(FileExistsError):
                    flow.download(directory, dict(session='fixture', board_output='/board/fixture'),
                                  'user@fixture', Path('key-not-read'))


if __name__=='__main__':
    unittest.main()

"""Local tests only. BoardSSH is replaced; no hardware connections."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import workbench as web
import board_console_agent as agent
from console_control import BoardSSH, Console, BoardOperationError


class FakeBoard:
    def __init__(self, *args):
        self.calls = []
        self.error = None
        self.ready = True
        self.status = 'running'
        self.pcm = 'RUNNING'
        self.wrong_session = False
        self.bad_copy = False
        self.files = {}

    def call(self, action, **data):
        self.calls.append((action, data))
        if self.error:
            raise self.error
        session = 'wrong' if self.wrong_session else data.get('session', 'trial')
        state = dict(session_id=session, status=self.status, resources_released=self.status=='closed',
                     automatic_labels=dict(saved=0, submitted=0, all_acknowledged=True))
        if action == 'probe':
            return dict(hostname='lubancat', architecture='aarch64', ready=self.ready)
        if action == 'list':
            return dict(sessions=['trial'])
        if action == 'check':
            return dict(session_id=session, ready=self.ready, environment={'ready':self.ready})
        if action == 'start':
            return dict(session_id=session, status='starting')
        if action == 'stop':
            return dict(session_id=session, status='stopping')
        if action == 'status':
            return dict(session_id=session, control=state, capture=state, pcm=self.pcm)
        if action == 'manifest':
            return dict(session_id=session, files={n:hashlib.sha256(b).hexdigest() for n,b in self.files.items()},
                        state=dict(control=state, capture=state))
        raise AssertionError(action)

    def copy(self, remote, local):
        name = remote.split('/trial/', 1)[1]
        local.write_bytes(b'corrupt' if self.bad_copy else self.files[name])


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = web.Workbench(self.root/'workspace', root=self.root)
        self.addCleanup(self.app.close)
        self.board = FakeBoard()
        self.app.console = Console(self.app, lambda *args: self.board)
        self.console = self.app.console
        self.job = dict(id='test', status='running')

    def op(self, operation, **data):
        self.console.execute(operation, dict(session='trial', **data), self.job)

    def connect(self):
        self.op('connect', host='boarduser@fixture', identity='fixture path only')

    def prepared(self):
        self.connect()
        self.op('new-session')
        self.op('check-ready')

    def test_default_restore_never_calls_ssh_and_marks_stale(self):
        self.assertFalse(self.board.calls)
        self.console.local['live'] = dict(state='running', stale=False)
        self.app.persist()
        restored = web.Workbench(self.app.output, root=self.root, resume=True)
        try:
            self.assertEqual(restored.console.local['connection'], 'disconnected')
            self.assertTrue(restored.console.local['live']['stale'])
            self.assertFalse(restored.console.local['ready'])
        finally:
            restored.close()
        self.assertFalse(self.board.calls)

    def test_disconnected_and_not_ready_block_start(self):
        for operation in ('refresh-board', 'start-capture', 'download'):
            with self.assertRaises(ValueError):
                self.op(operation, confirmed=True)
        self.connect()
        with self.assertRaises(ValueError):
            self.op('start-capture', confirmed=True)
        self.assertFalse(any(a=='start' for a,_ in self.board.calls))

    def test_connection_failure_visible(self):
        self.board.error = ValueError('SSH认证失败 fixture')
        with self.assertRaisesRegex(ValueError, '认证失败'):
            self.connect()
        self.assertEqual(self.console.local['connection'], 'failed')
        self.assertFalse(self.console.local['ready'])

    def test_existing_session_refused(self):
        self.op('new-session')
        with self.assertRaises(FileExistsError):
            self.op('new-session')

    def test_busy_and_mismatched_readiness_refuse_start(self):
        self.connect()
        self.op('new-session')
        self.board.ready = False
        with self.assertRaises(ValueError):
            self.op('check-ready')
        self.assertFalse(self.console.local['ready'])
        self.connect()
        self.board.ready = True
        self.board.wrong_session = True
        with self.assertRaisesRegex(ValueError, '身份不一致'):
            self.op('check-ready')
        self.assertFalse(self.console.local['ready'])

    def test_start_once_and_timeout_cannot_retry(self):
        self.prepared()
        with self.assertRaisesRegex(ValueError, '明确点击'):
            self.op('start-capture')
        self.board.error = TimeoutError('fixture disconnect after remote accepted')
        with self.assertRaises(TimeoutError):
            self.op('start-capture', confirmed=True)
        self.assertTrue((self.console.directory('trial')/'start-request.json').is_file())
        self.assertTrue(self.console.local['live']['stale'])
        self.board.error = None
        self.connect()
        self.op('check-ready')
        with self.assertRaisesRegex(ValueError, '已发出开始请求'):
            self.op('start-capture', confirmed=True)
        self.assertEqual(sum(a=='start' for a,_ in self.board.calls), 1)

    def test_disconnect_retains_unknown_running_state(self):
        self.prepared()
        self.op('start-capture', confirmed=True)
        self.op('refresh-board')
        self.op('disconnect')
        self.assertEqual(self.console.local['live']['state'], 'running')
        self.assertTrue(self.console.local['live']['stale'])
        self.assertFalse(self.console.local['ready'])

    def test_stop_timeout_closed_and_failed_are_distinct(self):
        self.connect()
        self.op('stop-capture')
        self.assertIn('stop_pending', self.console.local['live'])
        self.console.local['live']['stop_requested_at'] = time.time()-21
        self.op('refresh-board')
        self.assertIn('停止超时', self.console.local['live']['stop_pending'])
        self.board.status = 'closed'
        self.op('refresh-board')
        self.assertFalse(self.console.local['live']['resource_release_confirmed'])
        self.board.pcm = 'closed'
        self.op('refresh-board')
        self.assertTrue(self.console.local['live']['resource_release_confirmed'])
        self.assertIsNone(self.console.local['live']['label_ack'])
        self.board.status = 'failed'
        self.op('refresh-board')
        self.assertEqual(self.console.local['live']['state'], 'failed')

    def downloads(self):
        self.connect()
        self.board.files = {'capture/status.json': json.dumps(dict(session_id='trial', status='failed')).encode(),
                            'capture/candidates.relative.jsonl': b''}

    def test_independent_download_hash_identity_and_repeat_protection(self):
        self.downloads()
        self.op('download')
        result = self.console.local['downloads'][0]
        self.assertTrue(result['verified'])
        self.assertFalse(any(a in ('start','stop') for a,_ in self.board.calls))
        with self.assertRaisesRegex(ValueError, '已验证下载'):
            self.op('download')

    def test_corrupt_download_and_mismatched_rows_preserve_diagnostics(self):
        self.downloads()
        self.board.bad_copy = True
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.op('download')
        self.assertFalse(self.console.local['downloads'])
        reports = list(self.app.output.glob('downloads/*/download-verification.json'))
        self.assertEqual(web.load(reports[0])['status'], 'incomplete')
        self.board.bad_copy = False
        self.board.files['capture/candidates.relative.jsonl'] = b'{"session_id":"wrong"}\n'
        with self.assertRaisesRegex(ValueError, '候选会话不一致'):
            self.op('download')
        self.assertFalse(self.console.local['downloads'])

    def test_parallel_http_jobs_rejected_and_failure_saved(self):
        release = threading.Event()
        entered = threading.Event()
        def execute(*args):
            entered.set()
            release.wait(3)
            raise ValueError('fixture background failure')
        with patch.object(self.console, 'execute', side_effect=execute):
            self.app.submit('console', dict(operation='sessions'))
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError, '尚未完成'):
                self.app.submit('console', dict(operation='sessions'))
            release.set()
            self.app.pool.shutdown(wait=True)
        job = self.app.state['jobs'][-1]
        self.assertEqual(job['status'], 'failed')
        self.assertTrue((self.app.output/job['log']).exists())

    def test_online_start_requires_matching_ready_receiver(self):
        self.connect()
        self.op('new-session')
        with self.assertRaisesRegex(ValueError, '未READY'):
            self.op('check-ready', online=True, bind='192.0.2.10')
        self.assertFalse(any(a=='check' for a,_ in self.board.calls))

    def test_transport_errors_and_host_check(self):
        for text, expected in [('Permission denied (publickey)', '认证失败'),
                               ('Host key verification failed', '主机身份'), ('No route to host', 'SSH操作失败')]:
            def fail(*args, **kwargs):
                raise subprocess.CalledProcessError(255, args[0], stderr=text)
            ssh = BoardSSH('boarduser@fixture', str(self.root/'key'), run=fail)
            with self.assertRaisesRegex(ValueError, expected):
                ssh.call('probe')
            self.assertIn('StrictHostKeyChecking=yes', ssh.options)
        with self.assertRaises(ValueError):
            BoardSSH('boarduser@fixture;echo bad', str(self.root/'key'))

    def test_external_camera_video_is_explicitly_accepted(self):
        camera = self.root/'camera.mp4'
        camera.write_bytes(b'explicit media')
        self.assertEqual(self.app.video_path(str(camera)), camera.resolve())
        private = self.root/'private.txt'
        private.write_text('not video')
        with self.assertRaises(ValueError):
            self.app.video_path(str(private))

    def test_remote_adapter_reserves_once_and_only_cooperatively_stops(self):
        identity = type('Identity', (), {'machine':'aarch64'})()
        with patch.object(agent, 'BASE', self.root), patch.object(agent.socket, 'gethostname', return_value='lubancat'), \
                patch.object(agent.os, 'uname', return_value=identity, create=True), \
                patch.object(agent, 'runtime_check', return_value={}), \
                patch.object(agent, 'environment', return_value={'ready':True}), \
                patch.object(agent.subprocess, 'Popen') as spawn:
            (self.root/'outputs').mkdir()
            spawn.return_value.pid = 123
            result = agent.main('start', dict(session='trial'), '# source fixture')
            self.assertEqual(result['status'], 'starting')
            folder = self.root/'outputs/trial'
            self.assertTrue((folder/'request.json').is_file())
            with self.assertRaises(FileExistsError):
                agent.main('start', dict(session='trial'), '# source fixture')
            self.assertEqual(spawn.call_count, 1)
            (folder/'capture').mkdir()
            with patch.object(agent, 'snapshot', return_value=dict(capture={'status':'running'})):
                agent.main('stop', dict(session='trial'), '')
            self.assertTrue((folder/'capture/STOP').is_file())
            with patch.object(agent, 'snapshot', return_value=dict(
                    control={'status':'failed'}, capture={'status':'running'})):
                manifest = agent.main('manifest', dict(session='trial'), '')
                self.assertIn('request.json', manifest['files'])
            with patch.object(agent, 'snapshot', return_value=dict(
                    control={'status':'running'}, capture={'status':'running'})):
                with self.assertRaisesRegex(ValueError, 'terminal'):
                    agent.main('manifest', dict(session='trial'), '')

    def test_remote_adapter_busy_and_wrong_identity_never_spawn(self):
        identity = type('Identity', (), {'machine':'aarch64'})()
        with patch.object(agent.socket, 'gethostname', return_value='other'), \
                patch.object(agent.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'identity'):
                agent.main('probe', {}, '')
            spawn.assert_not_called()
        with patch.object(agent, 'BASE', self.root), patch.object(agent.socket, 'gethostname', return_value='lubancat'), \
                patch.object(agent.os, 'uname', return_value=identity, create=True), \
                patch.object(agent, 'environment', return_value={'ready':False}), \
                patch.object(agent.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'busy'):
                agent.main('start', dict(session='trial'), '')
            spawn.assert_not_called()
            self.assertFalse((self.root/'outputs/trial').exists())

    def test_changed_online_option_does_not_start(self):
        self.prepared()
        with self.assertRaisesRegex(ValueError, '选项已改变'):
            self.op('start-capture', confirmed=True, online=True)
        self.assertFalse(any(a=='start' for a,_ in self.board.calls))

    def test_business_refusal_preserves_connection_but_invalidates_ready(self):
        self.prepared()
        self.board.error = BoardOperationError('REC occupied')
        with self.assertRaises(BoardOperationError):
            self.op('check-ready')
        self.assertEqual(self.console.local['connection'], 'connected')
        self.assertTrue(self.app.state['hardware_connected'])
        self.assertFalse(self.console.local['ready'])

    def test_ssh_structured_refusal_is_not_transport_failure(self):
        def refuse(*args, **kwargs):
            raise subprocess.CalledProcessError(3, args[0],
                output=json.dumps({'console_error':'PCM, REC service or capture owner busy; inspect probe, do not force stop'}),
                stderr='')
        ssh = BoardSSH('boarduser@fixture', str(self.root/'key'), run=refuse)
        with self.assertRaisesRegex(BoardOperationError, 'SSH仍已连接'):
            ssh.call('check', session='trial')

    def test_empty_receiver_address_explained_before_ssh(self):
        self.prepared()
        with self.assertRaisesRegex(ValueError, '电脑接收IPv4'):
            self.op('check-ready', online=True, bind='')
        self.assertEqual(self.console.local['connection'], 'connected')

    def test_process_zero_download_without_video_or_repeated_entries(self):
        self.downloads()
        self.op('download')
        with patch.object(web, 'inspect_video') as inspect:
            self.op('prepare-result')
            self.op('prepare-result')
            inspect.assert_not_called()
        self.assertEqual(len(self.app.state['entries']), 1)
        entry = self.app.state['entries'][0]
        self.assertEqual(entry['candidate_count'], 0)
        self.assertIsNone(entry['video'])
        self.assertIsNone(entry['view']['label_ack'])
        self.assertFalse(entry['reviews'])
        with self.assertRaisesRegex(ValueError, '无候选'):
            self.app.plan(entry, {}, self.job)
        self.app.persist()
        resumed = web.Workbench(self.app.output, root=self.root, resume=True)
        try:
            restored = resumed.state['entries'][0]
            self.assertIsNone(restored['video'])
            self.assertEqual(restored['candidate_count'], 0)
            self.assertFalse(restored['reviews'])
            self.assertEqual(resumed.console.local['connection'], 'disconnected')
        finally:
            resumed.close()

    def test_process_nonempty_download_requires_new_video_and_basis(self):
        self.downloads()
        self.board.files['capture/candidates.relative.jsonl'] = json.dumps(dict(session_id='trial',
            candidate=dict(segment_id='trial:segment-1', estimated_start_sample=22050))).encode()+b'\n'
        self.op('download')
        self.op('prepare-result')
        self.assertEqual(self.console.local['processing']['candidate_count'], 1)
        self.assertFalse(self.app.state['entries'])
        with self.assertRaisesRegex(ValueError, '选择对应录像'):
            self.app.import_result(dict(session_dir=self.console.local['processing']['directory'], video=''), self.job)

    def test_processing_changed_download_refused_before_import(self):
        self.downloads()
        self.op('download')
        folder = Path(self.console.local['downloads'][0]['directory'])
        (folder/'capture/candidates.relative.jsonl').write_text('changed')
        with self.assertRaisesRegex(ValueError, '文件已变化'):
            self.op('prepare-result')
        self.assertFalse(self.app.state['entries'])


if __name__ == '__main__':
    unittest.main()

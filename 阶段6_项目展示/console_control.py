"""Local orchestration for explicit board operations and existing delivery tools."""
import base64
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import shlex
import subprocess
import sys
import threading
import time

import session_flow as flow
from workbench import capture_view, save_new, stamp, load, sha

IDENTITY = 'YOUR_SSH_IDENTITY_FILE'


class BoardOperationError(ValueError):
    """The authenticated board refused an operation; transport remains available."""


class BoardSSH:
    def __init__(self, host, identity, run=None):
        if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*', host):
            raise ValueError('需要明确的user@host')
        if not isinstance(identity, str) or not Path(identity).is_absolute():
            raise ValueError('需要电脑上的私钥绝对路径；不读取密钥内容')
        self.host, self.run = host, run or subprocess.run
        self.options = ['-i', identity, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
                        '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8',
                        '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2']

    def call(self, action, **data):
        source = Path(__file__).with_name('board_console_agent.py').read_text(encoding='utf-8')
        encoded = base64.b64encode(source.encode()).decode()
        bootstrap = "import base64;s=base64.b64decode(%r).decode();exec(compile(s,'console-adapter','exec'),dict(__name__='__main__',SCRIPT=s))" % encoded
        command = shlex.join([flow.PYTHON, '-B', '-c', bootstrap, action, json.dumps(data)])
        try:
            result = self.run(['ssh', *self.options, self.host, command], check=True,
                              capture_output=True, text=True, encoding='utf-8', timeout=45)
        except subprocess.CalledProcessError as error:
            try:
                refusal = json.loads((error.stdout or '').strip().splitlines()[-1])
            except (ValueError, IndexError):
                refusal = {}
            if refusal.get('console_error'):
                reason = refusal['console_error']
                if 'Unexpected board identity' in reason:
                    raise ValueError('板卡身份不匹配，请核查连接目标') from error
                if 'PCM, REC service or capture owner busy' in reason:
                    reason = '采集未就绪：REC服务或采集资源被占用。SSH仍已连接，请查看占用诊断；不要强杀进程。'
                elif 'Session exists' in reason or 'File exists' in reason:
                    reason = '板端会话已存在，请使用新编号，保留旧结果。'
                raise BoardOperationError(reason) from error
            diagnostic = str(error.stderr or '')
            if 'Permission denied' in diagnostic:
                raise ValueError('SSH认证失败：核对账户、密钥路径和授权。'+diagnostic[-1500:]) from error
            if 'HOST IDENTIFICATION' in diagnostic or 'Host key verification failed' in diagnostic:
                raise ValueError('SSH主机身份校验失败：请人工核对known_hosts，不关闭校验。'+diagnostic[-1500:]) from error
            raise ValueError('SSH操作失败：'+diagnostic[-3000:]) from error
        except subprocess.TimeoutExpired as error:
            raise ValueError('SSH超时：板端操作结果未知，请显式重连查询，勿重复开始') from error
        return json.loads(result.stdout.strip().splitlines()[-1])

    def copy(self, remote, local):
        self.run(['scp', *self.options, self.host+':'+remote, str(local)], check=True,
                 capture_output=True, text=True, encoding='utf-8', timeout=120)


class Console:
    def __init__(self, app, factory=BoardSSH):
        self.app, self.factory, self.ssh = app, factory, None
        self.receiver = None
        self.receiver_stop = threading.Event()
        self.receiver_thread = None
        self.pc_process = None
        self.pc_log = None
        self.local = app.state.setdefault('console', dict(connection='disconnected', sessions=[], downloads=[]))
        self.local.update(connection='disconnected', ready=False, receiver='unknown_after_restart')
        if self.local.get('live'):
            self.local['live']['stale'] = True
        self.selection = None

    def connected(self):
        if not self.ssh or self.local['connection'] != 'connected':
            raise ValueError('请先显式连接板卡')
        return self.ssh

    def remote(self, action, **data):
        try:
            value = self.connected().call(action, **data)
            if data.get('session') and value.get('session_id') != data['session']:
                raise ValueError('电脑与板端会话身份不一致')
            save_new(self.app.output/('board-query-'+stamp()+'.json'),
                     dict(operation=action, request=data, response=value, source='explicit SSH operation'))
            return value
        except BoardOperationError:
            self.local['ready'] = False
            raise
        except Exception:
            self.local.update(connection='unknown', ready=False)
            self.app.state['hardware_connected'] = None
            if self.local.get('live'):
                self.local['live']['stale'] = True
            raise

    def directory(self, session):
        if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', session):
            raise ValueError('会话编号只允许1～80位字母、数字、下划线、短横线')
        return self.app.output/'sessions'/session

    def execute(self, action, data, job):
        session = data.get('session', '')
        if action == 'connect':
            ssh = self.factory(data.get('host'), data.get('identity'))
            self.local.update(connection='connecting', ready=False)
            try:
                info = ssh.call('probe')
                if info.get('hostname') != 'lubancat' or info.get('architecture') != 'aarch64':
                    raise ValueError('板卡身份不匹配')
                self.ssh = ssh
                self.local.update(connection='connected', connected_host=data['host'], environment=info, ready=False)
                self.app.state['hardware_connected'] = True
                save_new(self.app.output/('board-identity-'+stamp()+'.json'), info)
            except Exception:
                self.ssh = None
                self.local['connection'] = 'failed'
                self.app.state['hardware_connected'] = False
                raise
        elif action == 'disconnect':
            self.ssh = None
            self.local.update(connection='disconnected', ready=False)
            self.app.state['hardware_connected'] = False
            if self.local.get('live'):
                self.local['live']['stale'] = True
        elif action == 'sessions':
            self.local['sessions'] = self.remote('list')['sessions']
        elif action == 'new-session':
            folder = self.directory(session)
            self.local['ready'] = False
            if any((self.app.root/stage/'outputs'/session).exists() for stage in
                   ('阶段1_离线手动触发', '阶段2_离线哨声检测', '阶段6_项目展示')):
                raise ValueError('本地已有同名会话，请使用新编号')
            folder.parent.mkdir(exist_ok=True)
            if data.get('playback'):
                video = self.app.video_path(data['video'])
                flow.prepare(folder, session, data['bind'], video)
            else:
                folder.mkdir(exist_ok=False)
                save_new(folder/'session.json', dict(session=session, board_output=flow.BOARD+'/outputs/'+session))
            self.local.update(selected=session, prepared=str(folder))
            self.selection = None
        elif action == 'check-ready':
            self.local['ready'] = False
            self.selection = None
            folder = self.directory(session)
            cfg = flow.config(folder)
            url = None
            if data.get('online'):
                if not data.get('bind', '').strip():
                    raise ValueError('请填写电脑接收IPv4地址，例如本机直连网卡地址；不是板卡地址')
                address = ipaddress.IPv4Address(data['bind'])
                if address.is_unspecified or address.is_loopback:
                    raise ValueError('在线上传需板卡可达的电脑IPv4地址')
                if self.local.get('receiver_session') != session:
                    raise ValueError('本会话电脑接收器未READY')
                self.receiver_status(folder, cfg)
                url = 'http://'+data['bind']+':8765/events'
                if self.local.get('receiver_bind') != data['bind']:
                    raise ValueError('接收器地址不一致')
            result = self.remote('check', session=session, url=url)
            if result.get('ready') is not True:
                raise ValueError('板端未就绪')
            self.selection = dict(session=session, url=url)
            self.local.update(ready=True, ready_session=session, ready_bind=data.get('bind',''),
                              ready_online=bool(data.get('online')), environment=result['environment'])
        elif action == 'start-capture':
            if not self.local.get('ready') or not self.selection or self.selection['session'] != session:
                raise ValueError('本会话未完成就绪检查')
            if data.get('confirmed') is not True:
                raise ValueError('必须明确点击并确认开始采集')
            if bool(data.get('online')) != bool(self.selection['url']):
                raise ValueError('在线上传选项已改变，请重新检查就绪')
            if self.selection['url'] and self.selection['url'] != 'http://'+data.get('bind','')+':8765/events':
                raise ValueError('接收地址已改变，请重新检查就绪')
            folder = self.directory(session)
            if (folder/'start-request.json').exists():
                raise ValueError('本会话已发出开始请求；请查询，不重复开始')
            if self.selection['url']:
                self.receiver_status(folder, flow.config(folder))
            self.local['ready'] = False
            save_new(folder/'start-request.json', dict(session=session, requested=stamp(), result='unknown_until_status'))
            self.local['live'] = dict(session_id=session, state='unknown', stale=True, resources_released=None)
            result = self.remote('start', **self.selection)
            self.local['live'].update(state=result['status'], stale=False)
        elif action in ('refresh-board', 'stop-capture'):
            if action == 'stop-capture':
                since = self.local.get('live', {}).get('stop_requested_at', time.time())
                self.remote('stop', session=session)
                self.local['live'] = dict(session_id=session, state='stopping', resources_released=None,
                                         stale=False, stop_requested_at=since)
            result = self.remote('status', session=session)
            view = capture_view(result['control'], result['capture'])
            view.update(snapshot=False, stale=False, session_id=session, pcm=result['pcm'], exit=result.get('exit'))
            if result.get('exit', {}).get('status') == 'failed':
                view.update(state='failed', error=result['exit'].get('error', view.get('error')))
            view['queried_at'] = stamp()
            view['resource_release_confirmed'] = view['resources_released'] is True and result['pcm']=='closed'
            if action == 'stop-capture' and view['state'] not in ('closed', 'failed'):
                view['stop_pending'] = '已请求正常停止，尚未确认；请刷新。超过20秒仍未结束视为停止超时，不强杀。'
                view['stop_requested_at'] = time.time()
            previous = self.local.get('live', {})
            since = previous.get('stop_requested_at')
            if since and view['state'] not in ('closed', 'failed'):
                view['stop_requested_at'] = since
                view['stop_pending'] = '停止超时，资源释放未确认' if time.time()-since>20 else '正在等待正常收尾'
            self.local['live'] = view
            self.local['ready'] = False
        elif action == 'download':
            self.download(session, job)
        elif action == 'prepare-result':
            matches = [r for r in self.local['downloads'] if r['session']==session and r['verified']]
            if not matches:
                raise ValueError('本会话尚无已验证下载')
            record = matches[-1]
            source = self.app.path(record['directory'])
            report = load(source/'download-verification.json')
            if report.get('status') != 'verified' or report.get('session_id') != session:
                raise ValueError('下载校验报告无效或会话不一致')
            for name, digest in report['remote_manifest']['files'].items():
                path = self.app.path(str(source/('console-outer.log' if name=='@console.log' else name)))
                path.relative_to(source)
                if sha(path) != digest:
                    raise ValueError('下载文件已变化，请保留原件并核查')
            saved = report['remote_manifest']['state']
            capture_dir = source/'capture' if (source/'capture').is_dir() else source
            rows = flow.jsonl(capture_dir/'candidates.relative.jsonl')
            if len(rows) != report['candidates'] or any(r.get('session_id') != session for r in rows):
                raise ValueError('候选数量或会话与下载报告不一致')
            view = capture_view(saved['control'], saved['capture'])
            record['summary'] = dict(view, candidate_count=report['candidates'],
                pc_saved_labels=report.get('pc_saved_labels'), files=report['files'])
            self.local['processing'] = dict(id=stamp(), session=session, directory=str(source),
                                           candidate_count=report['candidates'], summary=record['summary'])
            if report['candidates']==0:
                existing = next((e for e in self.app.state['entries'] if
                    e.get('source_dir')==str(source) and e.get('candidate_count')==0), None)
                if existing:
                    job['entry_id'] = existing['id']
                else:
                    self.app.import_result(dict(session_dir=str(source), video=''), job)
        elif action == 'receiver-start':
            self.start_receiver(session, data)
        elif action == 'receiver-status':
            folder = self.directory(session)
            self.receiver_status(folder, flow.config(folder))
        elif action == 'receiver-stop':
            self.local['ready'] = False
            if self.local.get('receiver_session') != session:
                raise ValueError('接收器会话不一致')
            if self.pc_process:
                flow.service_request(self.directory(session), flow.config(self.directory(session)), True)
                self.pc_process.wait(timeout=8)
                self.pc_process = None
                self.pc_log.close()
            else:
                self.receiver_stop.set()
                if self.receiver_thread:
                    self.receiver_thread.join(timeout=5)
                    if self.receiver_thread.is_alive():
                        raise ValueError('接收器停止尚未确认')
            self.local['receiver'] = 'closed'
        elif action == 'export-labels':
            folder = self.directory(session)
            rows = flow.pc_events(folder, session)
            save_new(folder/('received-export-'+stamp()+'.json'), rows)
            self.local['pc_saved_labels'] = len(rows)
        elif action == 'resend':
            self.resend(session, data)
        elif action == 'playback-finish':
            # Existing finish validates observations, maps, extracts and verifies. No guessed origin.
            folder = self.directory(session)
            if 'preview_sha256' not in flow.config(folder):
                raise ValueError('本会话未准备受控播放记录；请使用下方显式时间依据流程')
            if not (folder/'pc/service-closed.json').is_file():
                raise ValueError('请先正常关闭本会话接收器，再核查最终播放记录')
            self.prepare_playback_download(folder, session)
            flow.main(['finish', '--directory', str(folder)])
            self.local['playback_result'] = str(folder/'clips')
            self.import_playback(folder, job)
        else:
            raise ValueError('未知总控操作')
        self.app.persist()

    def download(self, session, job):
        before = self.remote('manifest', session=session)
        if any(row['session']==session and row['verified'] and
               load(Path(row['directory'])/'download-verification.json')['remote_manifest']['files']==before['files']
               for row in self.local['downloads']):
            raise ValueError('本会话相同结果已验证下载，请使用已有目录')
        files = before['files']
        if not files or not any(n in files for n in ('status.json', 'capture/status.json')) or not any(
                n in files for n in ('candidates.relative.jsonl', 'capture/candidates.relative.jsonl')):
            raise ValueError('所选会话缺少状态或候选文件')
        target = self.app.output/'downloads'/(session+'-'+stamp())
        target.mkdir(parents=True, exist_ok=False)
        report = dict(session_id=session, status='incomplete', remote_manifest=before)
        save_new(target/'download-request.json', report)
        try:
            destinations = set()
            for index, (name, digest) in enumerate(files.items(), 1):
                relative = PurePosixPath(name)
                if name != '@console.log' and (relative.is_absolute() or '..' in relative.parts or
                        str(relative) != name or
                        not re.fullmatch(r'[A-Za-z0-9_./-]+', name) or
                        relative.suffix not in ('.json', '.jsonl', '.log')):
                    raise ValueError('非法下载文件路径')
                local = target/('console-outer.log' if name=='@console.log' else name)
                if local in destinations or local.name in ('download-request.json', 'download-verification.json'):
                    raise ValueError('下载文件名冲突，保留诊断')
                destinations.add(local)
                local.parent.mkdir(parents=True, exist_ok=True)
                remote = flow.BOARD+'/outputs/'+session+('.console.log' if name=='@console.log' else '/'+name)
                self.app.update(job, '下载并校验 %s/%s：%s' % (index, len(files), name))
                self.connected().copy(remote, local)
                if sha(local) != digest:
                    raise ValueError('下载SHA256不匹配：'+name)
            after = self.remote('manifest', session=session)
            if before['files'] != after['files']:
                raise ValueError('下载期间板端文件变化，请保留本次诊断')
            capture = target/'capture' if (target/'capture').is_dir() else target
            for path in (target/'status.json', capture/'status.json'):
                if path.exists() and load(path).get('session_id') != session:
                    raise ValueError('下载状态会话不一致')
            rows = flow.jsonl(capture/'candidates.relative.jsonl')
            if any(row.get('session_id') != session for row in rows):
                raise ValueError('下载候选会话不一致')
            events = capture/'events.jsonl'
            if events.exists():
                labels = flow.jsonl(events)
                for row in labels:
                    flow.eventlink.validate(row, session)
                folder = self.directory(session)
                if (folder/'pc/labels.sqlite3').exists():
                    received = flow.pc_events(folder, session)
                    report['pc_saved_labels'] = len(received)
                    report['pc_fields_match_nonempty'] = bool(labels) and sorted(labels, key=lambda e:e['event_id'])==received
            report.update(status='verified', files=len(files), candidates=len(rows))
            self.local['downloads'].append(dict(session=session, directory=str(target), verified=True))
        finally:
            save_new(target/'download-verification.json', report)

    def prepare_playback_download(self, folder, session):
        matches = [row for row in self.local['downloads'] if row['session']==session and row['verified']]
        if not matches:
            raise ValueError('请先下载并验证本会话结果')
        source = Path(matches[-1]['directory'])
        verified = load(source/'download-verification.json')
        capture = source/'capture' if (source/'capture').is_dir() else source
        expected = {}
        for name in flow.FILES:
            path = capture/name
            relative = str(path.relative_to(source)).replace('\\', '/')
            if not path.is_file() or sha(path) != verified['remote_manifest']['files'].get(relative):
                raise ValueError('受控映射所需文件缺失或改变：'+name)
            expected[name] = sha(path)
        target = folder/'board-originals'
        target.mkdir(exist_ok=False)
        for name in flow.FILES:
            shutil.copyfile(capture/name, target/name)
        save_new(folder/'download-verification.json', dict(matched=True, remote_sha256=expected,
                 remote_unchanged_during_download=True, source_download=str(source)))

    def import_playback(self, folder, job):
        entry = self.app.import_result(dict(session_dir=str(folder/'board-originals'),
                                            video=flow.config(folder)['video']), job)
        manifest = load(folder/'clips/manifest.json')['results']
        rows = self.app.rows(entry)
        metadata = flow.jsonl(folder/'board-originals/event-metadata.jsonl')
        results = []
        for number, row in enumerate(rows, 1):
            matches = [m for m in metadata if m['source_segment']==row['candidate']['segment_id'] and
                       m['target_start_sample']==row['candidate']['estimated_start_sample']]
            result = next((r for r in manifest if len(matches)==1 and
                           r['event']['event_id']==matches[0]['event_id']), None)
            if not result or result['status'] != 'completed':
                results.append(dict(candidate_number=number, status='rejected', clip=None,
                                    reason='没有对应的已验证播放映射片段'))
                continue
            clip = folder/'clips'/result['file']
            # Reuse the verified mapped manifest; no start-only offset is invented.
            results.append(dict(candidate_number=number, status='completed', clip=str(clip),
                clip_sha256=sha(clip), full_decode_verified=True,
                video_seconds=result['video_event_seconds'], marker_seconds=result['video_event_seconds']-result['start_seconds'],
                start_seconds=result['start_seconds'], end_seconds=result['end_seconds']))
        entry.update(results=results, plan_id='software-playback-'+stamp(),
                     alignment=dict(kind='software_playback_observation', evidence='Recorded continuous playback; total error unknown'))
        save_new(Path(entry['folder'])/'playback-import.json', entry)

    def start_receiver(self, session, data):
        folder = self.directory(session)
        cfg = flow.config(folder)
        if self.pc_process or (self.receiver_thread and self.receiver_thread.is_alive()):
            raise ValueError('已有接收器，请先查询或正常关闭')
        bind = str(ipaddress.IPv4Address(data['bind']))
        self.local.update(receiver='starting', receiver_session=session, receiver_bind=bind)
        if 'preview_sha256' in cfg:
            if bind != cfg['bind']:
                raise ValueError('接收地址与准备的播放会话不一致')
            self.pc_log = (folder/'pc-launch.log').open('x', encoding='utf-8')
            self.pc_process = subprocess.Popen([sys.executable, '-B', str(Path(flow.__file__)),
                'start', '--directory', str(folder)], stdout=self.pc_log, stderr=self.pc_log)
            for _ in range(50):
                if self.pc_process.poll() is not None:
                    self.pc_process = None
                    self.pc_log.close()
                    raise ValueError('接收器启动失败，查看pc-launch.log')
                try:
                    self.receiver_status(folder, cfg)
                    self.local['playback_url'] = 'http://127.0.0.1:8766/'
                    return
                except (OSError, ValueError):
                    time.sleep(.1)
            raise ValueError('等待READY超时，查看日志并显式刷新接收器状态')
        pc = folder/'pc'
        pc.mkdir(exist_ok=False)
        self.receiver_stop.clear()
        ready, errors = threading.Event(), []
        def receive():
            store = server = None
            try:
                flow.eventlink.stage1_path(self.app.root/'阶段1_离线手动触发')
                store = flow.eventlink.Store(pc/'labels.sqlite3', session)
                server = flow.eventlink.server((bind, 8765), store)
                server.timeout = .3
                self.receiver = server
                ready.set()
                while not self.receiver_stop.is_set():
                    server.handle_request()
            except Exception as error:
                errors.append(str(error))
                save_new(pc/('receiver-error-'+stamp()+'.json'), dict(error=str(error)))
            finally:
                ready.set()
                if server:
                    server.server_close()
                if store:
                    store.db.close()
                self.receiver = None
                save_new(pc/'service-closed.json', dict(session=session, errors=errors))
        self.receiver_thread = threading.Thread(target=receive, daemon=True)
        self.receiver_thread.start()
        if not ready.wait(5) or errors or self.receiver is None:
            self.local['receiver'] = 'failed'
            raise ValueError('电脑接收器未READY：'+'; '.join(errors))
        self.receiver_status(folder, cfg)

    def receiver_status(self, folder, cfg):
        if 'preview_sha256' in cfg:
            live = flow.service_request(folder, cfg)
            self.local['pc_saved_labels'] = live['pc_committed_labels']
        elif self.receiver is None or not self.receiver_thread or not self.receiver_thread.is_alive():
            self.local['receiver'] = 'unknown_or_closed'
            raise ValueError('本进程接收器未运行；不控制其他接收器')
        else:
            self.local['pc_saved_labels'] = len(flow.pc_events(folder, cfg['session']))
        self.local['receiver'] = 'READY'

    def resend(self, session, data):
        folder = self.directory(session)
        self.receiver_status(folder, flow.config(folder))
        events = self.app.path(data['events'])
        rows = flow.jsonl(events)
        for row in rows:
            flow.eventlink.validate(row, session)
        uploader = flow.eventlink.Uploader('http://127.0.0.1:8765/events' if self.local['receiver_bind']=='0.0.0.0'
                         else 'http://'+self.local['receiver_bind']+':8765/events')
        try:
            for row in rows:
                uploader.submit(row)
        finally:
            pending = uploader.close(5)
        save_new(folder/('resend-'+stamp()+'.json'), dict(events_sha256=sha(events), count=len(rows), pending=pending,
                 meaning='Explicit replay of original four fields; original process ACK unchanged'))
        if pending:
            raise ValueError('补发仍有未确认标签；诊断已保存')

    def close(self):
        self.receiver_stop.set()
        if self.receiver_thread:
            self.receiver_thread.join(timeout=5)
        # The controlled receiver has its own normal stop API and saved identity.
        if self.pc_process and self.pc_process.poll() is None:
            folder = self.directory(self.local['receiver_session'])
            flow.service_request(folder, flow.config(folder), True)
            self.pc_process.wait(timeout=8)
        if self.pc_log:
            self.pc_log.close()

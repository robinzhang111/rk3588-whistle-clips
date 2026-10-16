"""Loopback workbench with explicit user-operated board controls."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import traceback
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT/'阶段1_离线手动触发'))
from basketclip.media import extract_clip, inspect_video, require_tool
from basketclip.relative import sha
from basketclip.start_only import candidate_window
from basketclip.timing import finite_number


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def save_new(path, value):
    with path.open('x', encoding='utf-8') as target:
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.flush()
        os.fsync(target.fileno())


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def capture_view(control, capture, simulated=False):
    state = capture.get('status') or control.get('status', 'unknown')
    if control.get('status') == 'failed' or capture.get('error'):
        state = 'failed'
    labels = capture.get('automatic_labels', {})
    return dict(state=state, snapshot=True, simulated=simulated,
        resources_released=capture.get('resources_released', control.get('resources_released')),
        stop_reason=control.get('stop_reason') or capture.get('close_reason') or
                    capture.get('segment_report', {}).get('close_reason'),
        error=capture.get('error'), windows=capture.get('windows'),
        elapsed_seconds=capture.get('elapsed_seconds'),
        candidates_saved=capture.get('candidates_saved', control.get('candidates_saved', 0)),
        time_basis=capture.get('time_basis', 'unknown'),
        time_invalid_reason=capture.get('sampling_time_invalid_reason'),
        relative_only=bool(capture.get('sampling_time_invalid_reason') or
            'relative' in capture.get('time_basis','') or
            'sample_clock' in str(capture.get('error') or '')),
        labels_saved=labels.get('saved'), labels_submitted=labels.get('submitted'),
        label_ack=(labels.get('all_acknowledged') if labels.get('saved', 0)>0 else None),
        pending_event_ids=labels.get('pending_event_ids'), hardware_online=None,
        ten_minute_acceptance='待实机验收', full_product_accepted=False)


class Workbench:
    def __init__(self, output, root=ROOT, resume=False):
        self.root = root.resolve()
        self.output = output.absolute()
        if not resume:
            self.output.mkdir(parents=True, exist_ok=False)
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.state = load(self.output/'state.json') if resume else dict(
            entries=[], jobs=[], output=str(self.output), hardware_connected=False)
        if self.state['output'] != str(self.output):
            raise ValueError('工作目录或工作台类型不匹配')
        self.state['hardware_connected'] = False
        for job in self.state['jobs']:
            if job['status'] in ('running','queued'):
                job.update(status='failed',message='上次进程退出时尚未完成；请核查已保存产物，不自动重试')
        from console_control import Console
        self.console = Console(self)
        self.persist()

    def video_path(self, value):
        # Camera cards/USB may live outside the project. Only explicit media paths are accepted.
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() not in ('.mp4', '.mov', '.mkv', '.avi', '.mts', '.m4v'):
            raise ValueError('请选择已挂载相机或本地的录像文件')
        return path

    def video_hash(self, path, job):
        digest = hashlib.sha256()
        total = path.stat().st_size
        count = 0
        with path.open('rb') as stream:
            while True:
                block = stream.read(4*1024*1024)
                if not block:
                    break
                digest.update(block)
                count += len(block)
                if count == total or count % (64*1024*1024) == 0:
                    self.update(job, '录像读取与SHA256：%.1f%%（原件只读）' % (100*count/max(1,total)))
        return digest.hexdigest()

    def path(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('请填写本地文件或目录路径')
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.root/path
        path = path.resolve(strict=True)
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise ValueError('请先将材料放入Project_1项目目录，原件不要覆盖')
        if any(part.startswith('.') for part in relative.parts):
            raise ValueError('不读取隐藏目录中的文件')
        return path

    def persist(self):
        with self.lock:
            temporary = self.output/'state.tmp'
            temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            os.replace(temporary, self.output/'state.json')

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def entry(self, entry_id):
        return next(e for e in self.state['entries'] if e['id']==entry_id)

    def update(self, job, message):
        with self.lock:
            job['message'] = message
            self.persist()

    def submit(self, action, data):
        if action not in ('import', 'example', 'simulate', 'plan', 'extract', 'review', 'console'):
            raise ValueError('未知操作')
        with self.lock:
            if any(j['status'] in ('queued', 'running') for j in self.state['jobs']):
                raise ValueError('当前操作尚未完成，请等待后再提交')
            job = dict(id=stamp(), action=action, status='queued', message='等待处理')
            self.state['jobs'].append(job)
            self.persist()
        self.pool.submit(self.run, job, data)
        return job['id']

    def run(self, job, data):
        try:
            job['status'] = 'running'
            self.update(job, '处理中')
            if job['action']=='console':
                self.console.execute(data['operation'], data, job)
            elif job['action']=='example':
                self.example(job)
            elif job['action']=='import':
                self.import_result(data, job)
            elif job['action']=='simulate':
                entry=self.simulate(data)
                job['entry_id']=entry['id']
            else:
                getattr(self, job['action'])(self.entry(data['entry_id']), data, job)
            job.update(status='completed', message='操作完成')
        except Exception as failure:
            text = traceback.format_exc()
            if isinstance(failure, subprocess.CalledProcessError):
                text += '\n'+str(failure.stderr or '')
            with (self.output/(job['id']+'.log')).open('x', encoding='utf-8') as log:
                log.write(text)
            job.update(status='failed', message=str(failure), log=job['id']+'.log')
        finally:
            self.persist()

    def import_result(self, data, job):
        source = self.path(data['session_dir'])
        if not source.is_dir():
            raise ValueError('请选择会话目录')
        capture_dir = source/'capture' if (source/'capture').is_dir() else source
        candidates = self.path(str(capture_dir/'candidates.relative.jsonl'))
        if candidates.stat().st_size > 32*1024*1024:
            raise ValueError('候选文件超过32MB，需先核查内容')
        raw = candidates.read_bytes()
        rows = [json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()]
        capture = load(capture_dir/'status.json') if (capture_dir/'status.json').exists() else {}
        control = load(source/'status.json') if (source/'status.json').exists() else {}
        exit_state = load(source/'exit.json') if (source/'exit.json').exists() else {}
        session = capture.get('session_id') or control.get('session_id') or (rows[0]['session_id'] if rows else None)
        if not isinstance(session, str) or not session:
            raise ValueError('缺少会话标识')
        if any(row.get('session_id')!=session for row in rows) or any(
                s.get('session_id', session)!=session for s in (capture, control, exit_state)):
            raise ValueError('候选、采集和控制状态的会话不一致')
        control.update(exit_state)
        segments = sorted(set(row['candidate']['segment_id'] for row in rows))
        video = self.video_path(data['video']) if data.get('video') else None
        if rows and video is None:
            raise ValueError('本会话有候选，请选择对应录像后导入；不会自动沿用历史录像或偏移')
        duration = video_hash = before_video = info = None
        if video:
            self.update(job, '核查录像并计算来源哈希；大文件需要一些时间')
            info, duration = inspect_video(video)
            before_video = video.stat()
            video_hash = self.video_hash(video, job)
            after_video = video.stat()
            if (before_video.st_size, before_video.st_mtime_ns) != (after_video.st_size, after_video.st_mtime_ns):
                raise ValueError('录像在导入期间改变，请保留原件并重试')
        if candidates.read_bytes()!=raw:
            raise ValueError('候选文件正在变化，请停止采集并重新下载后导入')
        number = 'result-%04d' % (len(self.state['entries'])+1)
        folder = self.output/number
        folder.mkdir(exist_ok=False)
        (folder/'candidates.snapshot.jsonl').write_bytes(raw)
        save_new(folder/'control.snapshot.json', control)
        save_new(folder/'capture.snapshot.json', capture)
        stream = next(s for s in info['streams'] if s['codec_type']=='video') if info else None
        entry = dict(id=number, session_id=session, source_kind='导入的本地历史结果',
            folder=str(folder), source_dir=str(source), video=str(video) if video else None,
            video_sha256=video_hash, video_size=before_video.st_size if before_video else None,
            video_mtime_ns=before_video.st_mtime_ns if before_video else None,
            video_duration=duration, zero_pts=bool(stream and float(stream['start_time'])==0 and float(info['format']['start_time'])==0),
            candidates_sha256=sha(candidates), candidates_path=str(candidates),
            candidate_count=len(rows), segments=segments, view=capture_view(control, capture),
            alignment=None, results=[], reviews={}, historical_reviews={})
        save_new(folder/'import.json', entry)
        with self.lock:
            self.state['entries'].append(entry)
            job['entry_id']=number
            self.persist()
        return entry

    def rows(self, entry):
        return [json.loads(line) for line in (Path(entry['folder'])/'candidates.snapshot.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]

    def check_video(self, entry, full=False):
        path = self.video_path(entry['video'])
        stat = path.stat()
        if stat.st_size!=entry['video_size'] or stat.st_mtime_ns!=entry['video_mtime_ns'] or (full and sha(path)!=entry['video_sha256']):
            raise ValueError('原录像已改变，请保留本次结果并重新导入')
        return path

    def plan(self, entry, data, job):
        if not entry['candidate_count']:
            raise ValueError('本会话无候选，无需时间映射或裁剪；可直接导出记录')
        if entry.get('simulated'):
            raise ValueError('模拟状态不用于真实定位')
        if data.get('confirmed') is not True or not isinstance(data.get('evidence'), str) or not data['evidence'].strip():
            raise ValueError('请填写本轮起点依据并明确确认；不能猜测起点')
        offset = finite_number(data['offset'], '开头偏移')
        if offset < 0 or not entry['zero_pts']:
            raise ValueError('当前入口要求非负偏移、录像首PTS为0')
        if data.get('segment_id') not in entry['segments']:
            raise ValueError('请选择本会话的连续采集段')
        self.check_video(entry)
        alignment = dict(kind='user_selected_start_only', session_id=entry['session_id'],
            segment_id=data['segment_id'], sample_rate=22050, video_seconds_at_sample_zero=offset,
            source_video_sha256=entry['video_sha256'], evidence=data['evidence'].strip(),
            synchronization_verified=False, total_alignment_error_seconds=None,
            drift_compensation_applied=False)
        results=[]
        for i,row in enumerate(self.rows(entry),1):
            result=dict(candidate_number=i, clip=None)
            try:
                result.update(candidate_window(row, alignment, entry['video_duration']), status='ready')
            except (ValueError, KeyError, TypeError) as failure:
                result.update(status='rejected', reason=str(failure))
            results.append(result)
        plan_id=stamp()
        save_new(Path(entry['folder'])/('plan-'+plan_id+'.json'), dict(alignment=alignment, results=results))
        # New plans never inherit clip reviews from a different alignment.
        entry.update(alignment=alignment, plan_id=plan_id, results=results, reviews={}, historical_reviews={})
        self.persist()

    def extract(self, entry, data, job):
        if not entry['alignment']:
            raise ValueError('缺少本轮起点依据，禁止截片')
        numbers = data.get('numbers')
        if not isinstance(numbers,list) or not numbers or len(numbers)!=len(set(numbers)) or any(type(n) is not int or n<1 or n>len(entry['results']) for n in numbers):
            raise ValueError('请选择有效候选')
        if any(entry['results'][n-1]['status']=='rejected' for n in numbers):
            raise ValueError('所选候选被定位检查拒绝')
        self.update(job, '核对原录像哈希')
        video=self.check_video(entry, full=True)
        folder=Path(entry['folder'])/('clips-'+stamp())
        folder.mkdir(exist_ok=False)
        records=[]
        for n in numbers:
            result=entry['results'][n-1]
            if result.get('clip'):
                continue
            self.update(job, '正在生成并完整解码候选%d' % n)
            output=folder/('candidate_%04d.mp4'%n)
            try:
                extract_clip(video, output, result['start_seconds'], result['end_seconds'])
                _, duration=inspect_video(output)
                if abs(duration-(result['end_seconds']-result['start_seconds']))>.15:
                    raise ValueError('片段实际时长与计划不符')
                subprocess.run([require_tool('ffmpeg'), '-nostdin','-v','error','-xerror','-i',
                    str(output),'-f','null','-'], check=True,capture_output=True,text=True,encoding='utf-8')
                result.update(clip=str(output),clip_sha256=sha(output),full_decode_verified=True, status='completed')
                records.append(dict(candidate_number=n, status='completed', **{k:result[k] for k in ('clip','clip_sha256','full_decode_verified')}))
            except Exception as failure:
                result.update(status='failed', reason=str(failure))
                save_new(folder/'failure.json', dict(candidate_number=n,error=str(failure),
                    stderr=str(getattr(failure,'stderr',''))))
                raise
            finally:
                self.persist()
        save_new(folder/'verification.json', dict(results=records, synchronization_verified=False))
        self.check_video(entry)

    def review(self, entry, data, job):
        n=data['candidate_number']
        if type(n) is not int or not 1<=n<=len(entry['results']):
            raise ValueError('候选编号无效')
        result=entry['results'][n-1]
        if not result.get('clip') or not result.get('full_decode_verified'):
            raise ValueError('请先生成并核验片段，再保存人工意见')
        if sha(self.path(result['clip']))!=result['clip_sha256']:
            raise ValueError('片段哈希已变化')
        whistle=data.get('whistle')
        if whistle not in ('yes','no','uncertain') or type(data.get('position_confirmed')) is not bool:
            raise ValueError('请选择声音分类及位置意见')
        actual=data.get('actual_clip_seconds')
        if actual is not None:
            actual=finite_number(actual,'声音在片段中的秒数')
            if not 0<=actual<=result['end_seconds']-result['start_seconds']:
                raise ValueError('声音位置超出片段范围')
        if data['position_confirmed'] and actual is None:
            raise ValueError('确认位置前请填写实际声音在片段中的秒数')
        same=data.get('same_whistle_as')
        if same is not None and (type(same) is not int or not 1<=same<=len(entry['results']) or same==n or whistle!='yes'):
            raise ValueError('重复候选编号无效，且当前候选必须确认含哨声')
        value=dict(session_id=entry['session_id'], plan_id=entry['plan_id'], candidate_number=n,
            whistle=whistle, actual_clip_seconds=actual, same_whistle_as=same,
            position_confirmed=data['position_confirmed'], note=str(data.get('note',''))[:4000],
            source='user review saved in local workbench', recorded_utc=datetime.now(timezone.utc).isoformat(),
            clip_sha256=result['clip_sha256'], synchronization_verified=False, scope='selected candidate only; not full-video truth')
        save_new(Path(entry['folder'])/('review-'+stamp()+'.json'),value)
        entry['reviews'][str(n)]=value
        self.persist()

    def example(self, job):
        entry=self.import_result(dict(session_dir=str(self.root/'阶段6_项目展示/outputs/field-return-20261015-01/core-field-20261015-02-06f3633f'),
            video=str(self.root/'data/acepro2/VID_20261015_162443_074.mp4')), job)
        alignment=load(self.root/'阶段1_离线手动触发/outputs/start-only-anchor-20261015-01/alignment.json')
        if (alignment['source_video_sha256']!=entry['video_sha256'] or alignment['session_id']!=entry['session_id']):
            raise ValueError('历史示例来源不匹配')
        self.plan(entry,dict(confirmed=True,evidence=alignment['selection'],
            offset=alignment['video_seconds_at_sample_zero'],segment_id=alignment['segment_id']),job)
        prior=self.path('阶段1_离线手动触发/outputs/start-only-plan-20261015-01')
        plan=load(prior/'manifest.json')
        reuse=load(prior/'clip-reuse-verification.json')
        feedback_path=self.path('阶段1_离线手动触发/outputs/field-candidate-review-20261015-01/human-review-20261015-01/feedback.json')
        if (sha(feedback_path)!=reuse['human_feedback_sha256'] or len(reuse['results'])!=len(entry['results'])
                or len(plan['results'])!=len(entry['results'])):
            raise ValueError('历史片段或反馈依据不完整')
        for result, old, clip in zip(entry['results'],plan['results'],reuse['results']):
            if result['candidate_number']!=clip['candidate_number'] or any(abs(result[k]-old[k])>1e-6 for k in ('start_seconds','end_seconds')):
                raise ValueError('历史片段窗口与新计划不一致')
            path=self.path(clip['file'])
            if not clip['full_decode_verified_by_prior_identical_file'] or sha(path)!=clip['sha256']:
                raise ValueError('历史片段哈希或完整解码依据不符')
            result.update(clip=str(path),clip_sha256=clip['sha256'],status='completed',full_decode_verified=True,reused=True)
        entry['historical_reviews']={str(r['candidate_number']):r for r in load(feedback_path)['items']}
        entry['source_kind']='历史16候选示例（原95秒失败会话）'
        save_new(Path(entry['folder'])/'historical-reuse.json', dict(reuse_source=str(prior),feedback_source=str(feedback_path),
            results=entry['results'], historical_reviews=entry['historical_reviews']))
        self.persist()

    def simulate(self, data):
        scenario=data.get('scenario')
        scenarios=dict(waiting=dict(status='waiting'),running=dict(status='running',windows=20),
            closed=dict(status='closed',resources_released=True,close_reason='duration_limit',elapsed_seconds=600),
            failed=dict(status='failed',error='模拟输入错误',resources_released=False),
            relative=dict(status='running',time_basis='relative samples; software time invalid',sampling_time_invalid_reason='模拟时间失效'))
        if scenario not in scenarios:
            raise ValueError('未知模拟状态')
        number='demo-%04d'%(len(self.state['entries'])+1)
        entry=dict(id=number,session_id=number,source_kind='模拟状态（不是实机证据）',simulated=True,
            candidate_count=0,segments=[],results=[],alignment=None,reviews={},historical_reviews={},
            view=capture_view(scenarios[scenario],scenarios[scenario],True))
        self.state['entries'].append(entry)
        save_new(self.output/(number+'.json'),entry)
        self.persist()
        return entry

    def close(self):
        self.pool.shutdown(wait=True)
        self.console.close()


def make_server(address, app):
    class Handler(BaseHTTPRequestHandler):
        def valid_host(self):
            return self.headers.get('Host')=='127.0.0.1:%d'%self.server.server_port

        def json(self, value, status=200):
            body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            app=self.server.app
            if not self.valid_host() or self.headers.get('Origin')!='http://127.0.0.1:%d'%self.server.server_port:
                self.json(dict(error='仅允许本机页面提交操作'),403)
                return
            try:
                self.connection.settimeout(5)
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=65536 or self.headers.get('Content-Type')!='application/json':
                    raise ValueError('需要有效JSON请求')
                if not self.path.startswith('/api/'):
                    raise ValueError('未知操作')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise ValueError('请求必须为对象')
                if self.path=='/api/close':
                    self.json(dict(stopping=True))
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                    return
                self.json(dict(job_id=app.submit(self.path[len('/api/'):],data)),202)
            except (ValueError,KeyError,TypeError) as error:
                self.json(dict(error=str(error)),400)

        def do_GET(self):
            app=self.server.app
            if not self.valid_host():
                self.json(dict(error='仅本机地址可用'),403)
                return
            parsed=urlsplit(self.path)
            if parsed.path=='/api/state':
                self.json(app.snapshot())
            elif parsed.path=='/':
                body=Path(__file__).with_suffix('.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path=='/api/report':
                self.json(app.snapshot())
            elif parsed.path=='/media':
                try:
                    query=parse_qs(parsed.query)
                    entry=app.entry(query['entry'][0])
                    n=int(query['number'][0])
                    if not 1<=n<=len(entry['results']):
                        raise ValueError('候选编号错误')
                    result=entry['results'][n-1]
                    path=app.path(result['clip'])
                    if sha(path)!=result['clip_sha256']:
                        raise ValueError('片段已改变')
                    self.media(path)
                except (KeyError,ValueError,OSError,StopIteration,TypeError) as error:
                    self.json(dict(error=str(error)),404)
            else:
                self.json(dict(error='未找到'),404)

        def media(self, path):
            size=path.stat().st_size
            start,end=0,size-1
            header=self.headers.get('Range')
            if header:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',header)
                if not match or not any(match.groups()):
                    self.send_error(416)
                    return
                a,b=match.groups()
                if a:
                    start=int(a)
                    end=min(int(b),end) if b else end
                else:
                    start=max(0,size-int(b))
                if not 0<=start<=end<size:
                    self.send_error(416)
                    return
            self.send_response(206 if header else 200)
            self.send_header('Content-Type',mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Accept-Ranges','bytes')
            self.send_header('Content-Length',str(end-start+1))
            if header:
                self.send_header('Content-Range','bytes %d-%d/%d'%(start,end,size))
            self.end_headers()
            try:
                with path.open('rb') as source:
                    source.seek(start)
                    remaining=end-start+1
                    while remaining:
                        chunk=source.read(min(65536,remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining-=len(chunk)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                pass

        def log_message(self, *_):
            pass
    server=ThreadingHTTPServer(address,Handler)
    server.app=app
    return server


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--port',type=int,default=8767)
    parser.add_argument('--open-browser',action='store_true')
    parser.add_argument('--resume',action='store_true',help='Reopen the specified saved workbench output')
    args=parser.parse_args(argv)
    bundled=ROOT/'tools/ffmpeg-9.0.2-essentials_build/bin'
    os.environ['PATH']=str(bundled)+os.pathsep+os.environ.get('PATH','')
    if args.resume and not args.output:
        parser.error('--resume requires --output')
    # Bind first: a second click must not touch an already-running workspace.
    server=make_server(('127.0.0.1',args.port),None)
    app=None
    try:
        app=Workbench(args.output or ROOT/'阶段6_项目展示/outputs'/('workbench-'+stamp()),resume=args.resume)
        server.app=app
        url='http://127.0.0.1:%d/'%server.server_port
        save_new(app.output/('ready-'+stamp()+'.json'),dict(pid=os.getpid(),url=url,hardware_connected=False))
        print('READY '+url+'\n结果目录：'+str(app.output),flush=True)
        if args.open_browser:
            import webbrowser
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        print('等待当前本地操作结束并保存结果…',flush=True)
    finally:
        if server:
            server.server_close()
        if app:
            app.close()
            save_new(app.output/('closed-'+stamp()+'.json'),dict(closed_utc=datetime.now(timezone.utc).isoformat(),hardware_connected=False))


if __name__=='__main__':
    main()

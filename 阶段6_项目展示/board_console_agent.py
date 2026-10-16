"""Explicit SSH command adapter. Reuses the installed runtime; no source deployment."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading

BASE = Path('/home/basketclip-project/stage2')
RELEASE = BASE/'runtime-field-rec-20261015-04'
LABEL_RELEASE = BASE/'runtime-software-labels-20261015-01'
MODEL = BASE/'deploy-20261013-01/conversion-onnx-fp16/WhistleNetMk16-fp16.rknn'


def session_path(session):
    if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', session):
        raise ValueError('Invalid session ID')
    path = BASE/'outputs'/session
    if path.is_symlink():
        raise ValueError('Symlink session refused')
    return path


def read_json(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def environment():
    pcm = Path('/proc/asound/card0/pcm0c/sub0/status').read_text().strip()
    occupied = subprocess.run(['/usr/bin/fuser', '-s', '/dev/snd/pcmC0D0c']).returncode
    units = subprocess.run(['systemctl', 'list-units', '--all', '--no-legend',
                            'basketclip*'], capture_output=True, text=True, check=True).stdout
    processes = subprocess.run(['ps', '-eo', 'pid,args'], capture_output=True, text=True, check=True).stdout
    # Waiting REC owns the next capture too. Never stop it from this adapter.
    owners = [line for line in processes.splitlines() if line.split()[0] != str(os.getpid()) and any(name in line for name in
              ('/field_rec.py', '/start_software_time.py', '/start_space.py', '/board_runtime.py'))]
    transient = subprocess.run(['systemctl', 'list-jobs', '--no-legend'],
                               capture_output=True, text=True, check=True).stdout
    service_busy = any(('active' in line.split() or 'activating' in line.split() or
                        'deactivating' in line.split()) for line in units.splitlines())
    ready = pcm == 'closed' and occupied == 1 and not owners and not service_busy and 'basketclip' not in transient
    return dict(hostname=socket.gethostname(), architecture=os.uname().machine,
                python=sys.executable, pcm=pcm, fuser_returncode=occupied,
                services=units, capture_owners=owners, service_jobs=transient, ready=ready)


def runtime_check(session, url=None):
    sys.path[:0] = [str(RELEASE/'阶段2_离线哨声检测/tools'),
                   str(LABEL_RELEASE/'阶段3_实时传输'), str(BASE.parent/'stage1')]
    from board_runtime import preflight
    from sampling_capture import verify_abi
    from verify_resampling_relation import validate_relation, producer_command
    output = session_path(session)/'capture'
    if output.parent.exists():
        raise ValueError('Session exists; refusing overwrite')
    validate_relation(RELEASE/'native-relation.json', Path('/usr/bin/ffmpeg'))
    verify_abi(RELEASE/'abi-source/upstream-pcm-1.2.8.h')
    cfg = preflight(output, session, 2, 512, False, producer_command(Path('/usr/bin/ffmpeg')), MODEL, input_gain=16)
    cfg.update(time_basis='same-source software estimate; absolute error unknown',
               field_limit_seconds=None, capture_buffer_target_us=1000000,
               capture_minimum_read_frames=1024, allow_relative_on_clock_drift=True)
    if url:
        import automatic_labels
        from basketclip.timing import utc_time
        cfg.update(formal_utc_upload=True, upload_reason='explicit software-time engineering policy',
                   label_receiver=url, time_quality='software_estimate')
    return cfg


def snapshot(session):
    folder = session_path(session)
    if not folder.is_dir():
        raise ValueError('Session missing')
    control, capture = read_json(folder/'status.json'), read_json(folder/'capture/status.json')
    exit_state = read_json(folder/'exit.json')
    if exit_state:
        control = dict(control, **exit_state)
    if not capture:
        capture = control
    for state in (control, capture):
        if state and state.get('session_id') != session:
            raise ValueError('Remote session mismatch')
    return dict(session_id=session, control=control, capture=capture,
                pcm=Path('/proc/asound/card0/pcm0c/sub0/status').read_text().strip())


def main(action, data, script):
    if socket.gethostname() != 'lubancat' or os.uname().machine != 'aarch64':
        raise ValueError('Unexpected board identity')
    session = data.get('session')
    if action == 'probe':
        return environment()
    if action == 'list':
        return dict(sessions=sorted(p.name for p in (BASE/'outputs').iterdir()
                    if p.is_dir() and not p.is_symlink() and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', p.name)))
    folder = session_path(session)
    if action == 'check':
        env = environment()
        if not env['ready']:
            raise ValueError('PCM, REC service or capture owner busy; inspect probe, do not force stop')
        runtime_check(session, data.get('url'))
        return dict(session_id=session, ready=True, environment=env)
    if action == 'start':
        # A detached child repeats preflight and takes an exclusive lock before opening PCM.
        main('check', data, script)
        folder.mkdir(exist_ok=False)
        (folder/'request.json').write_text(json.dumps(data))
        (folder/'status.json').write_text(json.dumps(dict(session_id=session, status='starting', resources_released=None)))
        with (folder/'console.log').open('x') as log:
            child = subprocess.Popen([sys.executable, '-B', '-c', script, 'run', json.dumps(data)],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        return dict(session_id=session, status='starting', launcher_pid=child.pid,
                    resources_released=None)
    if action == 'run':
        import fcntl
        import signal
        import ctypes as C
        import ctypes.util
        lock = open('/tmp/basketclip-console-capture.lock', 'a')
        stopped = threading.Event()
        stop_reason = ['unknown']
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            env = environment()
            if not env['ready']:
                raise ValueError('Capture owner changed; refusing PCM')
            # Parent exclusively reserved the session; preflight checks capture subdirectory.
            sys.path[:0] = [str(RELEASE/'阶段2_离线哨声检测/tools'),
                           str(LABEL_RELEASE/'阶段3_实时传输'), str(BASE.parent/'stage1')]
            from board_runtime import preflight, run_session
            from sampling_capture import CapturePCM
            from timed_input import TimedCaptureSource
            from verify_resampling_relation import producer_command
            from whistle_predictor import NpuPredictor
            cfg = preflight(folder/'capture', session, 2, 512, False,
                            producer_command(Path('/usr/bin/ffmpeg')), MODEL, input_gain=16)
            cfg.update(time_basis='same-source software estimate; absolute error unknown',
                       field_limit_seconds=None, allow_relative_on_clock_drift=True)
            def stop(reason):
                stop_reason[0] = reason
                stopped.set()
            signal.signal(signal.SIGTERM, lambda *_: stop('external_sigterm'))
            signal.signal(signal.SIGINT, lambda *_: stop('external_sigint'))
            diagnostic = []
            def source(log):
                stream = (folder/'capture/sampling-diagnostics.jsonl').open('x')
                diagnostic.append(stream)
                library = C.CDLL(ctypes.util.find_library('asound') or 'libasound.so.2')
                return TimedCaptureSource(Path('/usr/bin/ffmpeg'), RELEASE/'native-relation.json',
                    lambda: CapturePCM(library, 'hw:CARD=rockchipes8388,DEV=0',
                                       buffer_us=1000000, minimum_frames=1024),
                    session+':segment-0001', stream, log=log, allow_relative_on_clock_drift=True)
            labels = None
            if data.get('url'):
                from automatic_labels import AutomaticLabels
                cfg.update(formal_utc_upload=True, label_receiver=data['url'],
                           time_quality='software_estimate', upload_reason='explicit software-time engineering policy')
                labels = lambda path: AutomaticLabels(path, session, data['url'], allow_software_time=True)
            try:
                result = run_session(folder/'capture', cfg, source, lambda: NpuPredictor(MODEL),
                    stopped, timing_factory=lambda segment, src: src.timing, label_factory=labels)
            finally:
                for stream in diagnostic:
                    stream.close()
            result['stop_reason'] = ('user_stop' if (folder/'capture/STOP').exists() else
                                     'runtime_failure' if result['status']=='failed' else stop_reason[0])
            (folder/'exit.json').write_text(json.dumps(dict(status=result['status'],
                session_id=session, stop_reason=result['stop_reason'], resources_released=result['resources_released'],
                exit_code=0 if result['status']=='closed' else 1)))
            return result
        except Exception as error:
            (folder/'exit.json').write_text(json.dumps(dict(session_id=session, status='failed',
                error=str(error), resources_released=None, exit_code=1)))
            raise
        finally:
            lock.close()
    if action == 'status':
        value = snapshot(session)
        value['exit'] = read_json(folder/'exit.json')
        return value
    if action == 'stop':
        state = snapshot(session)
        if state['capture'].get('status') not in ('starting', 'running', 'stopping'):
            raise ValueError('No active capture status; inspect before stopping')
        # Existing run_session cooperative stop; no PID signal or force kill.
        capture_folder = folder/'capture' if (folder/'capture').is_dir() else folder
        (capture_folder/'STOP').touch(exist_ok=True)
        return dict(session_id=session, status='stopping', resources_released=None)
    if action == 'manifest':
        state = snapshot(session)
        if state['capture'].get('status') not in ('closed', 'failed') and state['control'].get('status') != 'failed':
            raise ValueError('Download only terminal sessions; preserve active files')
        files = {}
        for path in sorted(folder.rglob('*')):
            if path.is_symlink():
                raise ValueError('Symlink in session refused')
            if path.is_file() and path.suffix in ('.json', '.jsonl', '.log'):
                files[str(path.relative_to(folder)).replace(os.sep, '/')] = hashlib.sha256(path.read_bytes()).hexdigest()
        outer = folder.with_name(folder.name+'.console.log')
        if outer.is_file() and not outer.is_symlink():
            files['@console.log'] = hashlib.sha256(outer.read_bytes()).hexdigest()
        return dict(session_id=session, files=files, state=state)
    raise ValueError('Unknown board operation')


if __name__ == '__main__':
    # Parent passes the same adapter only when the user explicitly clicks Start.
    try:
        print(json.dumps(main(sys.argv[1], json.loads(sys.argv[2]), SCRIPT if 'SCRIPT' in globals() else ''), ensure_ascii=False))
    except (ValueError, FileExistsError) as error:
        print(json.dumps(dict(console_error=str(error)), ensure_ascii=False), flush=True)
        sys.exit(3)

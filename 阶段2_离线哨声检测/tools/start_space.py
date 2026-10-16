"""User-owned space start/stop for the existing relative-only runtime."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from basketball_audio_adapter import FILTER
from board_runtime import preflight


@contextmanager
def keyboard():
    if os.name == 'nt':
        import msvcrt
        def read(timeout):
            deadline = time.perf_counter() + timeout
            while time.perf_counter() < deadline:
                if msvcrt.kbhit():
                    key = msvcrt.getwch()
                    if key == '\x03':
                        raise KeyboardInterrupt()
                    return key
                time.sleep(.02)
        yield read
    else:
        import select
        import termios
        import tty
        with open('/dev/tty', 'r') as terminal:
            previous = termios.tcgetattr(terminal)
            try:
                tty.setcbreak(terminal.fileno())
                def read(timeout):
                    ready, _, _ = select.select([terminal], [], [], timeout)
                    return terminal.read(1) if ready else None
                yield read
            finally:
                termios.tcsetattr(terminal, termios.TCSADRAIN, previous)


def control(command, output, read_key, announce=print):
    """Never launch before first space; stop only this session using A's callback."""
    announce('准备好后轻按一次空格开始；再次空格停止。建议运行约60秒。')
    try:
        while read_key(.1) != ' ':
            pass
    except KeyboardInterrupt:
        announce('已取消；未启动采集。')
        return 130
    output.parent.mkdir(parents=True, exist_ok=True)
    console = output.with_name(output.name + '.console.log')
    with console.open('xb') as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT)
        stopping = False
        last = None
        next_status = 0.
        try:
            announce('已请求启动，等待状态；再次空格可请求停止。')
            while process.poll() is None:
                if not stopping and read_key(.1) == ' ':
                    stopping = True
                    announce('已请求停止，等待本会话收尾。')
                elif stopping:
                    time.sleep(.05)
                if stopping and output.is_dir():
                    (output / 'STOP').touch(exist_ok=True)
                if time.perf_counter() >= next_status:
                    next_status = time.perf_counter() + 1
                    path = output / 'status.json'
                    if path.exists():
                        state = json.loads(path.read_text(encoding='utf-8'))
                        current = (state['status'], state['windows'], state['candidates_saved'])
                        if current != last:
                            announce('状态=%s 窗数=%s 候选=%s 耗时=%.1f秒 增益=%s倍 UTC未验证/上传禁用' %
                                     (*current, state.get('elapsed_seconds', 0), state.get('input_gain', 1)))
                            last = current
        finally:
            # Ctrl+C or a controller error still requests cleanup, without killing
            # unrelated processes or pretending a stuck child has been released.
            deadline = time.perf_counter() + 10
            while process.poll() is None and time.perf_counter() < deadline:
                if output.is_dir():
                    (output / 'STOP').touch(exist_ok=True)
                time.sleep(.05)
            if process.poll() is None:
                raise RuntimeError('停止未完成；保留现场，检查本会话进程和日志')
        path = output / 'status.json'
        if not path.exists():
            announce('启动失败，无状态文件；检查 ' + str(console))
            return 1
        state = json.loads(path.read_text(encoding='utf-8'))
        announce('最终状态=%s 资源释放=%s；结果目录：%s' %
                 (state['status'], state['resources_released'], output))
        return 0 if process.returncode == 0 and state['status'] == 'closed' and state['resources_released'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--session', required=True)
    parser.add_argument('--simulate', action='store_true')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--model', type=Path)
    parser.add_argument('--input-gain', type=int, choices=(1, 16), default=16)
    args = parser.parse_args()
    output = args.output.absolute()
    producer = None if args.simulate else [
        '/usr/bin/ffmpeg', '-nostdin', '-hide_banner', '-v', 'info', '-xerror',
        '-f', 'alsa', '-sample_rate', '48000', '-channels', '2',
        '-i', 'hw:CARD=rockchipes8388,DEV=0', '-map', '0:a:0', '-vn',
        '-af', FILTER, '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1']
    try:
        config = preflight(output, args.session, 2, 512, args.simulate, producer, args.model,
                           input_gain=args.input_gain)
        if output.with_name(output.name + '.console.log').exists():
            raise ValueError('Console log already exists; choose a new session')
        if not args.simulate:
            busy = subprocess.run(['/usr/bin/fuser', '-s', '/dev/snd/pcmC0D0c'])
            if busy.returncode != 1:
                raise ValueError('PCM busy or occupancy check failed; do not stop other services')
        if args.check_only:
            print(json.dumps(config, ensure_ascii=False, indent=2))
            return 0
        command = [sys.executable, '-B', str(Path(__file__).with_name('board_runtime.py')),
                   'run', '--channels', '2', '--session', args.session, '--output', str(output)]
        command += ['--input-gain', str(args.input_gain)]
        if args.simulate:
            command += ['--simulate']
        else:
            command += ['--model', str(args.model), '--pcm-command', json.dumps(producer)]
        with keyboard() as read_key:
            return control(command, output, read_key)
    except KeyboardInterrupt:
        print('已中断控制；检查最终status与本会话日志。')
        return 130
    except (ValueError, OSError, RuntimeError) as failure:
        print(str(failure), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

"""One REC-triggered offline field trial; no receiver and no audio files."""
import argparse
from contextlib import contextmanager
import ctypes as C
import ctypes.util
import json
from pathlib import Path
import select
import signal
import sys
import threading
import time

from board_runtime import preflight, run_session, save_status
from sampling_capture import CapturePCM, verify_abi
from timed_input import TimedCaptureSource
from verify_resampling_relation import producer_command, validate_relation
from whistle_predictor import NpuPredictor

ROOT = Path(__file__).absolute().parents[2]
sys.path.insert(0, str(ROOT / '阶段1_离线手动触发'))
from basketclip.buttons import find_rec_device, INPUT_EVENT, rec_press_timestamp


@contextmanager
def rec_reader():
    import fcntl
    with find_rec_device().open('rb', buffering=0) as stream:
        fcntl.ioctl(stream, 0x40044590, 1)  # EVIOCGRAB; release on every exit.
        try:
            def read(timeout):
                if not select.select([stream], [], [], timeout)[0]:
                    return False
                packet = stream.read(INPUT_EVENT.size)
                if len(packet) != INPUT_EVENT.size:
                    raise OSError('REC input interrupted')
                return rec_press_timestamp(packet) is not None
            yield read
        finally:
            fcntl.ioctl(stream, 0x40044590, 0)


def indicator(state):
    """Use only the board's existing system LED; no GPIO/driver configuration."""
    led = Path('/sys/class/leds/sys_led')
    (led / 'trigger').write_text('timer' if state in ('waiting', 'failed') else 'none')
    if state in ('waiting', 'failed'):
        delay = '1000' if state == 'waiting' else '100'
        (led / 'delay_on').write_text(delay)
        (led / 'delay_off').write_text(delay)
    else:
        (led / 'brightness').write_text('1' if state == 'starting' else '0')


def controlled_run(output, config, read_rec, run, stopped, duration=600,
                   announce=print, show=lambda state: None):
    """No source/model construction until a fresh REC press; one trial only."""
    def publish(state, **extra):
        save_status(output.parent, dict(status=state, session_id=config['session_id'],
                    monotonic_ns=time.monotonic_ns(), observed_realtime_ns=time.time_ns(),
                    raw_audio_saved=False, formal_utc_upload=False, **extra))
        show(state)
        announce(state, flush=True)

    # The control directory is separate from run_session's exclusive output.
    publish('waiting')
    while not stopped.is_set():
        if read_rec(.1):
            break
    if stopped.is_set():
        publish('cancelled')
        return 130
    started = time.monotonic()
    reason = ['external_stop']
    control_error = []
    def controls():
        try:
            while not stopped.is_set():
                if read_rec(.1) and time.monotonic() - started >= .75:
                    reason[0] = 'rec_stop'
                    stopped.set()
        except Exception as failure:
            control_error.append(str(failure))
            reason[0] = 'rec_input_failure'
            stopped.set()
    def expired():
        reason[0] = 'duration_limit'
        stopped.set()
    timer = threading.Timer(duration, expired)
    worker = threading.Thread(target=controls, daemon=True)
    publish('starting', limit_seconds=duration)
    timer.start()
    worker.start()
    try:
        result = run(stopped)
    except Exception as failure:
        publish('failed', stop_reason='runtime_exception', error=str(failure),
                resources_released=False)
        return 1
    finally:
        stopped.set()
        timer.cancel()
        worker.join(timeout=2)
    ok = result['status'] == 'closed' and result['resources_released'] and not control_error
    publish('closed' if ok else 'failed', stop_reason=reason[0],
            control_errors=control_error, runtime_status=result['status'],
            resources_released=result['resources_released'],
            candidates_saved=result['candidates_saved'])
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'model', 'header', 'relation'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    output = args.output.absolute()
    # Control status uses its own parent directory; runtime data stays exclusive.
    if output.name != 'capture' or output.parent.name != args.session:
        raise ValueError('Output must be <new-session>/capture')
    if output.parent.exists():
        raise ValueError('Session already exists; preserve it and use a new session')
    ffmpeg = Path('/usr/bin/ffmpeg')
    relation = validate_relation(args.relation, ffmpeg)
    abi = verify_abi(args.header)
    config = preflight(output, args.session, 2, 512, False,
                       producer_command(ffmpeg), args.model, input_gain=16)
    device = find_rec_device()
    led = Path('/sys/class/leds/sys_led')
    if 'timer' not in (led/'trigger').read_text():
        raise ValueError('System LED timer unavailable')
    import subprocess
    if subprocess.run(['/usr/bin/fuser', '-s', '/dev/snd/pcmC0D0c']).returncode != 1:
        raise ValueError('PCM busy or occupancy check failed; no other process stopped')
    config.update(time_basis='same-source software estimate; absolute error unknown',
                  field_limit_seconds=600, trigger_device=str(device),
                  capture_buffer_target_us=1000000, capture_minimum_read_frames=1024,
                  allow_relative_on_clock_drift=True,
                  relation_evidence=str(args.relation))
    if args.check_only:
        print(json.dumps(dict(config=config, abi=abi, resampling=relation), indent=2))
        return 0
    output.parent.mkdir(parents=True, exist_ok=False)
    stopped = threading.Event()
    old = {sig: signal.signal(sig, lambda *_: stopped.set())
           for sig in (signal.SIGINT, signal.SIGTERM)}
    diagnostic = None
    def run(event):
        if subprocess.run(['/usr/bin/fuser', '-s', '/dev/snd/pcmC0D0c']).returncode != 1:
            raise ValueError('PCM became busy while waiting; no other process stopped')
        def source(log):
            nonlocal diagnostic
            diagnostic = (output/'sampling-diagnostics.jsonl').open('x', encoding='utf-8')
            library = C.CDLL(ctypes.util.find_library('asound') or 'libasound.so.2')
            return TimedCaptureSource(ffmpeg, args.relation,
                lambda: CapturePCM(library, 'hw:CARD=rockchipes8388,DEV=0',
                                   buffer_us=1000000, minimum_frames=1024),
                args.session+':segment-0001', diagnostic, log=log,
                allow_relative_on_clock_drift=True)
        return run_session(output, config, source, lambda: NpuPredictor(args.model),
                           event, timing_factory=lambda segment, src: src.timing)
    try:
        with rec_reader() as read:
            return controlled_run(output, config, read, run, stopped, show=indicator)
    except BaseException:
        indicator('failed')
        raise
    finally:
        stopped.set()
        if diagnostic is not None:
            diagnostic.close()
        for sig, handler in old.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    sys.exit(main())

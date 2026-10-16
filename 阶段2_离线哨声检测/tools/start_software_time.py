"""User-space-started fixed-NPU runtime with same-source software time diagnostics."""
import argparse
import ctypes as C
import ctypes.util
import json
import signal
from pathlib import Path
import subprocess
import sys
import threading

from board_runtime import preflight, run_session
from sampling_capture import CapturePCM, verify_abi
from start_space import keyboard
from timed_input import TimedCaptureSource
from verify_resampling_relation import producer_command, validate_relation
from whistle_predictor import NpuPredictor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--session', required=True)
    parser.add_argument('--header', required=True, type=Path)
    parser.add_argument('--relation', required=True, type=Path)
    parser.add_argument('--ffmpeg', default='/usr/bin/ffmpeg', type=Path)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--url', help='Explicit label receiver /events URL; absent means no upload')
    parser.add_argument('--allow-software-time', action='store_true')
    parser.add_argument('--stage1', type=Path, default=Path('/home/basketclip-project/stage1'))
    args = parser.parse_args()
    if bool(args.url) != args.allow_software_time:
        parser.error('Automatic upload requires both --url and --allow-software-time')
    if args.url:
        from eventlink import stage1_path
        stage1_path(args.stage1)
    output = args.output.absolute()
    relation = validate_relation(args.relation, args.ffmpeg)
    abi = verify_abi(args.header)
    config = preflight(output, args.session, 2, 512, False,
                       producer_command(args.ffmpeg), args.model, input_gain=16)
    config.update(time_basis='same-source software estimate', sampling_utc_verified=False,
                  formal_utc_upload=False, relation_evidence=str(args.relation))
    if args.url:
        config.update(formal_utc_upload=True, upload_reason='explicit software-time engineering policy',
                      label_receiver=args.url, time_quality='software_estimate')
    if subprocess.run(['/usr/bin/fuser', '-s', '/dev/snd/pcmC0D0c']).returncode != 1:
        raise ValueError('PCM busy or occupancy check failed')
    if args.check_only:
        print(json.dumps(dict(config=config, abi=abi, resampling=relation), indent=2))
        return 0
    library = C.CDLL(ctypes.util.find_library('asound') or 'libasound.so.2')
    stopped = threading.Event()
    diagnostic = None
    with keyboard() as key:
        print('空格开始软件时间检测；再次空格停止。固定16倍/FP16/0.65/v1，'+
              ('软件估计标签上传已显式启用。' if args.url else '上传禁用。'), flush=True)
        while key(.1) != ' ':
            pass
        def stop_keys():
            try:
                while not stopped.is_set():
                    if key(.1) == ' ':
                        stopped.set()
            except BaseException:
                stopped.set()
        keys = threading.Thread(target=stop_keys, daemon=True)
        keys.start()
        old_handlers = {sig: signal.signal(sig, lambda *_: stopped.set())
                        for sig in (signal.SIGINT, signal.SIGTERM)}
        timer = threading.Timer(10, stopped.set)
        timer.start()
        def source_factory(log):
            nonlocal diagnostic
            diagnostic = (output/'sampling-diagnostics.jsonl').open('x', encoding='utf-8')
            return TimedCaptureSource(args.ffmpeg, args.relation,
                lambda: CapturePCM(library, 'hw:CARD=rockchipes8388,DEV=0'),
                args.session+':segment-0001', diagnostic, log=log)
        try:
            label_factory = None
            if args.url:
                from automatic_labels import AutomaticLabels
                label_factory = lambda path: AutomaticLabels(path, args.session, args.url,
                                                             allow_software_time=True)
            result = run_session(output, config, source_factory, lambda: NpuPredictor(args.model),
                                 stopped, timing_factory=lambda segment, source: source.timing,
                                 label_factory=label_factory)
        finally:
            stopped.set()
            timer.cancel()
            keys.join(timeout=2)
            if diagnostic is not None:
                diagnostic.close()
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status']=='closed' and result['resources_released'] else 1


if __name__ == '__main__':
    sys.exit(main())

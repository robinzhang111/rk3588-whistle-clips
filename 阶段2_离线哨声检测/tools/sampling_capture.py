"""Separate user-started, <=10 second ALSA diagnostic. No model/audio files/upload."""
import argparse
from contextlib import contextmanager
import ctypes as C
from datetime import datetime, timezone
import errno
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from sampling_probe import AlsaStatusReader, AudioConfig, AudioReport, Timespec, SamplingProbe


def layout():
    report = AudioReport(1, 10, 1, 0x12345678)
    config = AudioConfig(2, 1)
    return dict(pointer=C.sizeof(C.c_void_p), long=C.sizeof(C.c_long),
        uint=C.sizeof(C.c_uint), timespec=C.sizeof(Timespec),
        nsec_offset=Timespec.nanoseconds.offset, report=C.sizeof(AudioReport),
        accuracy_offset=AudioReport.accuracy.offset, config=C.sizeof(AudioConfig),
        report_hex=bytes(report).hex(), config_hex=bytes(config).hex())


def verify_abi(header):
    """Compile only type definitions extracted from pinned upstream pcm.h; no PCM."""
    if sys.platform != 'linux' or C.sizeof(C.c_long) != 8 or sys.byteorder != 'little':
        raise RuntimeError('Linux little-endian LP64 required')
    raw = header.read_bytes()
    provenance = json.loads(header.with_name('source.json').read_text(encoding='utf-8'))
    if hashlib.sha256(raw).hexdigest() != provenance['header_sha256']:
        raise ValueError('Pinned header hash mismatch')
    source = raw.decode()
    declarations = []
    for name in ['config', 'report']:
        match = re.search(r'typedef struct _snd_pcm_audio_tstamp_' + name +
                          r'\s*\{.*?\}\s*snd_pcm_audio_tstamp_' + name + r'_t;', source, re.S)
        if not match:
            raise ValueError('Missing upstream timestamp declaration')
        declarations.append(match.group())
    program = '#include <stdio.h>\n#include <stddef.h>\n#include <time.h>\n' + '\n'.join(declarations) + r'''
int main(void) {
 snd_pcm_audio_tstamp_report_t r = {0}; snd_pcm_audio_tstamp_config_t c = {0};
 r.valid=1; r.actual_type=10; r.accuracy_report=1; r.accuracy=0x12345678;
 c.type_requested=2; c.report_delay=1;
 printf("{\"pointer\":%zu,\"long\":%zu,\"uint\":%zu,\"timespec\":%zu,\"nsec_offset\":%zu,\"report\":%zu,\"accuracy_offset\":%zu,\"config\":%zu,\"report_hex\":\"",
 sizeof(void*),sizeof(long),sizeof(unsigned int),sizeof(struct timespec),offsetof(struct timespec,tv_nsec),sizeof(r),offsetof(snd_pcm_audio_tstamp_report_t,accuracy),sizeof(c));
 for(size_t i=0;i<sizeof(r);i++) printf("%02x",((unsigned char*)&r)[i]);
 printf("\",\"config_hex\":\"");
 for(size_t i=0;i<sizeof(c);i++) printf("%02x",((unsigned char*)&c)[i]);
 puts("\"}"); return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='sampling-abi-') as temporary:
        executable = str(Path(temporary)/'abi-check')
        built = subprocess.run(['gcc', '-std=c11', '-x', 'c', '-', '-o', executable],
            input=program, text=True, capture_output=True, timeout=15)
        if built.returncode:
            raise RuntimeError('ABI compilation failed: ' + built.stderr)
        checked = subprocess.run([executable], text=True, capture_output=True, check=True, timeout=5)
    native = json.loads(checked.stdout)
    if native != layout():
        raise RuntimeError('C/ctypes ABI mismatch: ' + json.dumps(dict(native=native, ctypes=layout())))
    return dict(status='passed', native=native, header_sha256=provenance['header_sha256'],
                source_url=provenance['url'], pcm_opened=False,
                scope='native type layout only; not driver timestamp capability')


def bind(library, name, arguments, result=C.c_int):
    function = getattr(library, name)
    function.argtypes, function.restype = arguments, result
    return function


def checked(result, operation):
    if result < 0:
        raise OSError(-result, operation + ' failed; no retry/recovery')
    return result


class CapturePCM:
    """Own one exclusive nonblocking capture handle; fixed raw diagnostic format."""
    def __init__(self, library, device, *, buffer_us=100000, minimum_frames=0):
        if type(buffer_us) is not int or not 100000 <= buffer_us <= 1000000:
            raise ValueError('Capture buffer target must be 100000..1000000 microseconds')
        if type(minimum_frames) is not int or not 0 <= minimum_frames <= 1024:
            raise ValueError('Minimum read must be 0..1024 frames')
        self.minimum_frames = minimum_frames
        self.library, self.handle = library, C.c_void_p()
        self.started = False
        ptr, integer, uint = C.c_void_p, C.c_int, C.c_uint
        signatures = {
            'open': ([C.POINTER(ptr), C.c_char_p, integer, integer], integer),
            'close': ([ptr], integer), 'drop': ([ptr], integer), 'start': ([ptr], integer),
            'format_value': ([C.c_char_p], integer),
            'set_params': ([ptr, integer, integer, uint, uint, integer, uint], integer),
            'readi': ([ptr, ptr, C.c_ulong], C.c_long),
            'avail_update': ([ptr], C.c_long),
            'hw_params_get_rate': ([ptr, C.POINTER(uint), C.POINTER(integer)], integer),
            'hw_params_get_channels': ([ptr, C.POINTER(uint)], integer),
            'hw_params_get_format': ([ptr, C.POINTER(integer)], integer),
            'sw_params': ([ptr, ptr], integer),
        }
        for kind in ['hw', 'sw']:
            signatures[kind+'_params_malloc'] = ([C.POINTER(ptr)], integer)
            signatures[kind+'_params_free'] = ([ptr], None)
            signatures[kind+'_params_current'] = ([ptr, ptr], integer)
        for name in ['mode', 'type']:
            signatures['sw_params_set_tstamp_'+name] = ([ptr, ptr, integer], integer)
            signatures['sw_params_get_tstamp_'+name] = ([ptr, C.POINTER(integer)], integer)
        for name, (arguments, result) in signatures.items():
            bind(library, 'snd_pcm_'+name, arguments, result)
        checked(library.snd_pcm_open(C.byref(self.handle), device.encode(), 1, 1), 'open capture nonblocking')
        try:
            fmt = checked(library.snd_pcm_format_value(b'S16_LE'), 'S16_LE format')
            # Upstream pcm.h: RW_INTERLEAVED=3; no software resampling.
            checked(library.snd_pcm_set_params(self.handle, fmt, 3, 2, 48000, 0, buffer_us), 'set_params')
            with self.params('hw') as params:
                rate, channels, actual_format, direction = C.c_uint(), C.c_uint(), C.c_int(), C.c_int()
                checked(library.snd_pcm_hw_params_get_rate(params, C.byref(rate), C.byref(direction)), 'get_rate')
                checked(library.snd_pcm_hw_params_get_channels(params, C.byref(channels)), 'get_channels')
                checked(library.snd_pcm_hw_params_get_format(params, C.byref(actual_format)), 'get_format')
                if (rate.value, channels.value, actual_format.value) != (48000, 2, fmt):
                    raise ValueError('Negotiated PCM format differs; stop without fallback')
            with self.params('sw') as params:
                checked(library.snd_pcm_sw_params_set_tstamp_mode(self.handle, params, 1), 'enable timestamps')
                checked(library.snd_pcm_sw_params_set_tstamp_type(self.handle, params, 1), 'request MONOTONIC')
                checked(library.snd_pcm_sw_params(self.handle, params), 'apply sw_params')
            with self.params('sw') as params:
                mode, clock = C.c_int(), C.c_int()
                checked(library.snd_pcm_sw_params_get_tstamp_mode(params, C.byref(mode)), 'get timestamp mode')
                checked(library.snd_pcm_sw_params_get_tstamp_type(params, C.byref(clock)), 'get timestamp type')
                if (mode.value, clock.value) != (1, 1):
                    raise ValueError('Timestamp readback differs; stop without fallback')
            self.configuration = dict(rate=48000, channels=2, format='S16_LE',
                timestamp_mode='enabled', timestamp_clock='monotonic',
                requested_audio_type=2, requested_audio_name='LINK', report_delay=True)
            self.configuration.update(buffer_target_us=buffer_us, minimum_read_frames=minimum_frames)
            self.buffer = C.create_string_buffer(1024*2*2)
            self.reader = AlsaStatusReader(library, AudioConfig(2, 1))
        except BaseException:
            self.close()
            raise

    @contextmanager
    def params(self, kind):
        params = C.c_void_p()
        checked(getattr(self.library, 'snd_pcm_'+kind+'_params_malloc')(C.byref(params)), kind+' params malloc')
        try:
            checked(getattr(self.library, 'snd_pcm_'+kind+'_params_current')(self.handle, params), kind+' params current')
            yield params
        finally:
            getattr(self.library, 'snd_pcm_'+kind+'_params_free')(params)

    def start(self):
        checked(self.library.snd_pcm_start(self.handle), 'start')
        self.started = True

    def read(self):
        if self.minimum_frames:
            available = self.library.snd_pcm_avail_update(self.handle)
            if available == -errno.EAGAIN:
                return 0
            checked(available, 'avail_update')
            if available < self.minimum_frames:
                return 0
        frames = self.library.snd_pcm_readi(self.handle, self.buffer, 1024)
        if frames == -errno.EAGAIN:
            return 0
        checked(frames, 'readi')
        if frames > 1024:
            raise ValueError('readi exceeded requested frames')
        return frames

    def status(self):
        return self.reader.read(self.handle)

    def close(self):
        if not self.handle.value:
            return
        errors = []
        if self.started:
            result = self.library.snd_pcm_drop(self.handle)
            if result < 0:
                errors.append('drop=' + str(result))
        result = self.library.snd_pcm_close(self.handle)
        self.handle = C.c_void_p()
        if result < 0:
            errors.append('close=' + str(result))
        if errors:
            raise RuntimeError('PCM cleanup failed: ' + ', '.join(errors))


def capture(factory, probe, emit, stop, seconds, clock=time):
    """No automatic retry; status/read/write failures poison the segment."""
    if not 0 < seconds <= 10:
        raise ValueError('Capture duration must be in (0,10] seconds')
    pcm = None
    summary = dict(status='failed', frames=0, formal_utc_upload=False, audio_saved=False,
                   resources_released=False, error=None, cleanup_error=None)
    try:
        pcm = factory()
        emit(dict(kind='configuration', **pcm.configuration, formal_utc_upload=False))
        pcm.start()
        start = clock.monotonic_ns()
        summary['close_reason'] = 'duration_limit'
        while clock.monotonic_ns() - start < seconds*10**9:
            if stop():
                summary['close_reason'] = 'user_stop'
                break
            frames = pcm.read()
            if not frames:
                clock.sleep(.005)
                continue
            summary['frames'] += frames
            before = clock.monotonic_ns()
            wall = clock.time_ns()
            paired = clock.monotonic_ns()
            snapshot = pcm.status()
            after = clock.monotonic_ns()
            stamp = datetime.fromtimestamp(wall//10**9, timezone.utc).replace(
                microsecond=(wall%10**9)//1000).isoformat().replace('+00:00', 'Z')
            row = probe.observe(summary['frames'], snapshot, paired, stamp,
                'userspace bracket only; NTP quality and ADC correspondence unverified',
                'monotonic', 'enabled')
            row.update(observed_realtime_ns=wall, pair_monotonic_before_ns=before,
                       pair_monotonic_after_ns=paired, status_monotonic_after_ns=after,
                       read_frames=frames)
            emit(row)
            if probe.mapping.invalid_reason:
                raise RuntimeError(probe.mapping.invalid_reason)
        if not summary['frames']:
            raise RuntimeError('No captured frames')
        summary['elapsed_ns'] = clock.monotonic_ns() - start
        summary['status'] = 'closed'
    except BaseException as failure:
        probe.interrupt('capture_failure: ' + str(failure))
        summary['error'] = type(failure).__name__ + ': ' + str(failure)
    finally:
        if pcm is not None:
            try:
                pcm.close()
                summary['resources_released'] = True
            except BaseException as failure:
                summary.update(status='failed', cleanup_error=str(failure))
        # A factory failure must close its partially initialized handle itself.
        summary['segment_invalid_reason'] = probe.mapping.invalid_reason
    return summary


def wait_for_start(read_key, clock=time):
    while True:
        key = read_key()
        if key == ' ':
            return True
        if key == '\x03':
            return False
        clock.sleep(.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--header', required=True, type=Path)
    parser.add_argument('--abi-only', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--device', default='hw:CARD=rockchipes8388,DEV=0')
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--clock-tolerance-ns', type=int, default=5000000,
                        help='Diagnostic jump threshold only; never an accuracy claim')
    args = parser.parse_args()
    if not 0 < args.seconds <= 10 or args.clock_tolerance_ns < 0:
        parser.error('seconds in (0,10], nonnegative diagnostic tolerance required')
    abi = verify_abi(args.header)
    if args.abi_only:
        print(json.dumps(abi, indent=2))
        return 0
    if args.output is None:
        parser.error('--output required for capture')
    if args.output.exists():
        raise FileExistsError('Choose a new output directory')
    # Import keyboard only here; standard-library tty, no detection/runtime imports.
    import select
    import termios
    import tty
    with open('/dev/tty', 'r') as terminal:
        previous = termios.tcgetattr(terminal)
        try:
            tty.setcbreak(terminal.fileno())
            def read_key():
                ready, _, _ = select.select([terminal], [], [], 0)
                return terminal.read(1) if ready else None
            def stop():
                return read_key() in [' ', '\x03']
            print('ABI通过。空格开始一次采样探针；再次空格停止，最多10秒。', flush=True)
            if not wait_for_start(read_key):
                return 130
            args.output.mkdir(parents=True, exist_ok=False)
            with (args.output/'abi.json').open('x', encoding='utf-8') as output:
                json.dump(abi, output, indent=2)
            import ctypes.util
            library = C.CDLL(ctypes.util.find_library('asound') or 'libasound.so.2')
            probe = SamplingProbe(args.output.name, tolerance_ns=args.clock_tolerance_ns)
            with (args.output/'probe-diagnostics.jsonl').open('x', encoding='utf-8') as output:
                def emit(row):
                    output.write(json.dumps(row, ensure_ascii=False) + '\n')
                    output.flush()
                summary = capture(lambda: CapturePCM(library, args.device), probe,
                                  emit, stop, args.seconds)
            summary.update(clock_tolerance_ns=args.clock_tolerance_ns,
                           tolerance_is_accuracy_claim=False)
            with (args.output/'summary.json').open('x', encoding='utf-8') as output:
                json.dump(summary, output, ensure_ascii=False, indent=2)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            return 0 if summary['status'] == 'closed' else 1
        finally:
            termios.tcsetattr(terminal, termios.TCSADRAIN, previous)


if __name__ == '__main__':
    sys.exit(main())

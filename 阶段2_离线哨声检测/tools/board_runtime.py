"""Unified session lifecycle; relative candidates only until real UTC acceptance."""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import time

ROOT = Path(__file__).absolute().parents[2]
sys.path.insert(0, str(ROOT / '阶段3_实时传输'))
from auto_events import candidate_event
from continuous_input import ProcessSource, run_segment
from sample_time import SegmentTime
from whistle_predictor import NpuPredictor, RKNN_SHA, sha
from simulate_continuous_input import DeterministicPredictor


def save_status(output, state):
    # Only this new session's current status is replaced; historical runs stay intact.
    path = output / 'status.tmp'
    with path.open('w', encoding='utf-8') as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(20):
        try:
            os.replace(path, output / 'status.json')
            break
        except PermissionError:
            # Windows readers can briefly hold the destination without delete sharing.
            if os.name != 'nt' or attempt == 19:
                raise
            time.sleep(.01)


def preflight(output, session, channels, chunk_frames, simulate, command=None,
              model=None, duration=None, input_gain=1):
    if not isinstance(session, str) or not session.strip():
        raise ValueError('Explicit session ID required')
    if isinstance(channels, bool) or not isinstance(channels, int) or channels < 1:
        raise ValueError('Explicit positive channel count required')
    if isinstance(chunk_frames, bool) or not isinstance(chunk_frames, int) or not 1 <= chunk_frames <= 4096:
        raise ValueError('Chunk frames must be 1..4096')
    if output.exists():
        raise ValueError('Output must be a new session directory')
    if isinstance(input_gain, bool) or input_gain not in (1, 16):
        raise ValueError('Input gain must be 1 or 16')
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError('Simulation duration must be finite and positive')
    if simulate:
        if command is not None or model is not None:
            raise ValueError('Simulation cannot launch a producer or model')
    else:
        if duration is not None:
            raise ValueError('Duration applies only to simulation')
        if (not isinstance(command, list) or not command
                or any(not isinstance(x, str) or not x for x in command)
                or shutil.which(command[0]) is None):
            raise ValueError('Explicit adapted PCM producer argv with existing executable required')
        if model is None or not model.is_file() or sha(model) != RKNN_SHA:
            raise ValueError('Fixed FP16 RKNN hash mismatch or missing model')
        if importlib.util.find_spec('rknnlite') is None:
            raise ValueError('Existing rknnlite runtime required; no installation performed')
    return dict(status='preflight_passed', session_id=session,
                mode='simulation' if simulate else 'board_npu', channels=channels,
                chunk_frames=chunk_frames, threshold=.65, rule='v1',
                input_gain=input_gain, gain_stage='adapted float PCM before FFT',
                model_sha256=None if simulate else RKNN_SHA,
                producer_command=command, sample_rate=22050,
                pcm_contract='continuous interleaved float32 little-endian; externally adapted',
                input_format_hardware_verified=False, sampling_utc_verified=False,
                formal_utc_upload=False, upload_reason='real sampling UTC not accepted',
                raw_audio_saved=False, feature_arrays_saved=False,
                predictor='injected 0.9; no model execution' if simulate else 'fixed FP16 NPU')


class SimulationSource:
    """Small paced synthetic blocks, cancellable while waiting; no audio file."""
    def __init__(self, channels, duration, stopped):
        self.channels, self.stopped = channels, stopped
        self.limit = None if duration is None else max(1, round(duration * 22050))
        self.sent, self.closed = 0, False
        self.started = time.perf_counter()

    def read(self, size, stop):
        import numpy as np
        if stop():
            return None
        if self.limit is not None and self.sent >= self.limit:
            return b''
        count = size // (4 * self.channels)
        if self.limit is not None:
            count = min(count, self.limit - self.sent)
        deadline = self.started + (self.sent + count) / 22050
        while time.perf_counter() < deadline:
            if stop() or self.stopped.wait(min(.05, max(0, deadline - time.perf_counter()))):
                return None
        self.sent += count
        return np.ones((count, self.channels), dtype='<f4').tobytes()

    def close(self):
        self.closed = True


def run_session(output, config, source_factory, predictor_factory, stopped=None, timing_factory=None,
                label_factory=None):
    """Factories open only after exclusive session creation; A owns segment cleanup."""
    stopped = stopped if stopped is not None else threading.Event()
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    segment = config['session_id'] + ':segment-0001'
    clock = SegmentTime(segment)  # No real anchor acquisition exists in this version.
    state = dict(config, status='starting', pid=os.getpid(), segment_id=segment,
                 windows=0, candidates_saved=0, production_gate_rejections=0,
                 error=None, cleanup_errors=[], resources_released=False)
    source = predictor = stream = log = None
    labels = None
    handed_to_segment = False
    candidate_writer_failed = False
    last_status = 0.

    def publish(force=False):
        nonlocal last_status
        now = time.perf_counter()
        if force or now - last_status >= .5:
            state['elapsed_seconds'] = now - started
            if clock.invalid_reason:
                state['sampling_time_invalid_reason'] = clock.invalid_reason
                state['time_basis'] = 'relative samples; software time invalid'
            save_status(output, state)
            last_status = now

    def stop():
        if (output / 'STOP').exists():
            stopped.set()
        if stopped.is_set():
            state['status'] = 'stopping'
        if labels is not None:
            labels.pump()
        publish()
        return stopped.is_set()

    def observe(index, features, scores, silent):
        state['windows'] = index + 1

    def emit(candidate):
        nonlocal candidate_writer_failed
        if candidate_writer_failed:
            raise OSError('Candidate journal failed; preserve for inspection')
        if candidate['closed_by'] in ('interrupted', 'incomplete_frame', 'source_or_processing_error'):
            clock.invalidate(candidate['closed_by'])
        timing = clock.candidate_time(candidate)
        accepted = False
        try:
            candidate_event(candidate, timing, config['session_id'], state['candidates_saved'] + 1,
                            allow_software_time=labels is not None and labels.allow_software_time)
        except ValueError:
            pass
        else:
            if labels is None:
                raise RuntimeError('Relative candidate escaped production time gate')
            accepted = True
        row = dict(session_id=config['session_id'], candidate=candidate,
                   timing=timing, formal_utc_upload=accepted, production_path_blocked=not accepted)
        try:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        except Exception as failure:
            candidate_writer_failed = True
            state['candidate_save_error'] = str(failure)
            raise
        state['candidates_saved'] += 1
        state['production_gate_rejections'] += int(not accepted)
        if accepted:
            try:
                labels.save(candidate, timing)
            except BaseException:
                candidate_writer_failed = True
                raise

    try:
        publish(True)
        stream = (output / 'candidates.relative.jsonl').open('x', encoding='utf-8')
        log = (output / 'producer.log').open('xb')
        if label_factory is not None:
            labels = label_factory(output)
        predictor = predictor_factory()
        if not stop():
            source = source_factory(log)
            if timing_factory is not None:
                clock = timing_factory(segment, source)
                if clock.segment_id != segment:
                    raise ValueError('Timing belongs to another segment')
                state['time_basis'] = 'same-source software estimate; absolute error unmeasured'
            state['status'] = 'running'
            publish(True)
            handed_to_segment = True
            report = run_segment(source, predictor, emit, config['channels'], segment,
                                 config['chunk_frames'], stop=stop, observe=observe,
                                 input_gain=config.get('input_gain', 1))
            state.update(segment_report=report, windows=report['windows'],
                         status='failed' if report['error'] else 'closed', error=report['error'])
        else:
            state.update(status='closed', close_reason='stop_before_source_start')
    except KeyboardInterrupt:
        state.update(status='closed', close_reason='interrupted_during_startup')
    except Exception as failure:
        state.update(status='failed', error=str(failure))
    finally:
        if labels is not None:
            try:
                state['automatic_labels'] = labels.close()
            except BaseException as failure:
                state['cleanup_errors'].append('automatic_labels: '+str(failure))
        # A closes source/predictor even if finish raises. Startup still belongs here.
        resources = [] if handed_to_segment else [source, predictor]
        for resource in resources + [stream, log]:
            if resource is not None:
                try:
                    resource.close()
                except Exception as failure:
                    state['cleanup_errors'].append(str(failure))
        if state['cleanup_errors']:
            state['status'] = 'failed'
        # A cleanup exceptions propagate; never claim release after an unknown failure.
        state['resources_released'] = not state['cleanup_errors'] and not (
            handed_to_segment and 'segment_report' not in state)
        publish(True)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('check', 'run'):
        command = sub.add_parser(name)
        command.add_argument('--output', type=Path, required=True)
        command.add_argument('--session', required=True)
        command.add_argument('--channels', type=int, required=True)
        command.add_argument('--chunk-frames', type=int, default=512)
        command.add_argument('--simulate', action='store_true')
        command.add_argument('--duration', type=float)
        command.add_argument('--pcm-command', help='JSON argv; never shell; externally verified PCM contract')
        command.add_argument('--model', type=Path)
        command.add_argument('--input-gain', type=int, choices=(1, 16), default=1)
    for name in ('status', 'stop'):
        command = sub.add_parser(name)
        command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.absolute()
    try:
        if args.action in ('status', 'stop'):
            state = json.loads((output / 'status.json').read_text(encoding='utf-8'))
            if args.action == 'stop' and state['status'] in ('starting', 'running', 'stopping'):
                # Cooperative request; never signal a possibly recycled PID.
                with (output / 'STOP').open('a', encoding='utf-8'):
                    pass
                print('Stop requested; confirm terminal status and resources_released')
            else:
                print(json.dumps(state, ensure_ascii=False, indent=2))
            return 0
        command = None if args.pcm_command is None else json.loads(args.pcm_command)
        config = preflight(output, args.session, args.channels, args.chunk_frames,
                           args.simulate, command, args.model, args.duration, args.input_gain)
        if args.action == 'check':
            print(json.dumps(config, ensure_ascii=False, indent=2))
            return 0
        stopped = threading.Event()
        old_handlers = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.signal(sig, lambda *_: stopped.set())
        try:
            if args.simulate:
                source = lambda log: SimulationSource(args.channels, args.duration, stopped)
                predictor = DeterministicPredictor
            else:
                source = lambda log: ProcessSource(command, args.channels, args.chunk_frames, log)
                predictor = lambda: NpuPredictor(args.model)
            state = run_session(output, config, source, predictor, stopped)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 1 if state['status'] == 'failed' else 0
    except (ValueError, OSError) as failure:
        print(str(failure), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

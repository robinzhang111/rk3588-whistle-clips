"""Local evidence for unified entry; synthetic sources only, no network or model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from board_runtime import ROOT, preflight, run_session
from simulate_continuous_input import DeterministicPredictor, SimulatedSource


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    # F's protected inputs include original source, earlier evidence and media.
    previous = ROOT / '阶段6_项目展示/outputs/core-synthetic-20261014-01'
    protected = json.loads((previous / 'verification.json').read_text(encoding='utf-8'))['protected_sha256']
    for path in previous.rglob('*'):
        if path.is_file():
            protected[str(path)] = digest(path)
    for path in [ROOT/'阶段6_项目展示/simulate_core_loop.py',
                 ROOT/'阶段6_项目展示/本地合成核心联调与验收.md']:
        protected[str(path)] = digest(path)
    assert all(digest(Path(path)) == expected for path, expected in protected.items())
    output.mkdir(parents=True, exist_ok=False)
    (output / 'protected-before.json').write_text(json.dumps(protected, ensure_ascii=False, indent=2), encoding='utf-8')
    groups = [
        ('阶段2_离线哨声检测/tests', 'test_board_runtime.py'),
        ('阶段2_离线哨声检测/tests', 'test_continuous_input.py'),
        ('阶段2_离线哨声检测/tests', 'test_stream_whistle.py'),
        ('阶段2_离线哨声检测/tests', 'test_continuous_whistle.py'),
        ('阶段2_离线哨声检测/tests', 'test_sample_time.py'),
        ('阶段3_实时传输/tests', 'test_auto_events.py'),
        ('阶段1_离线手动触发/tests', 'test_mapped.py')]
    tests = []
    env = dict(os.environ, PYTHONPATH=str(ROOT/'阶段1_离线手动触发'))
    for folder, pattern in groups:
        result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover',
                                 '-s', str(ROOT/folder), '-p', pattern, '-v'], capture_output=True, env=env)
        (output / (pattern + '.log')).write_bytes(result.stdout + result.stderr)
        tests.append(dict(pattern=pattern, exit_code=result.returncode))
        assert result.returncode == 0, pattern
    sessions = []
    for name, interrupt in [('source-end', False), ('interrupted', True)]:
        path = output / name
        config = preflight(path, 'synthetic-entry-' + name, 1, 777, True)
        source, predictor = SimulatedSource(22050, 1, interrupt), DeterministicPredictor()
        state = run_session(path, config, lambda log: source, lambda: predictor)
        assert source.closed and predictor.closed and state['resources_released']
        assert state['segment_report']['windows'] == 42 and state['candidates_saved'] == 1
        assert state['production_gate_rejections'] == 1 and not state['formal_utc_upload']
        sessions.append(state)
    script = ROOT / '阶段2_离线哨声检测/tools/board_runtime.py'
    live = output / 'continuous-stop'
    command = [sys.executable, '-B', str(script)]
    with (output / 'continuous-cli.log').open('xb') as log:
        process = subprocess.Popen(command + ['run', '--simulate', '--channels', '1',
                                   '--session', 'synthetic-entry-continuous', '--output', str(live)],
                                   stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.perf_counter() + 10
            while time.perf_counter() < deadline:
                if (live/'status.json').exists():
                    state = json.loads((live/'status.json').read_text(encoding='utf-8'))
                    if state['windows'] >= 42:
                        break
                assert process.poll() is None, 'entry exited early'
                time.sleep(.05)
            else:
                raise AssertionError('continuous entry did not report progress')
            (output/'status-running.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
            result = subprocess.run(command + ['stop', '--output', str(live)], capture_output=True)
            assert result.returncode == 0 and process.wait(10) == 0
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(10)
    state = json.loads((live/'status.json').read_text(encoding='utf-8'))
    assert state['resources_released'] and state['segment_report']['close_reason'] == 'normal_stop'
    assert state['candidates_saved'] == 1 and not state['formal_utc_upload']
    sessions.append(state)
    assert all(digest(Path(path)) == expected for path, expected in protected.items())
    assert not any(p.suffix.lower() in ('.wav', '.npy', '.npz', '.rknn', '.sqlite3') for p in output.rglob('*'))
    report = dict(status='passed', scope='unified entry local software simulation only',
                  tests=tests, sessions=sessions, protected_sha256=protected,
                  source_and_history_unchanged=True, hardware_tested=False,
                  model_executed=False, network_used=False, formal_real_event_upload=False,
                  real_sampling_utc_verified=False, raw_audio_saved=False)
    (output/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Unified entry local verification passed: 7 test groups, 3 independent synthetic sessions')


if __name__ == '__main__':
    main()

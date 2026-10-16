"""Paced synthetic PCM lifecycle evidence; deterministic scores, no model run."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
from continuous_input import run_segment


class SimulatedSource:
    def __init__(self, frames, channels, interrupt=False):
        self.frames, self.channels, self.interrupt = frames, channels, interrupt
        self.sent = 0
        self.started = time.perf_counter()
        self.closed = False
    def read(self, size, stop):
        if self.sent == self.frames:
            if self.interrupt:
                raise KeyboardInterrupt()
            return b''
        count = min(size // (self.channels * 4), self.frames - self.sent)
        self.sent += count
        time.sleep(max(0, self.started + self.sent / 22050 - time.perf_counter()))
        return np.ones((count, self.channels), dtype='<f4').tobytes()
    def close(self):
        self.closed = True


class DeterministicPredictor:
    def __init__(self):
        self.closed = False
    def __call__(self, feature):
        return .9
    def close(self):
        self.closed = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    segments = []
    with (args.output / 'events.jsonl').open('x', encoding='utf-8') as output:
        def emit(event):
            output.write(json.dumps(event)+'\n')
            output.flush()
        for name, size, interrupt in [('paced-512', 512, False), ('paced-777', 777, False),
                                       ('interrupted', 512, True), ('rebuilt', 512, False)]:
            source, predictor = SimulatedSource(22050, 4, interrupt), DeterministicPredictor()
            report = run_segment(source, predictor, emit, 4, name, size)
            assert source.closed and predictor.closed and report['retained_after_finish'] == 0
            report.update(source_released=True, predictor_released=True,
                          synthetic_sample_seconds=1., input_queue_frames=0)
            segments.append(report)
    summary = dict(status='passed', predictor='injected deterministic 0.9; no ONNX/NPU execution',
                   scope='synthetic paced local software; not microphone/realtime hardware acceptance',
                   segments=segments, formal_utc_upload=False, raw_audio_saved=False,
                   max_window_audio_bytes=16384, window_capacity_is_not_process_memory=True)
    with (args.output / 'summary.json').open('x', encoding='utf-8') as output:
        json.dump(summary, output, indent=2)
    assert sorted(p.name for p in args.output.iterdir()) == ['events.jsonl', 'summary.json']
    print(json.dumps(summary))


if __name__ == '__main__':
    main()

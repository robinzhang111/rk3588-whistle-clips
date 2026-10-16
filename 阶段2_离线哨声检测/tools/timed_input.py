"""One owned raw source supplies both software timing and existing FFmpeg filter."""
import json
import threading
import time

from continuous_input import ProcessSource, SourceError
from software_time import SoftwareTime
from verify_resampling_relation import producer_command, validate_relation


def continuous_status_step(previous, frames, snapshot, tolerance_ns):
    """Local software check only; never proves physical sampling continuity."""
    boundary = frames + snapshot['delay']
    stamp = snapshot['htstamp_ns']
    if previous is None or snapshot['state'] != 3 or snapshot['delay'] < 0:
        return False
    old_boundary, old_stamp = previous
    return (boundary >= old_boundary and stamp >= old_stamp
            and abs((stamp-old_stamp)*48000-(boundary-old_boundary)*10**9)
            <= tolerance_ns*48000)


class TimedCaptureSource(ProcessSource):
    def __init__(self, ffmpeg, evidence, pcm_factory, segment_id, diagnostic,
                 chunk_frames=512, log=None, clock=time, allow_relative_on_clock_drift=False):
        validate_relation(evidence, ffmpeg)
        self.timing = SoftwareTime(segment_id, 'same-stream fixed resampler fixture: '+str(evidence))
        self.failure = None
        self.clock = clock
        self.diagnostic = diagnostic
        self.allow_relative_on_clock_drift = allow_relative_on_clock_drift
        super().__init__(producer_command(ffmpeg), 2, chunk_frames, log, input_pipe=True)
        self.capture_thread = threading.Thread(target=self._capture, args=(pcm_factory,), daemon=True)
        try:
            self.capture_thread.start()
        except BaseException:
            super().close()
            raise

    def _capture(self, factory):
        pcm = None
        frames = 0
        previous = None
        try:
            pcm = factory()
            self.diagnostic.write(json.dumps(dict(kind='configuration', **pcm.configuration))+'\n')
            pcm.start()
            while not self.closed.is_set():
                count = pcm.read()
                if not count:
                    self.clock.sleep(.005)
                    continue
                frames += count
                before = self.clock.monotonic_ns()
                wall = self.clock.time_ns()
                paired = self.clock.monotonic_ns()
                snapshot = pcm.status()
                after = self.clock.monotonic_ns()
                self.timing.observe(frames, snapshot, before, wall, paired, after)
                step_ok = continuous_status_step(previous, frames, snapshot, self.timing.tolerance_ns)
                if self.allow_relative_on_clock_drift and previous is not None and not step_ok:
                    self.timing.invalidate('sample_clock_step_discontinuity')
                self.diagnostic.write(json.dumps(dict(kind='status', original_sample=frames,
                    snapshot=snapshot, before_ns=before, realtime_ns=wall,
                    paired_ns=paired, after_ns=after,
                    invalid_reason=self.timing.invalid_reason,
                    local_status_step_consistent=step_ok if previous is not None else None,
                    relative_capture_after_time_gate=bool(self.allow_relative_on_clock_drift
                        and self.timing.invalid_reason == 'sample_clock_discontinuity' and step_ok),
                    formal_utc_upload=False))+'\n')
                self.diagnostic.flush()
                if self.timing.invalid_reason and not (self.allow_relative_on_clock_drift
                        and self.timing.invalid_reason == 'sample_clock_discontinuity' and step_ok):
                    raise SourceError(self.timing.invalid_reason)
                previous = (frames+snapshot['delay'], snapshot['htstamp_ns'])
                self.process.stdin.write(pcm.buffer.raw[:count*4])
                self.process.stdin.flush()
        except BaseException as failure:
            if not self.closed.is_set():
                self.failure = type(failure).__name__+': '+str(failure)
                self.timing.invalidate('source_failure: '+self.failure)
        finally:
            if pcm is not None:
                try:
                    pcm.close()
                except BaseException as failure:
                    self.failure = 'PCM cleanup: '+str(failure)
                    self.timing.invalidate(self.failure)
            try:
                self.process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    def read(self, size, stop):
        if self.failure:
            raise SourceError(self.failure)
        raw = super().read(size, stop)
        if self.failure:
            raise SourceError(self.failure)
        if raw == b'' and not self.closed.is_set():
            self.timing.invalidate('unexpected_capture_eof')
            raise SourceError('Continuous capture ended unexpectedly')
        return raw

    def close(self):
        try:
            super().close()
        finally:
            self.capture_thread.join(timeout=3)
        if self.capture_thread.is_alive():
            raise SourceError('Raw capture thread did not stop')
        if self.failure:
            raise SourceError(self.failure)

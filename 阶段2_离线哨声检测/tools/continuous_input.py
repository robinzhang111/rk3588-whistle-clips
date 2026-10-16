"""Bounded adapted PCM source and segment lifecycle; no device or UTC defaults."""
import queue
import subprocess
import threading
import time

import numpy as np
from stream_whistle import ChunkDetector


class SourceError(RuntimeError):
    pass


class ProcessSource:
    """One persistent producer of 22050Hz interleaved float32 little-endian PCM."""
    def __init__(self, command, channels, chunk_frames=512, log=None, input_pipe=False):
        if channels < 1 or not 1 <= chunk_frames <= 4096:
            raise ValueError('Invalid channels or chunk size')
        self.read_bytes = channels * chunk_frames * 4
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                        stderr=log if log is not None else subprocess.DEVNULL,
                                        stdin=subprocess.PIPE if input_pipe else None)
        self.messages = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while not self.closed.is_set():
                raw = self.process.stdout.read1(self.read_bytes)
                item = raw if raw else self.process.wait()
                while not self.closed.is_set():
                    try:
                        self.messages.put(item, timeout=.05)
                        break
                    except queue.Full:
                        pass
                if not raw:
                    return
        except Exception as error:
            while not self.closed.is_set():
                try:
                    self.messages.put(error, timeout=.05)
                    break
                except queue.Full:
                    pass

    def read(self, size, stop):
        if size != self.read_bytes:
            raise ValueError('Read size differs from bounded source')
        while not stop():
            try:
                item = self.messages.get(timeout=.05)
            except queue.Empty:
                continue
            if isinstance(item, bytes):
                return item
            if item == 0:
                return b''
            raise SourceError('PCM producer failed: %s' % item)
        return None

    def close(self):
        self.closed.set()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.thread.join(timeout=2)
        self.process.stdout.close()
        if self.thread.is_alive():
            raise SourceError('PCM reader did not exit')


def run_segment(source, predictor, emit, channels, segment_id, chunk_frames=512,
                stop=lambda: False, observe=None, input_gain=1):
    """Own source/predictor for one continuous segment; restart by calling anew."""
    if channels < 1 or not 1 <= chunk_frames <= 4096:
        raise ValueError('Invalid channels or chunk size')
    if isinstance(input_gain, bool) or input_gain not in (1, 16):
        raise ValueError('Input gain must be 1 or 16')
    reason = 'source_end'
    error = None
    pending = b''
    started = time.perf_counter()
    processing_seconds = 0.
    peak_before_gain = peak_after_gain = 0.
    over_full_scale_samples = 0
    def candidate(event):
        event = dict(event, segment_id=segment_id, event_type='whistle_candidate',
                     review_status='pending', absolute_utc_start=None)
        if event['closed_by'] == 'end_of_file':
            event['closed_by'] = reason
        emit(event)
    detector = ChunkDetector(channels, predictor, candidate, observe)
    frame_bytes = channels * 4
    try:
        while True:
            if stop():
                reason = 'normal_stop'
                break
            raw = source.read(chunk_frames * frame_bytes, stop)
            if raw is None:
                reason = 'normal_stop'
                break
            if not isinstance(raw, bytes) or len(raw) > chunk_frames * frame_bytes:
                raise SourceError('Invalid or oversized source read')
            if not raw:
                if pending:
                    reason = 'incomplete_frame'
                    error = 'Source ended within a PCM frame'
                break
            raw = pending + raw
            complete = len(raw) // frame_bytes * frame_bytes
            pending = raw[complete:]
            if complete:
                processing_started = time.perf_counter()
                audio = np.frombuffer(raw[:complete], dtype='<f4').reshape(-1, channels)
                if not np.isfinite(audio).all():
                    raise SourceError('Nonfinite audio')
                peak_before_gain = max(peak_before_gain, float(np.max(np.abs(audio))))
                if input_gain != 1:
                    audio = audio * np.float32(input_gain)
                peak_after_gain = max(peak_after_gain, float(np.max(np.abs(audio))))
                over_full_scale_samples += int(np.count_nonzero(np.abs(audio) > 1))
                # Float PCM stays linear; do not silently clip or normalize loud input.
                detector.feed(audio)
                processing_seconds += time.perf_counter() - processing_started
    except KeyboardInterrupt:
        reason = 'interrupted'
    except Exception as failure:
        reason, error = 'source_or_processing_error', str(failure)
    finally:
        try:
            report = detector.finish()
        finally:
            try:
                source.close()
            finally:
                predictor.close()
    return dict(report, segment_id=segment_id, close_reason=reason, error=error,
                status='failed' if error else 'closed', partial_frame_bytes=len(pending),
                natural_offset_verified=False, elapsed_seconds=time.perf_counter()-started,
                processing_seconds=processing_seconds,
                input_gain=input_gain, peak_before_gain=peak_before_gain,
                peak_after_gain=peak_after_gain,
                gain_over_full_scale_samples=over_full_scale_samples, gain_clipped=False,
                time_basis='relative samples within segment; no verified UTC',
                raw_audio_saved=False, feature_arrays_saved=False, formal_utc_upload=False)

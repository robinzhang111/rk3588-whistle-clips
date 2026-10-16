"""Bounded 22050Hz chunk detector; reuses fixed FFT and v1 event semantics."""
import numpy as np
import continuous_whistle as c


class ChunkDetector:
    def __init__(self, channels, predict, emit, observe=None, max_chunk_frames=4096):
        self.channels, self.predict, self.emit, self.observe = channels, predict, emit, observe
        self.max_chunk_frames = max_chunk_frames
        self.buffer = np.empty((c.WINDOW, channels), dtype=np.float32)
        self.fill = self.samples = self.windows = self.event_count = 0
        self.max_buffer_frames = self.max_input_frames = 0
        self.first = None
        self.peak = -1.
        self.peak_channel = 0
        self.active_channels = np.zeros(channels, dtype=bool)
        self.finished = False

    def feed(self, chunk):
        if self.finished:
            raise ValueError('Already finished')
        chunk = np.asarray(chunk, dtype=np.float32)
        if chunk.ndim != 2 or chunk.shape[1] != self.channels or len(chunk)>self.max_chunk_frames:
            raise ValueError('Invalid channel shape or oversized chunk')
        if not np.isfinite(chunk).all():
            raise ValueError('Nonfinite audio')
        self.max_input_frames = max(self.max_input_frames,len(chunk))
        self.samples += len(chunk)
        cursor = 0
        while cursor < len(chunk):
            take = min(c.WINDOW-self.fill,len(chunk)-cursor)
            self.buffer[self.fill:self.fill+take] = chunk[cursor:cursor+take]
            self.fill += take
            cursor += take
            self.max_buffer_frames = max(self.max_buffer_frames,self.fill)
            if self.fill == c.WINDOW:
                features, silent, _ = c.features_for_starts(self.buffer,np.array([0]))
                scores = np.zeros(self.channels,dtype=np.float32)
                for channel in range(self.channels):
                    if not silent[0,channel]:
                        scores[channel] = self.predict(features[0,channel])
                if not np.isfinite(scores).all() or not ((scores>=0)&(scores<=1)).all():
                    raise ValueError('Invalid model scores')
                if self.observe is not None:
                    self.observe(self.windows,features[0],scores,silent[0])
                positive = scores.astype(np.float64)>=c.THRESHOLD
                if positive.any():
                    if self.first is None:
                        self.first = self.windows
                        self.peak = -1.
                        self.active_channels.fill(False)
                    self.active_channels |= positive
                    channel = int(np.argmax(scores))
                    if float(scores[channel])>self.peak:
                        self.peak, self.peak_channel = float(scores[channel]),channel
                elif self.first is not None:
                    self._close(self.windows-1,self.windows)
                self.windows += 1
                self.buffer[:c.WINDOW-c.HOP] = self.buffer[c.HOP:]
                self.fill = c.WINDOW-c.HOP

    def _close(self,last,closing):
        first = self.first
        if last-first+1 >= 2:
            end = last*c.HOP+c.WINDOW
            event = dict(event_index=self.event_count,first_window=first,last_window=last,
                estimated_start_sample=first*c.HOP,estimated_end_sample=end,
                estimated_start_seconds=first*c.HOP/22050,estimated_end_seconds=end/22050,
                decision_sample=(first+1)*c.HOP+c.WINDOW,
                closure_available_sample=closing*c.HOP+c.WINDOW if closing is not None else self.samples,
                closed_by='negative_window' if closing is not None else 'end_of_file',
                reaches_last_complete_window=closing is None,onset_clipped_at_file_start=first==0,
                natural_offset_verified=False,peak_score=self.peak,audition_channel=self.peak_channel,
                active_channels=np.flatnonzero(self.active_channels).tolist())
            self.emit(event)
            self.event_count += 1
        self.first = None
        self.active_channels.fill(False)

    def finish(self):
        if self.finished:
            raise ValueError('Already finished')
        if self.first is not None:
            self._close(self.windows-1,None)
        coverage = (self.windows-1)*c.HOP+c.WINDOW if self.windows else 0
        retained_before_finish = self.fill
        self.buffer = None
        self.fill = 0
        self.finished = True
        return dict(samples=self.samples,windows=self.windows,candidates=self.event_count,
            uncovered_tail_samples=self.samples-coverage,retained_before_finish=retained_before_finish,
            retained_after_finish=0,max_buffer_frames=self.max_buffer_frames,
            max_input_frames=self.max_input_frames,audio_buffer_capacity_bytes=c.WINDOW*self.channels*4,
            max_chunk_frames=self.max_chunk_frames,rule='v1',threshold=c.THRESHOLD,sample_rate=22050)

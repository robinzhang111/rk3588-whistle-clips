"""Float32 TestBench decoding, fixed preprocessing, and candidate event boundaries."""
import struct

import numpy as np

WINDOW = 1024
HOP = 512
THRESHOLD = .65


def read_float_wav(path):
    with open(path, "rb") as stream:
        header = stream.read(12)
        if header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ValueError("Expected RIFF/WAVE")
        fmt, payload = None, None
        while True:
            chunk = stream.read(8)
            if len(chunk) != 8:
                break
            name, size = struct.unpack("<4sI", chunk)
            if name == b"fmt ":
                fmt = stream.read(size)
            elif name == b"data":
                payload = (stream.tell(), size)
                stream.seek(size, 1)
            else:
                stream.seek(size, 1)
            if size % 2:
                stream.seek(1, 1)
        tag, channels, rate, byte_rate, align, bits = struct.unpack("<HHIIHH", fmt[:16])
        subtype = struct.unpack("<I", fmt[24:28])[0] if tag == 65534 else tag
        if subtype != 3 or bits != 32 or align != channels*4 or byte_rate != rate*align:
            raise ValueError("Only float32 PCM TestBench WAV accepted")
        stream.seek(payload[0])
        samples = np.fromfile(stream, dtype="<f4", count=payload[1]//4)
    if samples.size*4 != payload[1] or not np.isfinite(samples).all():
        raise ValueError("Truncated or nonfinite audio")
    return samples.reshape(-1, channels), rate


def starts_for_samples(count):
    return np.arange(0, max(0, count-WINDOW+1), HOP, dtype=np.int64)


def features_for_starts(audio, starts):
    # Shape (windows, channels, samples); matches upstream's per-channel FFT.
    frames = np.stack([audio[int(start):int(start)+WINDOW].T for start in starts])
    silent = np.all(frames == 0, axis=2)
    magnitude = np.abs(np.fft.fft(frames*np.hamming(WINDOW), axis=2)[:, :, :513])
    zero_bins = magnitude == 0
    magnitude[zero_bins] = 1e-12
    features = (20*np.log10(magnitude)).astype(np.float32)
    return features[..., None], silent, int(zero_bins.sum())


def candidate_events(scores, starts, sample_count, rate):
    """OR across channels, consecutive positive windows >=2; no gap bridging."""
    positive = np.max(scores.astype(np.float64), axis=1) >= THRESHOLD
    events = []
    first = None
    def emit(last, closing_index):
        if last-first+1 < 2:
            return
        peak_window, peak_channel = np.unravel_index(np.argmax(scores[first:last+1]), scores[first:last+1].shape)
        end = min(sample_count, int(starts[last])+WINDOW)
        events.append(dict(event_index=len(events), first_window=first, last_window=last,
            estimated_start_sample=int(starts[first]), estimated_end_sample=end,
            estimated_start_seconds=int(starts[first])/rate, estimated_end_seconds=end/rate,
            decision_sample=int(starts[first+1])+WINDOW,
            closure_available_sample=int(starts[closing_index])+WINDOW if closing_index is not None else sample_count,
            closed_by="negative_window" if closing_index is not None else "end_of_file",
            reaches_last_complete_window=last==len(starts)-1,
            onset_clipped_at_file_start=first==0, natural_offset_verified=False,
            peak_score=float(scores[first+peak_window,peak_channel]), audition_channel=int(peak_channel),
            active_channels=np.flatnonzero(np.max(scores[first:last+1].astype(np.float64),axis=0)>=THRESHOLD).tolist()))
    for index, active in enumerate(positive):
        if active and first is None:
            first = index
        elif not active and first is not None:
            emit(index-1,index)
            first = None
    if first is not None:
        emit(len(starts)-1,None)
    return events

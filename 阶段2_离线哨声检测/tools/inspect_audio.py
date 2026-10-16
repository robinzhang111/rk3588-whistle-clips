"""Inspect PCM WAV files without modifying audio or detecting events."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import wave

import numpy as np


def inspect(path):
    with wave.open(str(path), "rb") as audio:
        channels = audio.getnchannels()
        width = audio.getsampwidth()
        rate = audio.getframerate()
        frames = audio.getnframes()
        raw = audio.readframes(frames)
    if len(raw) != frames * channels * width:
        raise ValueError("WAV data length does not match header")
    if width == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.int32)
    elif width == 3:
        octets = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        samples = octets[:, 0] | (octets[:, 1] << 8) | (octets[:, 2] << 16)
        samples = (samples ^ (1 << 23)) - (1 << 23)
    else:
        raise ValueError("This inspection supports only 16-bit and 24-bit PCM")
    samples = samples.reshape(-1, channels)
    scale = 2 ** (width * 8 - 1)
    normalized = samples.astype(np.float64) / scale
    stats = []
    for index in range(channels):
        values = samples[:, index]
        signal = normalized[:, index]
        # Spectrum is descriptive, not an event label or selected detector band.
        spectrum = np.abs(np.fft.rfft((signal - signal.mean()) * np.hanning(frames))) ** 2
        frequencies = np.fft.rfftfreq(frames, 1 / rate)
        peak = int(np.argmax(spectrum[1:])) + 1
        stats.append({
            "channel": index + 1,
            "peak_absolute_fraction": float(np.abs(signal).max()),
            "rms_fraction": float(np.sqrt(np.mean(signal ** 2))),
            "dc_mean_fraction": float(signal.mean()),
            "all_zero": bool(np.all(values == 0)),
            "full_scale_samples": int(np.count_nonzero((values == -scale) | (values == scale - 1))),
            "dominant_fft_bin_hz": float(frequencies[peak]),
        })
    return {
        "path": str(path.absolute()), "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "sample_rate_hz": rate, "channels": channels,
        "bits_per_sample": width * 8, "sample_frames": frames,
        "duration_seconds": frames / rate, "pcm_bytes_read": len(raw),
        "channel_statistics": stats,
        "annotation_status": "not_provided", "listening_status": "not_confirmed",
        "absolute_utc_start": None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps({
        "python": platform.python_version(), "numpy": np.__version__,
        "files": [inspect(path) for path in args.files],
        "limitations": [
            "No event detection or manual listening performed",
            "No full-scale samples does not prove absence of earlier clipping or distortion",
            "Dominant whole-file FFT bin is not a detector threshold or event onset",
        ],
    }, ensure_ascii=False, indent=2))

"""Create source-preserving audio/figure spot checks; no whistle classifier is run."""
import argparse
import hashlib
import json
from pathlib import Path
import wave

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def spotcheck(root, derived, output):
    if output.exists():
        raise FileExistsError("Use a new spotcheck directory")
    output.mkdir(parents=True)
    manifest = json.loads((derived / "manifest.json").read_text(encoding="utf-8"))
    labels = json.loads((derived / "annotations_seconds.json").read_text(encoding="utf-8"))
    checks = [(item["filename"], 0, "first_label") for item in manifest]
    checks += [("LUnitedvsUPenn20170728Half2.wav", 3, "bad_right_end_left_candidate_only"),
               ("WhistleTest_tuhhnao16.wav", 12, "end_at_eof")]
    results = []
    for number, (filename, index, reason) in enumerate(checks, 1):
        info = next(item for item in manifest if item["filename"] == filename)
        left = next(row for row in labels if row["filename"] == filename
                    and row["channel_index"] == 0 and row["label_index"] == index)
        right = next(row for row in labels if row["filename"] == filename
                     and row["channel_index"] == 1 and row["label_index"] == index)
        rate, frames = info["sample_rate_hz"], info["sample_frames"]
        start, end = left["original_start"], left["original_end"]
        clip_start, clip_end = max(0, start - rate), min(frames, end + rate)
        with wave.open(str(root / filename), "rb") as audio:
            audio.setpos(clip_start)
            raw = audio.readframes(clip_end - clip_start)
        assert len(raw) == (clip_end - clip_start) * 2
        signal = (np.frombuffer(raw, dtype=np.uint8).reshape(-1, 2).astype(np.float64) - 128) / 128
        name = "%02d_%s_label%d" % (number, Path(filename).stem, index)
        with wave.open(str(output / (name + ".wav")), "wb") as clip:
            clip.setparams((2, 1, rate, 0, "NONE", "not compressed"))
            clip.writeframes(raw)
        fig, axes = plt.subplots(2, 2, figsize=(12, 5.5), constrained_layout=True)
        stats = []
        for channel in range(2):
            values = signal[:, channel]
            times = (np.arange(len(values)) + clip_start) / rate
            stride = max(1, len(values) // 8000)
            axes[channel, 0].plot(times[::stride], values[::stride], linewidth=.4)
            axes[channel, 0].set_ylim(-1.05, 1.05)
            axes[channel, 0].set_ylabel("ch%d amplitude (PCM/128)" % channel)
            _, _, _, image = axes[channel, 1].specgram(
                values, NFFT=1024, Fs=rate, noverlap=512,
                xextent=(clip_start / rate, clip_end / rate), vmin=-90, vmax=-20)
            axes[channel, 1].set_ylim(0, 10000)
            axes[channel, 1].set_ylabel("frequency (Hz)")
            for ax in axes[channel]:
                ax.axvspan(start / rate, end / rate, color="orange", alpha=.18)
                ax.axvline(start / rate, color="red", linewidth=.8)
                ax.axvline(end / rate, color="red", linewidth=.8)
                ax.set_xlabel("original file time (s)")
            axes[channel, 0].set_title("original label" if channel == 0 or right["valid"] else
                                       "left boundary candidate only; invalid right end NOT adopted")
            selected = values[start - clip_start:end - clip_start]
            context = np.concatenate((values[:start - clip_start], values[end - clip_start:]))
            raw_channel = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 2)[:, channel]
            stats.append({"channel_index": channel,
                          "interval_used_for_inspection": "left_original_bounds" if channel == 0 else
                                                          "right_original_bounds" if right["valid"] else
                                                          "left_candidate_bounds_not_adopted",
                          "interval_rms": float(np.sqrt(np.mean(selected ** 2))),
                          "context_rms": float(np.sqrt(np.mean(context ** 2))) if len(context) else None,
                          "full_scale_clip_samples": int(np.count_nonzero((raw_channel == 0) | (raw_channel == 255))),
                          "listening_confirmed": False})
        fig.suptitle(filename + " | label %d | " % index + reason)
        fig.savefig(str(output / (name + ".png")), dpi=120)
        plt.close(fig)
        results.append({"filename": filename, "label_index": index, "reason": reason,
                        "source_sha256": info["sha256"], "clip_start_sample": clip_start,
                        "clip_end_sample": clip_end, "clip_start_seconds": clip_start / rate,
                        "clip_end_seconds": clip_end / rate, "label_start_seconds": start / rate,
                        "label_end_seconds": end / rate, "wav": name + ".wav", "figure": name + ".png",
                        "clip_sha256": hashlib.sha256((output / (name + ".wav")).read_bytes()).hexdigest(),
                        "channel_statistics": stats,
                        "conclusion": "objective inspection only; semantic listening not confirmed"})
    # Check exact sample containment for the suspected same-match excerpts.
    with wave.open(str(root / "HTWK_Dutch_05_04_2017.wav"), "rb") as audio:
        long_pcm = audio.readframes(audio.getnframes())
    with wave.open(str(root / "HTWK_Dutch_05_04_2017-2.wav"), "rb") as audio:
        short_pcm = audio.readframes(audio.getnframes())
    offset = long_pcm.find(short_pcm)
    containment = {"long": "HTWK_Dutch_05_04_2017.wav", "short": "HTWK_Dutch_05_04_2017-2.wav",
                   "exact_pcm_subsequence_byte_offset": offset,
                   "interpretation": "exact containment found" if offset >= 0 else
                   "no exact full PCM containment; this does not establish independent sessions"}
    (output / "spotcheck.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (output / "containment_check.json").write_text(json.dumps(containment, indent=2), encoding="utf-8")
    print(json.dumps({"spotcheck_pairs": len(results), "channel_windows": len(results) * 2,
                      "listening_confirmed": False, "containment": containment}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--derived", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spotcheck(args.root, args.derived, args.output)

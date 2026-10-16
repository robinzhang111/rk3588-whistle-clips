"""Run fixed dev/validation selection followed by one held-out test evaluation."""
import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np

STAGE = Path(__file__).absolute().parent.parent
sys.path.insert(0, str(STAGE))
import traditional_baseline as baseline


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, content):
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def now():
    return datetime.now(timezone.utc).isoformat()


def aggregate(results, duration):
    tp, fp, fn, duplicate = [sum(item[field] for item in results)
                              for field in ("TP", "FP", "FN", "duplicate_FP")]
    matches = [match for item in results for match in item["matches"]]
    def stats(field):
        values = [m[field] for m in matches if m[field] is not None]
        return dict(count=len(values), mean=float(np.mean(values)) if values else None,
                    median=float(np.median(values)) if values else None,
                    p95=float(np.percentile(values, 95)) if values else None,
                    maximum=max(values) if values else None)
    return dict(TP=tp, FP=fp, FN=fn, precision=tp/(tp+fp) if tp+fp else None,
                recall=tp/(tp+fn) if tp+fn else None, F1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
                undefined_reasons={field: reason for field, reason, denominator in
                    [("precision", "no predictions", tp+fp), ("recall", "no targets", tp+fn),
                     ("F1", "no predictions or targets", 2*tp+fp+fn)] if not denominator},
                duration_seconds=duration, FP_per_hour=fp/(duration/3600), duplicate_FP=duplicate,
                signed_onset_error_seconds=stats("signed_onset_error_seconds"),
                absolute_onset_error_seconds=stats("absolute_onset_error_seconds"),
                decision_delay_seconds=stats("decision_delay_seconds"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="New output directory; existing directories are refused")
    args = parser.parse_args()
    output = Path(args.output).absolute()
    prepared = STAGE / "data/bhuman2017-prepared-v1"
    protocol_path, split_path = STAGE / "evaluation/protocol-v2.json", STAGE / "evaluation/splits-v1.json"
    protocol, splits = read(protocol_path), read(split_path)["splits"]
    for name, expected in protocol["basis_sha256"].items():
        if sha(prepared/name) != expected:
            raise ValueError("Prepared data hash changed: "+name)
    if sha(split_path) != protocol["split_snapshot_sha256"]:
        raise ValueError("Frozen split hash changed")
    manifest = {row["filename"]: row for row in read(prepared/"manifest.json")}
    all_labels = read(prepared/"annotations_seconds.json")
    labels = {name: [row for row in all_labels if row["filename"] == name and row["primary_evaluation"]]
              for name in manifest}
    source = STAGE.parent / "data/whistle"
    original_hashes = read(prepared/"integrity.json")["before"]
    before = {name: sha(source/name) for name in original_hashes}
    if before != original_hashes:
        raise ValueError("Original source hash changed")
    output.mkdir(parents=True, exist_ok=False)
    candidates = [dict(rms_threshold=rms, band_ratio_threshold=ratio,
                       min_positive_seconds=duration, merge_gap_seconds=gap)
                  for rms, ratio, duration, gap in itertools.product([.03,.08,.15], [.3,.5,.7], [.06,.1], [.15,.35])]
    design = dict(created_utc=now(), python=platform.python_version(), numpy=np.__version__,
                  protocol_sha256=sha(protocol_path), split_sha256=sha(split_path),
                  source_sha256=before, source_files_read_for_hash_only_before_test=True,
                  permission="User confirms all requested uses permitted; user declines evidence archival. Not independently verified official license.",
                  band_rule="Development labels only: mean Hann power peak within 500-8000 Hz; min/max of 31 peaks padded by 200 Hz, rounded outward to 100 Hz",
                  frame_seconds=.02, hop_seconds=.01, broadband_hz=[300,10000],
                  candidates=candidates, candidate_count=len(candidates),
                  selection="Validation F1 descending, FP ascending, mean matched absolute onset error ascending, candidate index ascending",
                  decision_semantics="Sample where minimum positive hops is reached; offline proxy, not measured online latency. Event end finalization requires waiting through merge gap.",
                  test_rule="No test features used for selection; one primary evaluation after selected config written. Secondary .1/.5 use unchanged predictions.")
    write(output/"design-before-selection.json", design)
    band_peaks = []
    for name in splits["development"]["files"]:
        signal, rate = baseline.read_channel0(source/name)
        frequencies = np.fft.rfftfreq(round(.02*rate), 1/rate)
        allowed = (frequencies >= 500) & (frequencies <= 8000)
        for label in labels[name]:
            total = sum((power.sum(axis=0) for _, _, power in baseline.frame_batches(
                signal[label["original_start"]:label["original_end"]], rate)))
            peak = float(frequencies[allowed][np.argmax(total[allowed])])
            band_peaks.append(dict(filename=name, label_index=label["label_index"], peak_hz=peak))
    band = [int(np.floor((min(p["peak_hz"] for p in band_peaks)-200)/100)*100),
            int(np.ceil((max(p["peak_hz"] for p in band_peaks)+200)/100)*100)]
    write(output/"development-band.json", dict(band_hz=band, peaks=band_peaks))
    print("Development-derived band:", band, flush=True)
    timings, cache = {}, {}
    def features(name):
        start = time.perf_counter()
        signal, rate = baseline.read_channel0(source/name)
        item = baseline.extract_features(signal, rate, band)
        timings[name] = dict(read_and_feature_seconds=time.perf_counter()-start,
                             duration_seconds=len(signal)/rate)
        return item
    for split in ("development", "validation"):
        for name in splits[split]["files"]:
            cache[name] = features(name)
            print("Features:", split, name, flush=True)
    def evaluate(names, config, feature_cache, tolerance=.2):
        records = []
        for name in names:
            start = time.perf_counter()
            predictions = baseline.detect_events(feature_cache[name], config, manifest[name]["sample_frames"])
            detect_seconds = time.perf_counter()-start
            for prediction in predictions:
                prediction.update(filename=name, channel_index=0,
                                  estimated_start_seconds=prediction["estimated_start_sample"]/manifest[name]["sample_rate_hz"])
            scores = baseline.score_events(predictions, labels[name], manifest[name]["sample_rate_hz"],
                                           manifest[name]["sample_frames"], tolerance)
            records.append(dict(filename=name, predictions=predictions, score=scores, detect_seconds=detect_seconds))
        return records, aggregate([item["score"] for item in records],
                                  sum(manifest[name]["duration_seconds"] for name in names))
    grid = []
    for index, config in enumerate(candidates):
        _, dev = evaluate(splits["development"]["files"], config, cache)
        _, val = evaluate(splits["validation"]["files"], config, cache)
        grid.append(dict(candidate_index=index, config=config, development=dev, validation=val))
    write(output/"validation-grid.json", grid)
    selected = min(grid, key=lambda row: (-(row["validation"]["F1"] or 0), row["validation"]["FP"],
        row["validation"]["absolute_onset_error_seconds"]["mean"]
        if row["validation"]["absolute_onset_error_seconds"]["mean"] is not None else float("inf"), row["candidate_index"]))
    frozen_path = output/"frozen-config-before-test.json"
    write(frozen_path, dict(frozen_utc=now(), band_hz=band, selected=selected,
                            baseline_sha256=sha(STAGE/"traditional_baseline.py"),
                            runner_sha256=sha(Path(__file__)), test_features_started=False))
    frozen_hash = sha(frozen_path)
    print("Frozen candidate:", selected["candidate_index"], selected["config"], flush=True)
    write(output/"test-start.json", dict(utc=now(), frozen_config_sha256=frozen_hash))
    for name in splits["test"]["files"]:
        cache[name] = features(name)
        print("Features: test", name, flush=True)
    report = dict(frozen_config_sha256=frozen_hash, splits={}, secondary_test_tolerances={},
                  processing=timings, no_board_run=True, no_ai_training=True, absolute_utc_start=None)
    for split, group in splits.items():
        records, metrics = evaluate(group["files"], selected["config"], cache)
        write(output/(split+"-predictions-and-scores.json"), records)
        metrics["read_feature_and_detect_seconds"] = sum(timings[item["filename"]]["read_and_feature_seconds"]+
                                                       item["detect_seconds"] for item in records)
        metrics["audio_seconds_per_processing_second"] = metrics["duration_seconds"]/metrics["read_feature_and_detect_seconds"]
        report["splits"][split] = metrics
    for tolerance in (.1, .5):
        _, metrics = evaluate(splits["test"]["files"], selected["config"], cache, tolerance)
        report["secondary_test_tolerances"][str(tolerance)] = metrics
    after = {name: sha(source/name) for name in original_hashes}
    if after != before or sha(frozen_path) != frozen_hash:
        raise ValueError("Integrity changed during evaluation")
    write(output/"integrity.json", dict(before=before, after=after, unchanged=True,
                                        frozen_config_unchanged=True))
    report["completed_utc"] = now()
    write(output/"summary.json", report)
    print(json.dumps(report["splits"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

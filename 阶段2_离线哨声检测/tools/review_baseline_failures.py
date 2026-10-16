"""Post-test descriptive audit only; does not select or modify detector parameters."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

STAGE = Path(__file__).absolute().parent.parent
sys.path.insert(0, str(STAGE))
import traditional_baseline as baseline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory")
    args = parser.parse_args()
    directory = Path(args.run_directory)
    report_path = directory/"failure-review.json"
    if report_path.exists():
        raise FileExistsError(report_path)
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    frozen = read(directory/"frozen-config-before-test.json")
    config = frozen["selected"]["config"]
    rows = read(STAGE/"data/bhuman2017-prepared-v1/annotations_seconds.json")
    audit = []
    for split in ("development", "validation", "test"):
        for record in read(directory/(split+"-predictions-and-scores.json")):
            name = record["filename"]
            signal, rate = baseline.read_channel0(STAGE.parent/"data/whistle"/name)
            features = baseline.extract_features(signal, rate, frozen["band_hz"])
            labels = [row for row in rows if row["filename"] == name and row["primary_evaluation"]]
            for index in record["score"]["missed_label_indices"]:
                label = labels[index]
                starts = features["starts"]
                inside = (starts >= label["original_start"]) & (starts < label["original_end"])
                rms, ratio = features["rms"][inside], features["ratio"][inside]
                amplitude_pass = rms >= config["rms_threshold"]
                band_pass = ratio >= config["band_ratio_threshold"]
                overlaps = [p for p in record["predictions"] if p["estimated_start_sample"] < label["original_end"]
                            and p["estimated_end_sample"] > label["original_start"]]
                nearest = min(record["predictions"], key=lambda p: abs(p["estimated_start_sample"]-label["original_start"])) if record["predictions"] else None
                audit.append(dict(split=split, filename=name, label_index=label["label_index"],
                    start_seconds=label["start_seconds"], end_seconds=label["end_seconds"],
                    max_frame_rms=float(rms.max()), max_band_ratio=float(ratio.max()),
                    frame_count=len(rms), amplitude_pass_frames=int(amplitude_pass.sum()),
                    band_pass_frames=int(band_pass.sum()), joint_pass_frames=int((amplitude_pass & band_pass).sum()),
                    overlapping_prediction_count=len(overlaps),
                    nearest_prediction_onset_error_seconds=(nearest["estimated_start_sample"]-label["original_start"])/rate if nearest else None,
                    diagnosis="all target-window frames below amplitude threshold" if not amplitude_pass.any() else
                              "target-window frames fail band ratio" if not (amplitude_pass & band_pass).any() else
                              "qualifying frames present, but no onset match; inspect duration/group merging and label boundary"))
    result = dict(scope="Post-test descriptive evidence only; no retuning or replacement evaluation", missed_groups=audit)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    for split in ("development", "validation", "test"):
        subset = [row for row in audit if row["split"] == split]
        print(split, len(subset), "missed; amplitude-zero", sum(row["amplitude_pass_frames"] == 0 for row in subset))


if __name__ == "__main__":
    main()

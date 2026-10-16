"""Derive auditable B-Human 2017 metadata; never rewrite source audio or labels."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import wave
import zipfile


SOURCE_URL = "https://b-human.informatik.uni-bremen.de/public/datasets/whistle-2017/"
GROUPS = {
    "WhistleTest_tuhhnao12.wav": ("german_open_2017_tests", "development"),
    "WhistleTest_tuhhnao16.wav": ("german_open_2017_tests", "development"),
    "TestBHuman_03_05_2017_1413.wav": ("german_open_2017_tests", "development"),
    "Lab_Fox40_vs_printed.wav": ("lab_fox40_printed", "development"),
    "HTWK_HULKs_1930_2016_04_29.wav": ("leipzig_20160429", "validation"),
    "HTWK_Dutch_05_04_2017.wav": ("iran_open_20170405", "test"),
    "HTWK_Dutch_05_04_2017-2.wav": ("iran_open_20170405", "test"),
    "SRC_Dutch_05-04-2017.wav": ("iran_open_20170405", "test"),
    "LUnitedvsUPenn20170728.wav": ("lunited_upenn_20170728", "test"),
    "LUnitedvsUPenn20170728Half2.wav": ("lunited_upenn_20170728", "test"),
}


def sha256_stream(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def source_hash(path):
    with path.open("rb") as stream:
        return sha256_stream(stream)


def annotation_row(filename, channel, index, raw, rate, frames, source_sha):
    start, end = raw["start"], raw["end"]
    valid = (type(start) is int and type(end) is int and 0 <= start < end <= frames)
    core_start, core_end = raw.get("coreStart"), raw.get("coreEnd")
    core_valid = (type(core_start) is int and type(core_end) is int
                  and valid and start <= core_start < core_end <= end)
    return {
        "filename": filename, "channel_index": channel,
        "channel_name": "left" if channel == 0 else "right",
        "label_index": index, "raw_label": raw,
        "source_json_pointer": "/audioFiles/{file_index}/channels/%d/whistleLabels/%d" % (channel, index),
        "source_json_sha256": source_sha,
        "position_unit": "sample_frames_per_channel", "interval_convention": "[start,end)",
        "sample_rate_hz": rate, "original_start": start, "original_end": end,
        "raw_start_seconds": start / rate, "raw_end_seconds": end / rate,
        "start_seconds": start / rate if valid else None,
        "end_seconds": end / rate if valid else None,
        "valid": valid, "status": "valid" if valid else "quarantined_out_of_bounds_or_order",
        "primary_evaluation": valid and channel == 0,
        "effective_start": start if valid else None,
        "effective_end": end if valid else None,
        "correction_applied": False,
        "core_start_seconds": core_start / rate if core_valid else None,
        "core_end_seconds": core_end / rate if core_valid else None,
        "core_status": "not_present" if core_start is None and core_end is None else
                       "in_bounds_semantics_unverified" if core_valid else "invalid_or_unset_semantics_unverified",
        "source_url": SOURCE_URL, "absolute_utc_start": None,
    }


def prepare(root, output, archive):
    if output.exists():
        raise FileExistsError("Use a new output directory; previous derived data is preserved")
    original_paths = sorted(root.glob("*.wav")) + [root / "whistledb.json"]
    before = {p.name: source_hash(p) for p in original_paths}
    db = json.loads((root / "whistledb.json").read_text(encoding="utf-8"))
    manifest, annotations, anomalies = [], [], []
    zip_checks = []
    with zipfile.ZipFile(archive) as package:
        for path in original_paths:
            with package.open("whistle/" + path.name) as stream:
                archived_hash = sha256_stream(stream)
            zip_checks.append({"filename": path.name, "archive_sha256": archived_hash,
                               "local_sha256": before[path.name], "equal": archived_hash == before[path.name]})
    assert all(row["equal"] for row in zip_checks), "Local originals differ from downloaded archive"
    for file_index, item in enumerate(db["audioFiles"]):
        filename = item["path"]
        assert Path(filename).name == filename, "Unexpected nested source path"
        path = root / filename
        with wave.open(str(path), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            channels, width = audio.getnchannels(), audio.getsampwidth()
            assert channels == len(item["channels"]) == 2 and width == 1
            count = 0
            while True:
                block = audio.readframes(48000)
                if not block:
                    break
                count += len(block)
            assert count == frames * channels * width
        group, split = GROUPS[filename]
        rows = []
        for channel, metadata in enumerate(item["channels"]):
            assert metadata["completelyLabeled"] is True
            previous_end = 0
            for index, raw in enumerate(metadata["whistleLabels"]):
                row = annotation_row(filename, channel, index, raw, rate, frames, before["whistledb.json"])
                row["source_json_pointer"] = row["source_json_pointer"].format(file_index=file_index)
                row.update(group_id=group, split_proposal=split)
                if row["valid"]:
                    assert row["original_start"] >= previous_end, "Overlapping or unsorted valid labels"
                    previous_end = row["original_end"]
                else:
                    counterpart = item["channels"][0]["whistleLabels"][index]
                    anomalies.append({"filename": filename, "channel_index": channel,
                                      "label_index": index, "original_label": raw,
                                      "sample_frames": frames, "left_counterpart": counterpart,
                                      "candidate_only_end": counterpart["end"],
                                      "action": "keep original, quarantine invalid row; right channel excluded from primary evaluation",
                                      "candidate_adopted": False})
                rows.append(row)
        annotations.extend(rows)
        manifest.append({
            "filename": filename, "path": str(path.absolute()), "sha256": before[filename],
            "bytes": path.stat().st_size, "sample_rate_hz": rate, "sample_frames": frames,
            "channels": channels, "bits_per_sample": 8, "pcm_encoding": "unsigned_8bit",
            "float_decode": "(uint8_sample - 128) / 128", "duration_seconds": frames / rate,
            "source_url": SOURCE_URL, "source_json_sha256": before["whistledb.json"],
            "primary_channel_index": 0, "primary_labels": sum(r["primary_evaluation"] for r in rows),
            "valid_channel_labels": sum(r["valid"] for r in rows),
            "invalid_channel_labels": sum(not r["valid"] for r in rows),
            "completely_labeled_publisher_flags": [c["completelyLabeled"] for c in item["channels"]],
            "label_scope": "target_field_referee_whistles; neighbouring-field whistles generally not labelled",
            "license_status": "explicit_dataset_license_not_found", "absolute_utc_start": None,
            "group_id": group, "split_proposal": split, "split_status": "proposal_not_frozen",
        })
    assert len(manifest) == len(original_paths) - 1
    split_summary = {}
    for split in ("development", "validation", "test"):
        selected = [row for row in manifest if row["split_proposal"] == split]
        split_summary[split] = {"groups": sorted({row["group_id"] for row in selected}),
                                "files": [row["filename"] for row in selected],
                                "duration_seconds": sum(row["duration_seconds"] for row in selected),
                                "primary_events": sum(row["primary_labels"] for row in selected)}
    after = {p.name: source_hash(p) for p in original_paths}
    assert before == after, "Source originals changed during derivation"
    output.mkdir(parents=True)
    for name, value in (("manifest.json", manifest), ("annotations_seconds.json", annotations),
                        ("anomalies.json", anomalies), ("split_proposal.json", split_summary),
                        ("integrity.json", {"before": before, "after": after, "unchanged": before == after,
                                            "archive_comparison": zip_checks})):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["filename", "channel_index", "label_index", "original_start", "original_end",
              "raw_start_seconds", "raw_end_seconds", "start_seconds", "end_seconds",
              "valid", "primary_evaluation", "status", "group_id", "split_proposal"]
    with (output / "annotations_seconds.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(annotations)
    print(json.dumps({"files": len(manifest), "channel_labels": len(annotations),
                      "primary_events": sum(r["primary_evaluation"] for r in annotations),
                      "invalid_channel_labels": len(anomalies), "sources_unchanged": before == after,
                      "splits": split_summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.root, args.output, args.archive)

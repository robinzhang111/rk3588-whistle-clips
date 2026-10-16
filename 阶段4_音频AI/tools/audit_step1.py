"""Read-only data audit. Output must be new; no inference or media exports."""
import argparse
from collections import Counter, defaultdict
import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import wave

import numpy as np

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "阶段2_离线哨声检测/tools"


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bh = load_tool("prepare_bhuman2017")
sha = bh.source_hash


def save(output, name, value):
    (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                        allow_nan=False) + "\n", encoding="utf-8")


def run(command):
    result = subprocess.run([str(x) for x in command], capture_output=True)
    return result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")


def provenance(path):
    relative = path.relative_to(ROOT).as_posix()
    if relative.startswith("data/whistle/"):
        group = bh.GROUPS.get(path.name, ("bhuman_metadata",))[0]
        source = "https://b-human.informatik.uni-bremen.de/public/datasets/whistle-2017/"
    elif relative.startswith("data/Robcup/"):
        group = "naodevils_origin_unresolved"  # No row-to-recording map.
        source = "https://github.com/NaoDevils/AudioProcessing"
    elif relative.startswith("data/volleyball/"):
        group = "volleyball_" + path.stem if path.suffix.lower() == ".wav" else "volleyball_annotation_versions"
        source = "https://huggingface.co/datasets/GYdevy/volleyball-whistles"
    else:
        group = "basketball_source_unresolved_" + path.stem
        source = "local_user_media; original_url_unknown"
    return dict(path=relative, bytes=path.stat().st_size, sha256=sha(path),
                group_proposal=group, source_candidate=source,
                group_status="proposal_only; same_source_not_excluded",
                source_identity="local_record_or_matching_card; not_remote_byte_verification")


def npy_audit(path):
    # numpy.load refuses object arrays with allow_pickle=False, including mmap.
    array = np.load(path, mmap_mode="r", allow_pickle=False)
    result = dict(path=path.relative_to(ROOT).as_posix(), shape=list(array.shape),
                  dtype=str(array.dtype), offset=array.offset,
                  expected_bytes=array.offset + array.nbytes,
                  actual_bytes=path.stat().st_size, scanned_values=0,
                  nonfinite_values=0, minimum=None, maximum=None, label_counts={})
    counts = Counter()
    for start in range(0, len(array), 4096):
        block = array[start:start + 4096]
        finite = np.isfinite(block)
        result["scanned_values"] += int(block.size)
        result["nonfinite_values"] += int(block.size - np.count_nonzero(finite))
        values = block[finite]
        if values.size:
            low, high = float(values.min()), float(values.max())
            result["minimum"] = low if result["minimum"] is None else min(low, result["minimum"])
            result["maximum"] = high if result["maximum"] is None else max(high, result["maximum"])
        if "_y_" in path.name:
            labels, numbers = np.unique(block, return_counts=True)
            counts.update({str(float(v)): int(n) for v, n in zip(labels, numbers)})
    result["label_counts"] = dict(counts)
    result["length_ok"] = result["expected_bytes"] == result["actual_bytes"]
    return result


def label_audit(media, output):
    db = json.loads((ROOT / "data/whistle/whistledb.json").read_text(encoding="utf-8"))
    rows, channels = [], []
    source_sha = sha(ROOT / "data/whistle/whistledb.json")
    for file_index, item in enumerate(db["audioFiles"]):
        path = ROOT / "data/whistle" / item["path"]
        info = media[path.relative_to(ROOT).as_posix()]
        stream = next(s for s in info["probe"]["streams"] if s["codec_type"] == "audio")
        with wave.open(str(path), "rb") as wav:
            frames, rate = wav.getnframes(), wav.getframerate()
        for channel, metadata in enumerate(item["channels"]):
            labels = metadata["whistleLabels"]
            signatures = [json.dumps(x, sort_keys=True) for x in labels]
            channels.append(dict(file=item["path"], channel=channel,
                                 completelyLabeled=metadata.get("completelyLabeled"),
                                 count=len(labels), exact_duplicate_rows=len(labels)-len(set(signatures)),
                                 wav_channels=int(stream["channels"]), json_channels=len(item["channels"]),
                                 adjacent_overlap_or_unsorted=sum(labels[i]["start"] < labels[i-1]["end"]
                                                                  for i in range(1, len(labels)))))
            for index, raw in enumerate(labels):
                row = bh.annotation_row(item["path"], channel, index, raw, rate, frames, source_sha)
                row["source_json_pointer"] = row["source_json_pointer"].format(file_index=file_index)
                rows.append(row)
    save(output, "bhuman-labels.json", dict(channels=channels, rows=rows,
         file_correspondence=dict(json_files=[x["path"] for x in db["audioFiles"]],
         actual_wavs=sorted(p.name for p in (ROOT / "data/whistle").glob("*.wav")))))


def volleyball_audit(media, output, ffmpeg):
    folder = ROOT / "data/volleyball/raw"
    versions = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))}
    summaries = {}
    maps = {}
    for name, rows in versions.items():
        identities = [(r["match_id"], r["whistle_id"]) for r in rows]
        maps[name] = {key: row for key, row in zip(identities, rows)}
        per_match = {}
        invalid = []
        for match in sorted({r["match_id"] for r in rows}):
            selected = [r for r in rows if r["match_id"] == match]
            info = media.get("data/volleyball/raw/" + match + ".wav")
            duration = float(info["probe"]["format"]["duration"]) if info else None
            per_match[match] = dict(count=len(selected), duration=duration, fields={})
            for field in ("time", "t_raw", "t_anchor"):
                values = [r[field] for r in selected if field in r]
                per_match[match]["fields"][field] = dict(count=len(values), minimum=min(values) if values else None,
                    maximum=max(values) if values else None, duplicate_times=len(values)-len(set(values)))
                for r in selected:
                    if field in r and (duration is None or not isinstance(r[field], (float, int)) or
                                       not np.isfinite(r[field]) or not 0 <= r[field] <= duration):
                        invalid.append(dict(identity=[match, r["whistle_id"]], field=field, value=r[field]))
        global_ids = defaultdict(list)
        for row in rows:
            global_ids[row.get("global_id")].append([row["match_id"], row["whistle_id"]])
        summaries[name] = dict(rows=len(rows), fields=dict(Counter(k for r in rows for k in r)),
            types=dict(Counter(r.get("type") for r in rows)), matches=per_match,
            duplicate_identities=len(rows)-len(set(identities)),
            duplicate_global_ids=len(rows)-len({r.get("global_id") for r in rows}),
            exact_duplicate_rows=len(rows)-len({json.dumps(r, sort_keys=True) for r in rows}), invalid_times=invalid,
            wav_matches_without_labels=sorted({p.stem for p in folder.glob("*.wav")}-set(per_match)),
            global_id_collisions={str(k):v for k,v in global_ids.items() if len(v)>1})
    comparisons = []
    names = list(versions)
    for i, left in enumerate(names):
        for right in names[i+1:]:
            a, b = maps[left], maps[right]
            common = sorted(a.keys() & b.keys())
            fields = sorted(set(k for row in list(a.values()) + list(b.values()) for k in row))
            changes = {field: sum(a[key].get(field) != b[key].get(field) for key in common) for field in fields}
            delta = [b[key]["t_anchor"]-a[key]["t_anchor"] for key in common
                     if "t_anchor" in a[key] and "t_anchor" in b[key]]
            comparisons.append(dict(left=left, right=right, common=len(common),
                left_only=sorted(a.keys()-b.keys()), right_only=sorted(b.keys()-a.keys()),
                changed_fields=changes, anchor_delta_seconds=dict(minimum=min(delta), maximum=max(delta),
                median=float(np.median(delta)), changed=sum(x != 0 for x in delta)) if delta else None))
    # Deterministic small signal checks, no listening or exported clips.
    samples = []
    base = versions["whistles_all_reanchored.json"]
    for match in ("match1", "match7", "match14"):
        selected = [r for r in base if r["match_id"] == match]
        for row in (selected[0], selected[len(selected)//2], selected[-1]):
            for field in ("t_raw", "t_anchor"):
                center = row[field]
                command = [ffmpeg, "-v", "error", "-ss", str(max(0, center-0.5)), "-i", folder/(match+".wav"),
                           "-t", "1", "-map", "0:a:0", "-ac", "1", "-ar", "22050", "-f", "f32le", "pipe:1"]
                process = subprocess.run([str(x) for x in command], capture_output=True)
                values = np.frombuffer(process.stdout, dtype="<f4")
                spectrum = np.abs(np.fft.rfft(values*np.hanning(len(values)))) if len(values) else np.array([])
                frequencies = np.fft.rfftfreq(len(values), 1/22050) if len(values) else np.array([])
                samples.append(dict(match=match, whistle_id=row["whistle_id"], field=field, center_seconds=center,
                    returncode=process.returncode, samples=len(values),
                    rms=float(np.sqrt(np.mean(values.astype(float)**2))) if len(values) else None,
                    peak=float(np.max(np.abs(values))) if len(values) else None,
                    peak_frequency_hz=float(frequencies[np.argmax(spectrum)]) if spectrum.size else None,
                    listening="not_performed; signal_statistics_do_not_validate_label"))
    save(output, "volleyball-labels.json", dict(versions=summaries, comparisons=comparisons, signal_spotchecks=samples))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("Use a new numbered output directory")
    if ROOT / "阶段4_音频AI" not in output.parents:
        raise ValueError("Output must stay in stage4")
    output.mkdir(parents=True)
    ffbin = ROOT / "tools/ffmpeg-9.0.2-essentials_build/bin"
    ffmpeg, ffprobe = ffbin/"ffmpeg.exe", ffbin/"ffprobe.exe"
    manifest = [provenance(p) for p in sorted((ROOT/"data").rglob("*")) if p.is_file()]
    save(output, "manifest.json", manifest)
    with (output/"manifest.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)
    media, arrays = {}, []
    for row in manifest:
        path = ROOT / row["path"]
        if path.suffix.lower() in (".wav", ".mp4"):
            code, stdout, stderr = run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", path])
            info = dict(probe_returncode=code, probe_error=stderr, probe=json.loads(stdout) if code == 0 else {})
            if path.suffix.lower() == ".wav":
                decode_code, _, decode_error = run([ffmpeg, "-v", "error", "-xerror", "-i", path,
                                                   "-map", "0:a:0", "-f", "null", "-"])
                info["full_audio_decode"] = dict(returncode=decode_code, stderr=decode_error,
                                                 method="all frames to null; no listening")
            else:
                info["full_audio_decode"] = "not_performed; metadata_only"
            media[row["path"]] = info
            print("media", row["path"], flush=True)
        elif path.suffix.lower() == ".npy":
            arrays.append(npy_audit(path))
            print("npy", row["path"], flush=True)
    save(output, "media.json", media)
    save(output, "npy.json", arrays)
    label_audit(media, output)
    volleyball_audit(media, output, ffmpeg)
    hashes = defaultdict(list)
    for row in manifest:
        hashes[row["sha256"]].append(row["path"])
    after = {row["path"]: sha(ROOT/row["path"]) for row in manifest}
    unchanged = all(row["sha256"] == after[row["path"]] for row in manifest)
    summary = dict(files=len(manifest), bytes=sum(r["bytes"] for r in manifest),
        extensions=dict(Counter(Path(r["path"]).suffix for r in manifest)),
        exact_duplicate_groups=[v for v in hashes.values() if len(v)>1],
        wav_decode_pass=sum(isinstance(v["full_audio_decode"], dict) and v["full_audio_decode"]["returncode"] == 0 for v in media.values()),
        sources_unchanged=unchanged, after_sha256=after,
        npy_pair_lengths={split: next(r["shape"][0] for r in arrays if "_x_base_"+split in r["path"]) ==
                         next(r["shape"][0] for r in arrays if "_y_base_"+split in r["path"]) for split in ("train", "test")},
        versions=dict(python=sys.version, numpy=np.__version__, ffmpeg=run([ffmpeg,"-version"])[1].splitlines()[0]),
        scope="data only; all WAV full decode; MP4 metadata only; no model execution; no listening")
    save(output, "summary.json", summary)
    assert unchanged
    assert all(r["length_ok"] and r["nonfinite_values"] == 0 for r in arrays)
    assert all(summary["npy_pair_lengths"].values())
    assert all(v["probe_returncode"] == 0 for v in media.values())
    assert summary["wav_decode_pass"] == sum(Path(k).suffix == ".wav" for k in media)
    print(json.dumps({k:v for k,v in summary.items() if k not in ("after_sha256", "versions")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

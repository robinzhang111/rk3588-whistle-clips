"""Static asset checks only; never imports or executes the upstream project."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import re
import struct

import numpy as np


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    snapshot, data = Path(args.snapshot), Path(args.data)
    target = snapshot/"static-audit.json"
    if target.exists():
        raise FileExistsError(target)
    source_files = [p for p in snapshot.rglob("*") if p.is_file() and
                    p.name not in ("model-config-extracted.json", "tree.json")]
    tree = json.loads((snapshot/"tree.json").read_text(encoding="utf-8-sig"))
    expected_sizes = {row["path"]: row.get("size") for row in tree["tree"]}
    source_check = [{"path": p.relative_to(snapshot).as_posix(), "bytes": p.stat().st_size,
                     "sha256": sha(p), "github_tree_size_matches":
                     p.stat().st_size == expected_sizes[p.relative_to(snapshot).as_posix()]}
                    for p in source_files]
    assets = []
    for path in sorted(data.rglob("*.npy")):
        before = sha(path)
        with path.open("rb") as stream:
            version = np.lib.format.read_magic(stream)
            shape, order, dtype = np.lib.format._read_array_header(stream, version)
            expected = stream.tell()+int(np.prod(shape))*dtype.itemsize
            count, finite = 0, True
            low, high = float("inf"), float("-inf")
            counts = {}
            while True:
                block = np.fromfile(stream, dtype=dtype, count=1000000)
                if not block.size:
                    break
                count += block.size
                finite = finite and bool(np.isfinite(block).all())
                low, high = min(low, float(block.min())), max(high, float(block.max()))
                if path.name.startswith("whistle_y"):
                    values, numbers = np.unique(block, return_counts=True)
                    for value, number in zip(values, numbers):
                        key = str(float(value))
                        counts[key] = counts.get(key, 0)+int(number)
        assets.append(dict(filename=path.name, shape=shape, dtype=str(dtype), fortran=order,
                           bytes=path.stat().st_size, expected_bytes=expected, length_ok=expected==path.stat().st_size,
                           scanned_values=count, finite=finite, minimum=low, maximum=high,
                           label_counts=counts or None, sha256=before, unchanged=before==sha(path)))
    wavs = []
    for path in sorted((data/"TestBench").glob("*.wav")):
        before = sha(path)
        with path.open("rb") as stream:
            header = stream.read(12)
            if header[:4] != b"RIFF" or header[8:] != b"WAVE":
                raise ValueError("Not RIFF/WAVE: "+str(path))
            fmt, payload = None, None
            while True:
                block = stream.read(8)
                if len(block) != 8:
                    break
                name, size = struct.unpack("<4sI", block)
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
            subtype = struct.unpack("<I", fmt[24:28])[0] if tag == 65534 and len(fmt) >= 40 else tag
            if subtype != 3 or bits != 32:
                raise ValueError("Audit expects float32 WAV")
            stream.seek(payload[0])
            samples = np.fromfile(stream, dtype="<f4", count=payload[1]//4).reshape(-1, channels)
        # Compare equivalent FFT implementations on one real source window only.
        window = samples[:1024, 0]*np.hamming(1024)
        fft = np.fft.fft(window)[:513]
        rfft = np.fft.rfft(window)
        feature = 20*np.log10(np.abs(fft))
        wavs.append(dict(filename=path.name, format_tag=tag, subtype=subtype, sample_rate=rate,
                         channels=channels, bits=bits, sample_frames=len(samples), duration_seconds=len(samples)/rate,
                         data_bytes_within_file=payload[0]+payload[1]<=path.stat().st_size,
                         finite=bool(np.isfinite(samples).all()), first_channel_first_window_feature_finite=bool(np.isfinite(feature).all()),
                         first_channel_first_window_fft_rfft_max_abs_difference=float(np.max(np.abs(fft-rfft))),
                         sha256=before, unchanged=before==sha(path)))
    model_path = snapshot/"PreTrainedNetworks/WhistleDetection/WhistleNetMk16.h5"
    raw = model_path.read_bytes()
    config = None
    for match in re.finditer(b'\{"class_name":', raw):
        try:
            item, _ = json.JSONDecoder().raw_decode(raw[match.start():].decode("latin1"))
            if "layers" in item.get("config", {}):
                config = item
                break
        except ValueError:
            pass
    if config is None:
        raise ValueError("No embedded model architecture JSON found")
    (snapshot/"model-config-extracted.json").write_text(json.dumps(config, indent=2)+"\n", encoding="utf-8")
    layers = [{"name": row["config"]["name"], "type": row["class_name"],
               "config": {key: row["config"][key] for key in ("batch_input_shape", "filters", "kernel_size",
                 "strides", "padding", "units", "activation") if key in row["config"]}}
              for row in config["config"]["layers"]]
    result = dict(created_utc=datetime.now(timezone.utc).isoformat(), commit=tree["sha"],
                  environment=dict(python=platform.python_version(), bitness=struct.calcsize("P")*8,
                    numpy=np.__version__, package_presence={name: importlib.util.find_spec(name) is not None
                    for name in ("tensorflow", "h5py", "scipy", "torch", "onnxruntime")}),
                  source_files=source_check, npy=assets, wav=wavs,
                  model=dict(bytes=len(raw), sha256=sha(model_path), hdf5_signature=raw[:8]==b"\x89HDF\r\n\x1a\n",
                    layers=layers, inspection="Embedded JSON extraction only; not HDF5 weight validation or Keras load"),
                  scope="Static audit only; no upstream execution, model inference, installation, training or board upload")
    assert all(row["github_tree_size_matches"] for row in source_check)
    assert all(row["length_ok"] and row["finite"] and row["unchanged"] for row in assets)
    assert all(row["finite"] and row["unchanged"] and row["data_bytes_within_file"] for row in wavs)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print("Static audit saved:", target)
    print("WAV subtype:", [(row["filename"], row["subtype"]) for row in wavs])


if __name__ == "__main__":
    main()

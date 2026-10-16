"""Load the pinned H5 and verify a fixed balanced NPY sample on CPU. No training."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import time

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import h5py
import numpy as np
import tensorflow as tf


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True, help="New directory, never overwrite an existing run")
    args = parser.parse_args()
    model_path, data, output = Path(args.model), Path(args.data), Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    expected_path = model_path.parent.parent.parent / "static-audit.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assets = {row["filename"]: row for row in expected["npy"]}
    files = [model_path, data/"whistle_x_base_test.npy", data/"whistle_y_base_test.npy"]
    before = {str(path.absolute()): sha(path) for path in files}
    if before[str(model_path.absolute())] != expected["model"]["sha256"]:
        raise ValueError("Model differs from static-audit snapshot")
    for path in files[1:]:
        if before[str(path.absolute())] != assets[path.name]["sha256"]:
            raise ValueError("Data differs from static-audit snapshot: "+path.name)
    labels = np.load(str(files[2]), allow_pickle=False).reshape(-1)
    rng = np.random.RandomState(20261011)
    selected = np.sort(np.concatenate([rng.choice(np.flatnonzero(labels == value), 32, replace=False)
                                      for value in (0, 1)]))
    # Seek only selected original rows: never materialize or copy the whole feature array.
    with files[1].open("rb") as stream:
        version = np.lib.format.read_magic(stream)
        shape, fortran, dtype = np.lib.format._read_array_header(stream, version)
        offset = stream.tell()
        if shape != (len(labels), 513, 1) or fortran:
            raise ValueError("Expected C-order (rows,513,1) NPY input")
        rows = []
        for index in selected:
            stream.seek(offset+int(index)*513*dtype.itemsize)
            rows.append(np.fromfile(stream, dtype=dtype, count=513))
    x = np.asarray(rows, dtype=np.float32).reshape(-1, 513, 1)
    if not np.isfinite(x).all():
        raise ValueError("Nonfinite input")
    write(output/"selection-before-prediction.json", dict(seed=20261011, samples_per_label=32,
        original_row_indices=selected.tolist(), selection="Fixed random sample within each label; no scores used",
        model_sha256=before[str(model_path.absolute())], threshold=.65,
        threshold_basis="Upstream annotation default, not selected or validated for this engineering task",
        scope="Compatibility and small-batch checks only, not full benchmark or independent test"))
    start = time.perf_counter()
    # A file handle bypasses TF 2.13's Windows path check, which fails on this
    # Chinese workspace path. h5py opens the same original file read-only.
    with h5py.File(str(model_path), "r") as asset:
        model = tf.keras.models.load_model(asset, compile=False)
    load_seconds = time.perf_counter()-start
    if model.input_shape != (None,513,1) or model.output_shape != (None,1):
        raise ValueError("Unexpected loaded model input/output")
    def forward(values):
        predictions = np.asarray(model(values, training=False)).reshape(-1)
        if len(predictions) != len(values) or not np.isfinite(predictions).all() or not ((predictions >= 0) & (predictions <= 1)).all():
            raise ValueError("Invalid prediction values")
        return predictions
    start = time.perf_counter()
    predictions = np.concatenate([forward(x[i:i+16]) for i in range(0,len(x),16)])
    first_seconds = time.perf_counter()-start
    start = time.perf_counter()
    one_batch = forward(x)
    second_seconds = time.perf_counter()-start
    repeated = np.concatenate([forward(x[i:i+16]) for i in range(0,len(x),16)])
    batch_error = float(np.max(np.abs(predictions-one_batch)))
    repeat_error = float(np.max(np.abs(predictions-repeated)))
    if not np.allclose(predictions, one_batch, rtol=1e-5, atol=1e-6) or not np.allclose(predictions, repeated, rtol=1e-5, atol=1e-6):
        raise ValueError("Predictions changed beyond numerical comparison tolerance")
    truth = labels[selected].astype(int)
    binary = predictions >= .65
    tp = int(((truth==1) & binary).sum())
    fp = int(((truth==0) & binary).sum())
    fn = int(((truth==1) & ~binary).sum())
    tn = int(((truth==0) & ~binary).sum())
    write(output/"predictions.json", [dict(original_row_index=int(index), label=int(label),
        score=float(score), prediction_at_upstream_threshold=int(decision))
        for index,label,score,decision in zip(selected,truth,predictions,binary)])
    after = {str(path.absolute()): sha(path) for path in files}
    if before != after:
        raise ValueError("Model or data changed during inference")
    with h5py.File(str(model_path), "r") as asset:
        original_keras = asset.attrs.get("keras_version")
        if isinstance(original_keras, bytes):
            original_keras = original_keras.decode("utf-8")
    result = dict(completed_utc=datetime.now(timezone.utc).isoformat(),
        versions=dict(python=platform.python_version(), tensorflow=tf.__version__, keras=tf.keras.__version__
            if hasattr(tf.keras,"__version__") else __import__("keras").__version__, numpy=np.__version__, h5py=h5py.__version__),
        original_h5_keras_version=original_keras, input_shape=list(model.input_shape), output_shape=list(model.output_shape),
        model_parameters=model.count_params(), layer_count=len(model.layers), samples=len(x),
        cpu_only=True, visible_devices=[device.name for device in tf.config.get_visible_devices()],
        score_min=float(predictions.min()), score_max=float(predictions.max()),
        TP=tp,FP=fp,FN=fn,TN=tn,threshold=.65,
        result_scope="Balanced 64-row published-test compatibility sample, not overall accuracy or event performance",
        model_load_seconds=load_seconds, first_four_batches_seconds=first_seconds, subsequent_single_batch_seconds=second_seconds,
        timing_scope="This PC/model call only; excludes process startup, TensorFlow import, file/hash reads and audio preprocessing; not board or online measurement",
        batch_vs_single_max_absolute_difference=batch_error, repeat_max_absolute_difference=repeat_error,
        checks=dict(model_loaded=True, finite_scores=True, scores_in_range=True, output_count_matches=True,
                    batch_comparison=True, repeat_comparison=True, source_hashes_unchanged=True),
        no_training=True,no_board_upload=True)
    write(output/"integrity.json", dict(before=before, after=after, unchanged=True))
    write(output/"result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

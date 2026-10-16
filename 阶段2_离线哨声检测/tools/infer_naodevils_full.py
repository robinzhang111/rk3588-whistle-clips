"""Full published-test re-evaluation at fixed threshold. No training or threshold search."""
import argparse
import csv
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
import keras
import numpy as np
import tensorflow as tf

THRESHOLD = .65
BATCH_SIZE = 512


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
    parser.add_argument("--output", required=True, help="New run directory only")
    args = parser.parse_args()
    started = time.perf_counter()
    model_path, data, output = Path(args.model), Path(args.data), Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    audit = json.loads((model_path.parent.parent.parent/"static-audit.json").read_text(encoding="utf-8"))
    files = [model_path, data/"whistle_x_base_test.npy", data/"whistle_y_base_test.npy"]
    expected = [audit["model"]["sha256"]]+[
        next(row["sha256"] for row in audit["npy"] if row["filename"] == path.name) for path in files[1:]]
    before = {str(path.absolute()): sha(path) for path in files}
    if list(before.values()) != expected:
        raise ValueError("Source asset hash differs from static audit")
    labels = np.load(str(files[2]), allow_pickle=False).reshape(-1)
    if len(labels) != 147440 or not np.isin(labels, [0,1]).all():
        raise ValueError("Unexpected published test labels")
    write(output/"plan-before-prediction.json", dict(created_utc=datetime.now(timezone.utc).isoformat(),
        threshold=THRESHOLD, threshold_basis="Upstream annotation default; fixed by user before this run",
        batch_size=BATCH_SIZE, row_order="All original rows 0..147439 exactly once, no selection",
        original_source_hashes=before, tool_sha256=sha(Path(__file__)),
        scope="Published benchmark re-evaluation; pretrained training/selection provenance unknown; not independent blind test",
        no_training=True, no_threshold_search=True, no_board_upload=True))
    start = time.perf_counter()
    with h5py.File(str(model_path), "r") as asset:
        model = tf.keras.models.load_model(asset, compile=False)
    model_load_seconds = time.perf_counter()-start
    if model.input_shape != (None,513,1) or model.output_shape != (None,1):
        raise ValueError("Unexpected model shape")
    totals = {"TP":0,"FP":0,"FN":0,"TN":0}
    timings = dict(read_and_float32_seconds=0., forward_seconds=0., csv_write_seconds=0.)
    batch_timings, all_scores = [], []
    loop_start = time.perf_counter()
    with files[1].open("rb") as stream, (output/"predictions.csv").open("w",encoding="utf-8",newline="") as target:
        version = np.lib.format.read_magic(stream)
        shape, fortran, dtype = np.lib.format._read_array_header(stream, version)
        if shape != (len(labels),513,1) or fortran:
            raise ValueError("Expected C-order (147440,513,1) NPY input")
        writer = csv.writer(target)
        writer.writerow(["original_row_index","label","score","prediction_at_0_65"])
        for first in range(0,len(labels),BATCH_SIZE):
            count = min(BATCH_SIZE,len(labels)-first)
            start = time.perf_counter()
            values = np.fromfile(stream,dtype=dtype,count=count*513)
            if len(values) != count*513:
                raise ValueError("Unexpected EOF")
            x = values.reshape(count,513,1).astype(np.float32)
            if not np.isfinite(x).all():
                raise ValueError("Nonfinite input")
            timings["read_and_float32_seconds"] += time.perf_counter()-start
            start = time.perf_counter()
            scores = np.asarray(model(x,training=False)).reshape(-1)
            duration = time.perf_counter()-start
            timings["forward_seconds"] += duration
            batch_timings.append(duration)
            if len(scores)!=count or not np.isfinite(scores).all() or not ((scores>=0)&(scores<=1)).all():
                raise ValueError("Invalid model output")
            all_scores.append(scores)
            # Float64 comparison preserves the decimal reference threshold; no float32 rounding of .65.
            binary = scores.astype(np.float64)>=THRESHOLD
            truth = labels[first:first+count].astype(int)
            for name,label,decision in [("TP",1,True),("FP",0,True),("FN",1,False),("TN",0,False)]:
                totals[name] += int(((truth==label)&(binary==decision)).sum())
            start = time.perf_counter()
            writer.writerows((first+i,int(truth[i]),format(float(scores[i]),".9g"),int(binary[i])) for i in range(count))
            timings["csv_write_seconds"] += time.perf_counter()-start
            if (first//BATCH_SIZE+1)%16==0 or first+count==len(labels):
                print("Scored rows:",first+count,"/",len(labels),flush=True)
        if stream.read(1):
            raise ValueError("Unexpected trailing bytes in NPY")
    loop_seconds = time.perf_counter()-loop_start
    scores = np.concatenate(all_scores)
    np.save(str(output/"scores.npy"),scores,allow_pickle=False)
    after = {str(path.absolute()): sha(path) for path in files}
    if before != after:
        raise ValueError("Model or data changed during run")
    write(output/"integrity.json",dict(before=before,after=after,unchanged=True,
        predictions_csv_sha256=sha(output/"predictions.csv"), scores_npy_sha256=sha(output/"scores.npy")))
    tp,fp,fn,tn = [totals[name] for name in ("TP","FP","FN","TN")]
    result = dict(completed_utc=datetime.now(timezone.utc).isoformat(),
        scope="Published benchmark window classification re-evaluation, not independent blind test or event evaluation",
        samples=len(labels), positive_labels=int((labels==1).sum()), negative_labels=int((labels==0).sum()),
        threshold=THRESHOLD, threshold_tuned=False, batch_size=BATCH_SIZE, batches=len(batch_timings),
        **totals, precision=tp/(tp+fp) if tp+fp else None, recall=tp/(tp+fn) if tp+fn else None,
        F1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None, accuracy=(tp+tn)/len(labels),
        false_positive_rate=fp/(fp+tn), false_negative_rate=fn/(tp+fn),
        always_negative_accuracy=tn*0+int((labels==0).sum())/len(labels),
        score_min=float(scores.min()), score_max=float(scores.max()),
        versions=dict(python=platform.python_version(),tensorflow=tf.__version__,keras=keras.__version__,
                      numpy=np.__version__,h5py=h5py.__version__),
        visible_devices=[device.name for device in tf.config.get_visible_devices()],
        model_load_seconds=model_load_seconds, processing=timings, read_forward_score_write_loop_seconds=loop_seconds,
        batch_forward_median_seconds=float(np.median(batch_timings)), batch_forward_p95_seconds=float(np.percentile(batch_timings,95)),
        windows_per_forward_second=len(labels)/timings["forward_seconds"],
        command_body_seconds_excluding_import=time.perf_counter()-started,
        timing_scope="This PC CPU. Main loop includes reads, casting, forward, scoring, CSV writing; excludes TensorFlow import and hashes. Not audio hours or online/board latency.",
        undefined_event_metrics=["event recall","false alarms per hour","onset error","online delay"],
        original_audio_positions_available=False, all_sources_unchanged=True,no_training=True,no_board_upload=True)
    write(output/"result.json",result)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__ == "__main__":
    main()

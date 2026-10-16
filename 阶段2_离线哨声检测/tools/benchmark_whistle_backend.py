"""Compare single-window CPU/NPU calls and complete file processing."""
import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import time

import numpy as np
import detect_file as d


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['cpu','npu'], required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    begin = time.perf_counter()
    shared = np.load(str(args.export/'features.npy'), allow_pickle=False)
    shared_silent = np.load(str(args.export/'silent.npy'), allow_pickle=False)
    protected = {str(p):d.sha(p) for p in [args.model,args.input,args.export/'features.npy',args.export/'silent.npy']}
    initialize = time.perf_counter()
    runtime = None
    report = dict(backend=args.backend, protected_sha256=protected, threshold=.65, rule='v1',
        absolute_utc_start=None, timing_scope='perf_counter; single-window calls include tensor input/output transfer and host overhead; hardware compute alone not measured; excludes process startup/top-level NumPy import',
        shared_feature_shape=list(shared.shape), versions=dict(numpy=np.__version__))
    try:
        if args.backend == 'cpu':
            if d.sha(args.model) != d.MODEL_SHA:
                raise ValueError('Fixed H5 hash mismatch')
            import h5py
            import tensorflow as tf
            with h5py.File(str(args.model), 'r') as asset:
                model = tf.keras.models.load_model(asset, compile=False)
            report['versions']['tensorflow'] = tf.__version__
            invoke = lambda feature: float(np.asarray(model(feature[None], training=False)).reshape(-1)[0])
        else:
            from rknnlite.api import RKNNLite
            runtime = RKNNLite(verbose=False)
            if runtime.load_rknn(str(args.model)) != 0 or runtime.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO) != 0:
                raise RuntimeError('NPU model/runtime initialization failed')
            report['versions']['lite2'] = metadata.version('rknn-toolkit-lite2')
            report['sdk_version'] = runtime.get_sdk_version()
            def invoke(feature):
                output = runtime.inference(inputs=[np.ascontiguousarray(feature[None],dtype=np.float32)], data_format=['nhwc'])
                if output is None or len(output) != 1 or output[0].size != 1:
                    raise ValueError('Invalid NPU output shape')
                return float(output[0].reshape(-1)[0])
        report['initialize_seconds_including_import'] = time.perf_counter()-initialize
        decode_start = time.perf_counter()
        audio, adaptation = d.decode(args.input, args.output, Path('/usr/bin/ffmpeg'), Path('/usr/bin/ffprobe'))
        report['media_seconds'] = time.perf_counter()-decode_start
        starts = d.c.starts_for_samples(len(audio))
        feature_start = time.perf_counter()
        features, silent, _ = d.c.features_for_starts(audio, starts)
        report['feature_seconds'] = time.perf_counter()-feature_start
        report.update(adapted_frames=len(audio), window_count=len(starts), sample_rate=22050,
                      channels=audio.shape[1], source_sha256=d.sha(args.input))
        if features.shape != shared.shape or not np.array_equal(silent,shared_silent):
            raise ValueError('File preprocessing shape/silence differs from reference')
        report['max_abs_feature_difference'] = float(np.max(np.abs(features-shared)))
        def score(data):
            values = np.zeros(silent.size, dtype=np.float32)
            for index, feature in enumerate(data.reshape(-1,513,1)):
                if not silent.reshape(-1)[index]:
                    values[index] = invoke(feature)
            if not np.isfinite(values).all() or not ((values>=0)&(values<=1)).all():
                raise ValueError('Nonfinite or out-of-range output')
            return values.reshape(silent.shape)
        call_start = time.perf_counter()
        file_scores = score(features)
        report['file_forward_seconds_including_transfer'] = time.perf_counter()-call_start
        events = d.c.candidate_events(file_scores,starts,len(audio),22050)
        np.save(str(args.output/'file-scores.npy'),file_scores,allow_pickle=False)
        d.write(args.output/'events.json',dict(events=events,time_basis='file relative samples',absolute_utc_start=None))
        report['candidate_count'] = len(events)
        report['complete_file_seconds_through_result_save'] = time.perf_counter()-begin
        # Shared arrays isolate model arithmetic from platform media/FFT differences.
        warmup_start = time.perf_counter()
        shared_scores = score(shared)
        report['shared_warmup_seconds'] = time.perf_counter()-warmup_start
        np.save(str(args.output/'shared-scores.npy'),shared_scores,allow_pickle=False)
        times = []
        for _ in range(3):
            start = time.perf_counter()
            repeated = score(shared)
            times.append(time.perf_counter()-start)
            if not np.array_equal(repeated,shared_scores):
                raise ValueError('Repeat output differs; inspect runtime stability')
        report.update(status='completed', shared_repeat_seconds=times,
                      shared_repeat_median_seconds=float(np.median(times)),
                      active_window_channel_calls=int((~silent).sum()))
        if any(d.sha(p)!=h for p,h in protected.items()):
            raise ValueError('Protected input changed')
    except Exception as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        if runtime is not None:
            runtime.release()
        d.write(args.output/'benchmark.json',report)
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()

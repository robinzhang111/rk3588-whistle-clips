"""Export the fixed H5 without quantization and verify shared file features."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import detect_file as d


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if d.sha(d.MODEL) != d.MODEL_SHA:
        raise ValueError('Fixed H5 hash mismatch')
    import tensorflow as tf
    import h5py
    with h5py.File(str(d.MODEL), 'r') as asset:
        model = tf.keras.models.load_model(asset, compile=False)
    model.summary()
    concrete = tf.function(lambda x: model(x, training=False)).get_concrete_function(
        tf.TensorSpec([1, 513, 1], tf.float32, name='whistle_features'))
    converter = tf.lite.TFLiteConverter.from_concrete_functions([concrete], model)
    blob = converter.convert()
    target = args.output/'WhistleNetMk16-float32.tflite'
    target.write_bytes(blob)
    summary = json.loads((args.run/'summary.json').read_text(encoding='utf-8'))
    audio, rate = d.c.read_float_wav(summary['adaptation']['adapted_path'])
    starts = d.c.starts_for_samples(len(audio))
    features, silent, _ = d.c.features_for_starts(audio, starts)
    np.save(str(args.output/'features.npy'), features, allow_pickle=False)
    np.save(str(args.output/'silent.npy'), silent, allow_pickle=False)
    interpreter = tf.lite.Interpreter(model_content=blob)
    interpreter.allocate_tensors()
    inp, out = interpreter.get_input_details()[0], interpreter.get_output_details()[0]
    scores = np.zeros(silent.size, dtype=np.float32)
    forward = 0.
    for index, feature in enumerate(features.reshape(-1,513,1)):
        if silent.reshape(-1)[index]:
            continue
        begin = time.perf_counter()
        interpreter.set_tensor(inp['index'], feature[None])
        interpreter.invoke()
        scores[index] = interpreter.get_tensor(out['index']).reshape(-1)[0]
        forward += time.perf_counter()-begin
    scores = scores.reshape(silent.shape)
    reference = np.load(str(args.run/'scores.npy'), allow_pickle=False)
    np.save(str(args.output/'tflite-scores.npy'), scores, allow_pickle=False)
    decisions_equal = bool(np.array_equal(scores.astype(float)>=.65, reference.astype(float)>=.65))
    events = d.c.candidate_events(scores, starts, len(audio), rate)
    old = d.c.candidate_events(reference, starts, len(audio), rate)
    positions = lambda es: [(e['first_window'],e['last_window']) for e in es]
    d.write(args.output/'export.json', dict(h5_sha256=d.MODEL_SHA, tflite_sha256=d.sha(target),
        tensorflow=tf.__version__, quantized=False, input_shape=inp['shape'].tolist(),
        output_shape=out['shape'].tolist(), layers=[dict(name=l.name,kind=type(l).__name__) for l in model.layers],
        operators=[op['op_name'] for op in interpreter._get_ops_details()],
        max_abs_difference=float(np.max(np.abs(scores-reference))), threshold_decisions_equal=decisions_equal,
        event_positions_equal=positions(events)==positions(old), candidate_count=len(events),
        shared_features_sha256=d.sha(args.output/'features.npy'), file_run_sha256=d.sha(args.run/'run.json'),
        forward_seconds_including_tensor_transfer=forward, not_online_latency=True))
    if not np.isfinite(scores).all() or not decisions_equal or positions(events)!=positions(old):
        raise ValueError('Export output or threshold/event decisions differ; see export.json')
    print((args.output/'export.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    main()

"""Export the unchanged fixed H5 to ONNX and verify identical file features."""
import argparse
import json
from pathlib import Path
import sys

import detect_file as d


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dependencies',required=True,type=Path)
    parser.add_argument('--features',required=True,type=Path)
    parser.add_argument('--reference',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    # Separate process-local export dependencies; original CPU packages are read only.
    sys.path.insert(0,str(args.dependencies.absolute()))
    import h5py
    import numpy as np
    import tensorflow as tf
    import tf2onnx
    import onnx
    import onnxruntime as ort
    if d.sha(d.MODEL)!=d.MODEL_SHA:
        raise ValueError('Fixed H5 mismatch')
    with h5py.File(str(d.MODEL),'r') as asset:
        model = tf.keras.models.load_model(asset,compile=False)
    proto,_ = tf2onnx.convert.from_keras(model,input_signature=[
        tf.TensorSpec([1,513,1],tf.float32,name='whistle_features')],opset=13)
    onnx.checker.check_model(proto)
    target = args.output/'WhistleNetMk16-float32.onnx'
    target.write_bytes(proto.SerializeToString())
    features = np.load(str(args.features/'features.npy'),allow_pickle=False)
    silent = np.load(str(args.features/'silent.npy'),allow_pickle=False)
    session = ort.InferenceSession(target.read_bytes(),providers=['CPUExecutionProvider'])
    scores = np.zeros(silent.size,dtype=np.float32)
    for i,feature in enumerate(features.reshape(-1,513,1)):
        if not silent.reshape(-1)[i]:
            scores[i] = session.run(None,{session.get_inputs()[0].name:feature[None]})[0].reshape(-1)[0]
    scores = scores.reshape(silent.shape)
    old = np.load(str(args.reference/'scores.npy'),allow_pickle=False)
    summary = json.loads((args.reference/'summary.json').read_text(encoding='utf-8'))
    starts = d.c.starts_for_samples(summary['adapted_frames'])
    event_positions = lambda values: [(e['first_window'],e['last_window']) for e in
        d.c.candidate_events(values,starts,summary['adapted_frames'],22050)]
    decisions_equal = bool(np.array_equal(scores.astype(float)>=.65,old.astype(float)>=.65))
    positions_equal = event_positions(scores)==event_positions(old)
    np.save(str(args.output/'onnx-scores.npy'),scores,allow_pickle=False)
    report = dict(h5_sha256=d.MODEL_SHA,onnx_sha256=d.sha(target),opset=13,quantized=False,
        versions=dict(tensorflow=tf.__version__,tf2onnx=tf2onnx.__version__,onnx=onnx.__version__,onnxruntime=ort.__version__),
        input_shape=session.get_inputs()[0].shape,output_shape=session.get_outputs()[0].shape,
        operators=sorted(set(n.op_type for n in proto.graph.node)),
        max_abs_difference=float(np.max(np.abs(scores-old))),threshold_decisions_equal=decisions_equal,
        event_positions_equal=positions_equal,shared_features_sha256=d.sha(args.features/'features.npy'),
        cpu_packages_not_modified=True)
    d.write(args.output/'export.json',report)
    if not np.isfinite(scores).all() or not decisions_equal or not positions_equal:
        raise ValueError('ONNX reference behavior differs; see export.json')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()

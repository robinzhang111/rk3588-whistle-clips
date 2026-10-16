"""Verify isolated CPU environment, uploaded assets, and fixed H5 forward."""
import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import sys

import detect_file as d


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    root = args.root.absolute()
    manifest = json.loads((root/'upload-manifest.json').read_text(encoding='utf-8'))
    for name, info in manifest.items():
        path = root/name
        if path.stat().st_size != info['bytes'] or d.sha(path) != info['sha256']:
            raise ValueError('Upload mismatch: '+name)
    import h5py
    import numpy as np
    import tensorflow as tf
    with h5py.File(str(root/'assets/model.h5'), 'r') as asset:
        model = tf.keras.models.load_model(asset, compile=False)
    assert model.input_shape == (None,513,1) and model.output_shape == (None,1)
    scores = np.asarray(model(np.zeros((1,513,1), dtype=np.float32), training=False))
    assert np.isfinite(scores).all() and ((scores>=0)&(scores<=1)).all()
    report = dict(upload_manifest_verified=True, python=sys.version, executable=sys.executable,
        prefix=sys.prefix, base_prefix=sys.base_prefix,
        venv_configuration=(Path(sys.prefix)/'pyvenv.cfg').read_text(),
        packages={p:metadata.version(p) for p in ['tensorflow-cpu-aws','keras','numpy','h5py']},
        input_shape=model.input_shape, output_shape=model.output_shape, finite_forward=True,
        devices=[device.name for device in tf.config.get_visible_devices()],
        synthetic_zero_feature_probe_not_audio=True)
    d.write(root/'cpu-verification.json', report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()

"""Build an RK3588 FP16 model from a verified unquantized intermediate."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--format', choices=['tflite','onnx'], default='tflite')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.export/('WhistleNetMk16-float32.'+args.format)
    manifest = json.loads((args.export/'export.json').read_text(encoding='utf-8'))
    if sha(source) != manifest[args.format+'_sha256'] or not manifest['threshold_decisions_equal']:
        raise ValueError('Unverified intermediate model')
    from rknn.api import RKNN
    model = RKNN(verbose=True)
    begin = time.perf_counter()
    report = dict(source_sha256=sha(source), h5_sha256=manifest['h5_sha256'],
        toolkit=metadata.version('rknn-toolkit2'), target='rk3588', integer_quantization=False,
        internal_float_dtype='float16', mean_values=None, std_values=None, steps={})
    try:
        operations = [
            ('config', lambda: model.config(target_platform='rk3588', float_dtype='float16')),
        ('load_'+args.format, lambda: model.load_tflite(model=str(source)) if args.format=='tflite'
         else model.load_onnx(model=str(source))),
            ('build', lambda: model.build(do_quantization=False)),
            ('export_rknn', lambda: model.export_rknn(str(args.output/'WhistleNetMk16-fp16.rknn')))]
        for name, operation in operations:
            start = time.perf_counter()
            result = operation()
            report['steps'][name] = dict(return_code=result, seconds=time.perf_counter()-start)
            if result != 0:
                raise RuntimeError(name+' failed: '+str(result))
        report.update(status='built_not_runtime_verified', total_seconds=time.perf_counter()-begin,
                      rknn_sha256=sha(args.output/'WhistleNetMk16-fp16.rknn'))
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        model.release()
        (args.output/'conversion.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()

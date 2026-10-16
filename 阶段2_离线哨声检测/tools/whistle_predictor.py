"""Fixed model adapters; NPU behavior follows the validated benchmark interface."""
import numpy as np
from detect_file import sha

RKNN_SHA = '82f165c8434508d026bb3c51e7528f16463b9eb3a9be7e0d8c4bcbb873395130'
ONNX_SHA = '4fcf70a935cdca1a7618480ca9df5375a476bddacf35a8fe03db13617441204f'


class NpuPredictor:
    def __init__(self,model):
        if sha(model)!=RKNN_SHA:
            raise ValueError('Fixed FP16 RKNN hash mismatch')
        from rknnlite.api import RKNNLite
        self.runtime = RKNNLite(verbose=False)
        if self.runtime.load_rknn(str(model))!=0 or self.runtime.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO)!=0:
            self.runtime.release()
            raise RuntimeError('NPU initialization failed')
        self.sdk_version = self.runtime.get_sdk_version()

    def __call__(self,feature):
        output = self.runtime.inference(inputs=[np.ascontiguousarray(feature[None],dtype=np.float32)],data_format=['nhwc'])
        if output is None or len(output)!=1 or output[0].size!=1:
            raise ValueError('Invalid NPU output shape')
        return float(output[0].reshape(-1)[0])

    def close(self):
        self.runtime.release()


class OnnxReference:
    def __init__(self,model):
        if sha(model)!=ONNX_SHA:
            raise ValueError('Fixed ONNX reference hash mismatch')
        import onnxruntime as ort
        self.session = ort.InferenceSession(model.read_bytes(),providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name

    def __call__(self,feature):
        return float(self.session.run(None,{self.input_name:feature[None]})[0].reshape(-1)[0])

    def close(self):
        self.session = None

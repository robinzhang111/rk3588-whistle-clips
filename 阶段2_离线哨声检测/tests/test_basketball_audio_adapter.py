import sys
from pathlib import Path
import unittest
import numpy as np

STAGE=Path(__file__).absolute().parent.parent
sys.path.insert(0,str(STAGE/'tools'))
from basketball_audio_adapter import resample_array, target_boundary, source_boundary
from continuous_whistle import features_for_starts, starts_for_samples
FFMPEG=STAGE.parent/'tools/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe'


class AdapterTests(unittest.TestCase):
    def test_even_and_odd_frame_lengths(self):
        for frames in [44100,44101,44102]:
            y=resample_array(np.zeros((frames,1),dtype=np.float32),FFMPEG)
            self.assertLessEqual(abs(len(y)*2-frames),1)

    def test_silence_exact_zero(self):
        y=resample_array(np.zeros((44100,2),dtype=np.float32),FFMPEG)
        self.assertEqual(y.shape,(22050,2))
        self.assertTrue(np.all(y==0))

    def test_dc_gain_and_no_float_clipping(self):
        x=np.tile(np.array([1.75,-.25],dtype=np.float32),(44100,1))
        y=resample_array(x,FFMPEG)
        np.testing.assert_allclose(y[100:-100],np.tile([1.75,-.25],(len(y)-200,1)),atol=1e-5)
        self.assertGreater(y[:,0].max(),1)

    def test_passband_and_antialias(self):
        t=np.arange(44100)/44100
        for freq in [1000,5000,8000,15000]:
            y=resample_array((.5*np.sin(2*np.pi*freq*t)).astype(np.float32)[:,None],FFMPEG)[100:-100,0]
            ratio=float(np.sqrt(np.mean(y.astype(float)**2)))/(.5/np.sqrt(2))
            if freq<=8000:self.assertLess(abs(ratio-1),.01)
            else:self.assertLess(ratio,.00316228)  # more than 50 dB suppression at 15 kHz

    def test_stereo_no_mix_or_swap(self):
        t=np.arange(44100)/44100
        x=np.stack([.3*np.sin(2*np.pi*1000*t),.7*np.sin(2*np.pi*3000*t)],axis=1).astype(np.float32)
        y=resample_array(x,FFMPEG)
        for c in range(2):
            solo=resample_array(x[:,c:c+1],FFMPEG)
            np.testing.assert_allclose(y[:,c],solo[:,0],atol=1e-6,rtol=1e-6)

    def test_impulse_grid_alignment(self):
        for location in [10000,10001,32000]:
            x=np.zeros((44100,1),dtype=np.float32);x[location]=1
            y=resample_array(x,FFMPEG)
            peak=int(np.argmax(np.abs(y[:,0])))
            self.assertLessEqual(abs(2*peak-location),1)

    def test_annotation_roundtrip_and_eof(self):
        for location in [0,1,100305,105721,283563]:
            j=target_boundary(location,141782)
            self.assertLessEqual(abs(source_boundary(j,283563)-location),1)
        self.assertEqual(target_boundary(283563,141781),141781)

    def test_feature_shape_for_actual_channel_counts(self):
        for channels in [1,2]:
            x=np.random.RandomState(1).normal(size=(44100,channels)).astype(np.float32)
            y=resample_array(x,FFMPEG)
            feature,silent,_=features_for_starts(y,starts_for_samples(len(y))[:4])
            self.assertEqual(feature.shape,(4,channels,513,1))
            self.assertTrue(np.isfinite(feature).all())
            self.assertFalse(silent.any())


if __name__=='__main__':unittest.main()

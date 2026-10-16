import sys
import tempfile
from pathlib import Path
import unittest
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).absolute().parent.parent/'tools'))
import detect_file as d


class FileInputTests(unittest.TestCase):
    def test_pcm_48000_stereo_preserves_channels_and_grid(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            x = np.zeros((48001, 2), dtype='<i2')
            x[12000, 0] = 16000
            x[24000, 1] = -8000
            source = root/'input.wav'
            with wave.open(str(source), 'wb') as stream:
                stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(48000)
                stream.writeframes(x.tobytes())
            before = d.sha(source)
            out = root/'out'; out.mkdir()
            y, info = d.decode(source, out)
            self.assertEqual(info['original_frames'], 48001)
            self.assertEqual(y.shape[1], 2)
            self.assertLessEqual(abs(len(y)/22050-48001/48000), 1/22050)
            for channel, second in [(0, .25), (1, .5)]:
                self.assertLessEqual(abs(np.argmax(np.abs(y[:,channel]))/22050-second), 1/22050)
            self.assertEqual(before, d.sha(source))

    def test_no_audio_rejected_before_inference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); out = root/'out'; out.mkdir()
            source = root/'silent-video.mp4'
            d.run([str(d.FFMPEG), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                   'color=size=16x16:duration=0.1', '-an', str(source)], root/'generate.log')
            with self.assertRaisesRegex(ValueError, 'no audio stream'):
                d.decode(source, out)
            self.assertFalse((out/'decoded.wav').exists())

    def test_22050_no_resampling_and_short_silent_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); out = root/'out'; out.mkdir()
            source = root/'short.wav'
            with wave.open(str(source), 'wb') as stream:
                stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(22050)
                stream.writeframes(b'\x00\x00'*1023)
            y, info = d.decode(source, out)
            self.assertEqual(y.shape, (1023,1))
            self.assertTrue(np.all(y == 0))
            self.assertEqual(len(info['commands']), 1)
            self.assertEqual(len(d.c.starts_for_samples(len(y))), 0)

    def test_nonzero_media_pts_is_recorded_not_used_as_utc(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); out = root/'out'; out.mkdir()
            source = root/'offset.mov'
            d.run([str(d.FFMPEG), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                   'sine=frequency=1000:sample_rate=48000:duration=0.5', '-c:a',
                   'pcm_s16le', '-output_ts_offset', '2', str(source)], root/'generate.log')
            y, info = d.decode(source, out)
            self.assertAlmostEqual(info['first_decoded_pts_seconds'], 2.)
            self.assertAlmostEqual(info['duration_seconds'], .5)
            self.assertEqual(len(y), 11025)


if __name__ == '__main__':
    unittest.main()

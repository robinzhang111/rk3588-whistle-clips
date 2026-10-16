"""Fixed float32 44100 -> 22050 adapter; no normalization, mixing or inference."""
import subprocess
import numpy as np

FILTER='aresample=22050:resampler=swr:osf=flt:filter_size=32:phase_shift=10:linear_interp=1:exact_rational=1:cutoff=0.97:filter_type=kaiser:kaiser_beta=9:dither_method=0:async=0'


def resample_array(audio, ffmpeg):
    command=[str(ffmpeg),'-nostdin','-v','error','-xerror','-f','f32le',
             '-ar','44100','-ac',str(audio.shape[1]),'-i','pipe:0',
             '-af',FILTER,'-c:a','pcm_f32le','-f','f32le','pipe:1']
    result=subprocess.run(command,input=np.asarray(audio,dtype='<f4').tobytes(),
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode:raise RuntimeError(result.stderr.decode('utf-8',errors='replace'))
    return np.frombuffer(result.stdout,dtype='<f4').reshape(-1,audio.shape[1]).copy()


def target_boundary(source_sample, target_frames):
    # Nearest target sample, half upward, with EOF clamped to actual output.
    return min(target_frames,(source_sample+1)//2)


def source_boundary(target_sample, source_frames):
    return min(source_frames,2*target_sample)

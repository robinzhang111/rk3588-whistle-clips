"""Explicit diagnostic whole-file reference, reusing existing FFT/v1.

This validation tool loads an existing 22050Hz float32 WAV; the normal chunk
entry never calls it or loads the whole audio. Only scores/events are saved.
"""
import argparse
from pathlib import Path
import sys

import numpy as np
import detect_file as d


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--model',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--backend',choices=['npu','onnx-reference'],default='npu')
    parser.add_argument('--dependencies',type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    if args.dependencies:
        sys.path.insert(0,str(args.dependencies.absolute()))
    from whistle_predictor import NpuPredictor,OnnxReference
    protected={str(p):d.sha(p) for p in [args.input,args.model]}
    audio,rate=d.c.read_float_wav(args.input)
    if rate!=22050:
        raise ValueError('Reference requires existing 22050Hz float32 WAV')
    predictor=NpuPredictor(args.model) if args.backend=='npu' else OnnxReference(args.model)
    try:
        starts=d.c.starts_for_samples(len(audio))
        scores=np.zeros((len(starts),audio.shape[1]),dtype=np.float32)
        for first in range(0,len(starts),256):
            features,silent,_=d.c.features_for_starts(audio,starts[first:first+256])
            for i in range(len(features)):
                for channel in range(audio.shape[1]):
                    if not silent[i,channel]:
                        scores[first+i,channel]=predictor(features[i,channel])
        if not np.isfinite(scores).all() or not ((scores>=0)&(scores<=1)).all():
            raise ValueError('Invalid whole reference scores')
        events=d.c.candidate_events(scores,starts,len(audio),rate)
        np.save(str(args.output/'scores.npy'),scores,allow_pickle=False)
        d.write(args.output/'events.json',dict(events=events))
        if any(d.sha(p)!=h for p,h in protected.items()):
            raise ValueError('Protected original/model changed')
        d.write(args.output/'summary.json',dict(status='completed',diagnostic_whole_audio_in_memory=True,
            raw_audio_saved=False,features_saved=False,backend=args.backend,protected_sha256=protected,
            samples=len(audio),channels=audio.shape[1],windows=len(starts),candidates=len(events)))
        print('Whole reference:',len(starts),'windows,',len(events),'candidates')
    finally:
        predictor.close()


if __name__=='__main__':
    main()

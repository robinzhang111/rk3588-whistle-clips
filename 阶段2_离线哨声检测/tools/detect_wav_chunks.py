"""WAV chunk simulation: bounded audio, default NPU, relative candidates only."""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import detect_file as d
from basketball_audio_adapter import FILTER
from stream_whistle import ChunkDetector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--model',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--backend',choices=['npu','onnx-reference'],default='npu')
    parser.add_argument('--chunk-frames',type=int,default=512)
    parser.add_argument('--ffmpeg',type=Path,default=d.FFMPEG if os.name=='nt' else Path('/usr/bin/ffmpeg'))
    parser.add_argument('--ffprobe',type=Path,default=d.FFPROBE if os.name=='nt' else Path('/usr/bin/ffprobe'))
    parser.add_argument('--reference-scores',type=Path,help='Explicit diagnostic mode; saves window scores, no audio/features')
    parser.add_argument('--dependencies',type=Path,help='PC ONNX export dependencies; process-local only')
    args = parser.parse_args()
    if not 1<=args.chunk_frames<=4096:
        parser.error('Chunk size must be 1..4096 target frames')
    if args.input.suffix.lower()!='.wav':
        parser.error('This simulation entry accepts WAV only')
    args.output.mkdir(parents=True,exist_ok=False)
    protected = {str(p.absolute()):d.sha(p) for p in [args.input,args.model,args.ffmpeg,args.ffprobe]}
    if args.dependencies:
        sys.path.insert(0,str(args.dependencies.absolute()))
    from whistle_predictor import NpuPredictor,OnnxReference
    metadata = subprocess.run([str(args.ffprobe),'-v','error','-select_streams','a:0',
        '-show_entries','stream=sample_rate,channels,codec_name','-of','json',str(args.input)],
        check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    stream = json.loads(metadata.stdout)['streams'][0]
    rate,channels = int(stream['sample_rate']),int(stream['channels'])
    command = [str(args.ffmpeg),'-nostdin','-v','error','-xerror','-i',str(args.input),'-map','0:a:0','-vn']
    if rate!=22050:
        command += ['-af',FILTER]
    command += ['-c:a','pcm_f32le','-f','f32le','pipe:1']
    report = dict(status='running',backend=args.backend,input_format=stream,decoder_command=command,
        chunk_frames=args.chunk_frames,protected_sha256=protected,
        mode='diagnostic-comparison' if args.reference_scores else 'normal',
        time_basis='relative target samples from first decoded sample',absolute_utc_start=None,
        raw_audio_saved=False,feature_arrays_saved=False,formal_utc_upload=False,
        input_simulation_not_microphone=True,timing_not_realtime_acceptance=True)
    reference = np.load(str(args.reference_scores),mmap_mode='r',allow_pickle=False) if args.reference_scores else None
    if reference is not None and (reference.ndim!=2 or reference.shape[1]!=channels):
        raise ValueError('Reference channel shape mismatch')
    score_log = (args.output/'window-scores.csv').open('x',encoding='utf-8',newline='') if reference is not None else None
    writer = csv.writer(score_log) if score_log else None
    if writer:
        writer.writerow(['window']+['C%d'%i for i in range(channels)])
    comparison = dict(max_abs_score_difference=0.,threshold_decisions_changed=0)
    def observe(index,features,scores,silent):
        if index>=len(reference):
            raise ValueError('More windows than reference')
        old = reference[index]
        comparison['max_abs_score_difference'] = max(comparison['max_abs_score_difference'],float(np.max(np.abs(scores-old))))
        comparison['threshold_decisions_changed'] += int(np.count_nonzero((scores.astype(float)>=.65)!=(old.astype(float)>=.65)))
        writer.writerow([index]+scores.tolist())
    predictor = process = None
    start = time.perf_counter()
    try:
        init_start = time.perf_counter()
        predictor = NpuPredictor(args.model) if args.backend=='npu' else OnnxReference(args.model)
        report['initialize_seconds'] = time.perf_counter()-init_start
        if args.backend=='npu':
            report['sdk_version'] = predictor.sdk_version
        with (args.output/'events.jsonl').open('x',encoding='utf-8') as events_file, (args.output/'decoder.log').open('xb') as log:
            expected = None
            def emit(event):
                if expected is not None:
                    if event!=expected[detector.event_count]:
                        raise ValueError('Streaming event differs from whole-score v1 reference')
                events_file.write(json.dumps(dict(event, event_id=event['event_index']+1,
                    event_type='whistle_candidate',review_status='pending'),ensure_ascii=False)+'\n')
                events_file.flush()
            detector = ChunkDetector(channels,predictor,emit,observe if reference is not None else None)
            process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=log)
            while True:
                raw = process.stdout.read(args.chunk_frames*channels*4)
                if not raw:
                    break
                if len(raw)%(channels*4):
                    raise ValueError('Incomplete decoded frame')
                detector.feed(np.frombuffer(raw,dtype='<f4').reshape(-1,channels))
            if process.wait()!=0:
                raise RuntimeError('WAV decode/resample failed; see decoder.log')
            # Diagnostic reference stores scores only; normal mode keeps no window history.
            if reference is not None:
                if detector.windows!=len(reference):
                    raise ValueError('Reference window count differs')
                expected = d.c.candidate_events(reference,d.c.starts_for_samples(detector.samples),detector.samples,22050)
                # Already-emitted events were streamed to disk, never retained in the detector.
                events_file.flush()
                with (args.output/'events.jsonl').open(encoding='utf-8') as saved:
                    for index,line in enumerate(saved):
                        event = json.loads(line)
                        for key,value in expected[index].items():
                            if event[key]!=value:
                                raise ValueError('Event field differs: '+key)
            report['buffer'] = detector.finish()
            if reference is not None:
                comparison.update(event_fields_equal=True,reference_candidates=len(expected))
                if detector.event_count!=len(expected) or comparison['threshold_decisions_changed']:
                    raise ValueError('Window decisions or candidate count differs')
                report['comparison'] = comparison
        after = {p:d.sha(p) for p in protected}
        if after!=protected:
            raise ValueError('Protected input/model/tool changed')
        report.update(status='completed',work_seconds=time.perf_counter()-start,protected_unchanged=True)
    except Exception as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        if process is not None:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                process.wait()
        if predictor is not None:
            predictor.close()
        if score_log is not None:
            score_log.close()
        d.write(args.output/'summary.json',report)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()

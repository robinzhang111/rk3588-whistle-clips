"""Build separate editable pages for completed detect_file runs; no inference."""
import argparse
import json
import os
from pathlib import Path
import numpy as np
from detect_file import sha, write
from continuous_whistle import read_float_wav

stage=Path(__file__).absolute().parent.parent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    run_dir=Path(args.run).absolute();out=Path(args.output).absolute()
    if out.exists():raise FileExistsError('Use a new output directory')
    plan=json.loads((run_dir/'run.json').read_text(encoding='utf-8'))
    container=json.loads((run_dir/'events.json').read_text(encoding='utf-8'))
    summary=json.loads((run_dir/'summary.json').read_text(encoding='utf-8'))
    assert plan['threshold']==.65 and plan['rule_version']=='v1' and summary['status']=='completed'
    source=Path(plan['input_path']);adaptation=summary['adaptation']
    assert sha(source)==container['input_sha256'] and sha(adaptation['adapted_path'])==adaptation['adapted_sha256']
    protected={str(p):sha(p) for p in run_dir.iterdir() if p.is_file()}
    protected.update(plan['protected_sha256'])
    assert all(sha(p)==h for p,h in protected.items())
    out.mkdir(parents=True,exist_ok=False)
    audio,rate=read_float_wav(run_dir/'decoded.wav')
    assert rate==adaptation['original_rate'] and len(audio)==adaptation['original_frames']
    step=max(1,int(np.ceil(len(audio)/12000)))
    binding=dict(input_path=str(source),input_sha256=container['input_sha256'],run_path=str(run_dir),
        run_sha256=sha(run_dir/'run.json'),original_events_path=str(run_dir/'events.json'),original_events_sha256=sha(run_dir/'events.json'),
        summary_sha256=sha(run_dir/'summary.json'),decoded_audio_sha256=sha(run_dir/'decoded.wav'),
        adapted_audio_sha256=adaptation['adapted_sha256'],model_sha256=plan['model']['sha256'],
        threshold=.65,rule_version='v1',time_basis='original decoded audio sample grid, half-open [start,end); file relative seconds; no UTC',
        rate=rate,frames=len(audio),channels=audio.shape[1])
    material=dict(schema='pc-correction-material-v1',binding=binding,predictions=container['events'],
        duration=len(audio)/rate,first_decoded_pts_seconds=adaptation['first_decoded_pts_seconds'],playback_gain=summary['playback_gain'],
        playback=[os.path.relpath(run_dir/('listen-C%d.wav'%c),out).replace('\\','/') for c in range(audio.shape[1])],
        envelope_step=step,envelopes=[[[float(audio[i:i+step,c].min()),float(audio[i:i+step,c].max())] for i in range(0,len(audio),step)] for c in range(audio.shape[1])])
    write(out/'material.json',material)
    core=(stage/'tools/pc_corrections.js').read_text(encoding='utf-8')
    template=(stage/'tools/pc_correction_review.html').read_text(encoding='utf-8')
    page=template.replace('__CORE__',core).replace('__MATERIAL__',json.dumps(material,ensure_ascii=False).replace('</','<\\/'))
    (out/'review.html').write_text(page,encoding='utf-8')
    assert all(sha(p)==h for p,h in protected.items())
    write(out/'generation-verification.json',dict(protected_sha256=protected,all_protected_bytes_unchanged=True,
        original_prediction_count=len(container['events']),original_sample_grid_used=True,
        preview_reused_without_extra_gain=True,no_model_execution=True,browser_playback_pending=True))
    print(str(out/'review.html'))


if __name__=='__main__':main()

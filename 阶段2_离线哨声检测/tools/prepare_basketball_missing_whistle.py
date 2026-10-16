# -*- coding: utf-8 -*-
"""Reuse the blank boundary UI for one manually confirmed omitted B01 blow."""
import ast
import json
from pathlib import Path
import numpy as np
from continuous_whistle import read_float_wav
from evaluate_testbench_events import read,write,sha

stage=Path(__file__).absolute().parent.parent
out=stage/'outputs/basketball-failure-review-20261012-01'
page=out/'mark-missing-FP01.html'
assert not page.exists()
material=read(stage/'outputs/basketball-manual-20261012-01/material.json')
f=next(f for f in material['files'] if f['id']=='B01')
audio,rate=read_float_wav(f['audio_path'])
waveform=[[float(chunk.min()),float(chunk.max())] for chunk in np.array_split(audio[:,0],1200)]
data=dict(filename=f['audio_filename'],sha256=f['audio_sha256'],rate=rate,start_sample=0,end_sample=len(audio),waveforms=[waveform])
# Read only the literal template, never execute the old generator's top-level code.
tree=ast.parse((stage/'tools/prepare_single_blow_marking.py').read_text(encoding='utf-8'))
assignment=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='template' for t in n.targets))
template=ast.literal_eval(assignment.value)
template=template.replace('FP01-02_manual_interval.json','basketball_FP01_manual_interval.json')
template=template.replace('audio/FP01-02_focus-C0.wav','../basketball-manual-20261012-01/audio/B01-C0.wav')
template=template.replace("'audio/FP01-02_focus-C'+channel+'.wav'","'../basketball-manual-20261012-01/audio/B01-C0.wav'")
template=template.replace('<option value="1">C1</option><option value="2">C2</option><option value="3">C3</option>','')
template=template.replace('min="178.4" max="179.6"','min="0" max="6.43"')
template=template.replace('片段0.000秒 / 原录音178.400秒','片段0.000秒 / 原音轨0.000秒')
template=template.replace('原录音范围178.400～179.600秒；切换声道只用于核查同一次吹哨。','这里是B01完整音轨0～6.430秒，使用已有统一系数试听副本。只标约第3秒之后新增的一声；2.274～2.397秒的第一声已有标注，不要重复添加。')
template=template.replace('manual-single-blow-correction-v1','basketball-missing-whistle-interval-v1')
truth_path=stage/'outputs/basketball-manual-review-20261012-03/manual_annotations_corrected.json'
template=template.replace("start_sample:start,end_sample:end,user_start_seconds:a,user_end_seconds:b,reference_channel:channel,",
    "file_id:'B01',source_annotation_sha256:'"+sha(truth_path)+"',start_sample:start,end_sample:end,user_start_seconds:a,user_end_seconds:b,reference_channel:channel,")
page.write_text(template.replace('__DATA__',json.dumps(data,ensure_ascii=False)),encoding='utf-8')
write(out/'missing-FP01-material.json',dict(file_id='B01',original_audio_sha256=f['audio_sha256'],
    source_annotation_sha256=sha(truth_path),whole_audio_range=[0,len(audio)/rate],
    playback_gain=f['playback_gain'],initial_start_end_blank=True,no_model_scores_displayed=True,
    known_existing_interval=[2.274489795918367,2.3973015873015875],new_blow_boundary_pending=True))
protected=read(out/'verification.json')['protected_sha256']
assert all(sha(Path(p))==h for p,h in protected.items())
print('B01 blank manual boundary page ready; old truth unchanged.')

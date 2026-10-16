# -*- coding: utf-8 -*-
"""Strict target auditions avoid accidental extra context in short FP checks."""
from pathlib import Path
import wave
import numpy as np
from continuous_whistle import read_float_wav
from evaluate_testbench_events import read,write,sha

stage=Path(__file__).absolute().parent.parent
out=stage/'outputs/basketball-failure-review-20261012-01'
data=read(out/'cases-corrected.json')
material=read(stage/'outputs/basketball-manual-20261012-01/material.json')
records=[]
page=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>FP01／FP04精确目标核查</title><style>body{max-width:900px;margin:30px auto;font:19px sans-serif;line-height:1.8}audio{width:100%}</style><h1>只检查目标处的声音</h1><p>第一播放器只播放模型候选区间，不额外添加0.1秒上下文，声音可能在片段边界被切断；这不代表原录音截断。第二播放器供比较前后完整声音。此次不更改真值。</p>']
for cid in ['FP01','FP04']:
    r=next(r for r in data['cases'] if r['id']==cid)
    f=next(f for f in material['files'] if f['id']==r['file_id'])
    audio,rate=read_float_wav(f['audio_path'])
    a=r['prediction']['estimated_start_sample'];b=r['prediction']['estimated_end_sample']
    pcm=np.round(audio[a:b,0].astype(float)*r['playback_gain']*32767).astype('<i2')
    target=out/'audio'/(cid+'-strict-C0.wav')
    assert not target.exists()
    with wave.open(str(target),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(pcm.tobytes())
    with wave.open(str(target),'rb') as w:assert w.getnframes()==b-a and w.readframes(b-a)==pcm.tobytes()
    records.append(dict(id=cid,file_id=r['file_id'],start_sample=a,end_sample=b,original_seconds=[a/rate,b/rate],source_audio_sha256=sha(Path(f['audio_path'])),source_quantization_verified=True))
    explanation='请比较：目标处是否为另一声独立吹哨，还是前一声延续/回声；已标前一声约2.274～2.397秒。' if cid=='FP01' else '后面约3.701～3.878秒已有一声标注。此前目标按钮多放的0.1秒会包含它的一部分，请只听第一播放器判断3.529～3.646秒本身。'
    page.append(f'<h2>{cid} · {r["file_id"]}</h2><p>{explanation}</p><p>严格目标 {a/rate:.6f}～{b/rate:.6f}秒</p><audio controls src="audio/{target.name}"></audio><p>完整上下文用于比较，不用于直接增加目标声数：</p><audio controls src="{r["audio"][0]}"></audio>')
(out/'strict-check.html').write_text('\n'.join(page)+'</html>',encoding='utf-8')
write(out/'strict-check-material.json',dict(clips=records,no_new_inference=True,annotations_unchanged=True,actual_listening_pending=True))
protected=read(out/'verification.json')['protected_sha256']
assert all(sha(Path(p))==h for p,h in protected.items())
print('Two strict clips prepared; all original evidence unchanged.')

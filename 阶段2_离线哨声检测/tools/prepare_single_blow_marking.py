# -*- coding: utf-8 -*-
"""Make a focused manual boundary page with no detector scores or prefilled labels."""
import hashlib
import json
from pathlib import Path
import wave
import numpy as np

STAGE=Path(__file__).absolute().parent.parent
out=STAGE/'outputs/testbench-failure-review-20261011-01'
page=out/'mark-FP01-02.html'
assert not page.exists()
waveforms=[]
for c in range(4):
    with wave.open(str(out/'audio'/('FP01-02_focus-C'+str(c)+'.wav')),'rb') as w:
        assert w.getframerate()==22050 and w.getnframes()==26460
        audio=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(float)/32767
    waveforms.append([[float(x.min()),float(x.max())] for x in np.array_split(audio,1200)])
source=STAGE.parent/'data/Robcup/TestBench/Testing_20221104_091937_2.wav'
data=dict(filename=source.name,sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
          rate=22050,start_sample=3933720,end_sample=3960180,waveforms=waveforms)
template='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>标记这一次吹哨</title>
<style>body{max-width:1050px;margin:30px auto;padding:0 20px;font:18px sans-serif;line-height:1.7}button,select,input{font-size:18px;margin:7px;padding:8px}canvas{width:100%;height:260px;border:1px solid #aaa;cursor:crosshair}audio{width:100%}#position{font-size:22px;font-weight:bold}label{display:inline-block}</style>
<h1>只标记这一次吹哨</h1><p>先播放，再在波形上点击哨声开始处，按“设为开始”；点击结束处，按“设为结束”。试听所选区间，确认包含完整一声后导出。无需手算时间。边界是人工近似估计，小数显示不代表毫秒精度。</p>
<p>此页不显示模型分数，不预填起止。原录音范围178.400～179.600秒；切换声道只用于核查同一次吹哨。</p>
<label>声道 <select id="channel"><option value="0">C0</option><option value="1">C1</option><option value="2">C2</option><option value="3">C3</option></select></label>
<audio id="audio" controls preload="metadata" src="audio/FP01-02_focus-C0.wav"></audio>
<p id="position">当前位置：片段0.000秒 / 原录音178.400秒</p>
<canvas id="wave" width="1200" height="280" aria-label="波形，点击定位"></canvas>
<p>波形横轴为原录音时间。蓝线为当前位置；绿色为开始，红色为结束。纵轴按本声道显示缩放，播放音量不变。</p>
<button id="back">后退0.01秒</button><button id="forward">前进0.01秒</button>
<button id="setStart">设为开始</button><button id="setEnd">设为结束</button>
<p><label>开始（原录音秒）<input id="start" type="number" min="178.4" max="179.6" step="0.001"></label>
<label>结束（原录音秒）<input id="end" type="number" min="178.4" max="179.6" step="0.001"></label></p>
<button id="preview">试听所选区间</button><button id="export">导出这一声标注</button><p id="message" role="status"></p>
<p>导出文件名：FP01-02_manual_interval.json。请保存后将文件路径发给本聊天；导出不会自动修改原真值或评价。</p>
<script>
const data=__DATA__;
const $=id=>document.getElementById(id),audio=$('audio'),canvas=$('wave'),ctx=canvas.getContext('2d');
let channel=0,stopAt=null,position=0;
const duration=(data.end_sample-data.start_sample)/data.rate,base=data.start_sample/data.rate;
function bounds(){
 if($('start').value===''||$('end').value==='')throw Error('请先设置开始与结束。');
 const a=Number($('start').value),b=Number($('end').value);
 if(!Number.isFinite(a)||!Number.isFinite(b)||!(base<=a&&a<b&&b<=base+duration+1e-9))throw Error('起止必须在片段范围内，且开始早于结束。');
 return [a,b];
}
function draw(){
 ctx.clearRect(0,0,1200,280);ctx.fillStyle='#fafafa';ctx.fillRect(0,0,1200,280);
 const wave=data.waveforms[channel],peak=Math.max(...wave.flat().map(Math.abs),1e-6);
 ctx.strokeStyle='#3569a8';ctx.beginPath();wave.forEach((v,i)=>{ctx.moveTo(i,125-100*v[0]/peak);ctx.lineTo(i,125-100*v[1]/peak)});ctx.stroke();
 for(let j=0;j<=6;j++){const x=1200*j/6;ctx.fillStyle='#333';ctx.fillText((base+duration*j/6).toFixed(3)+'s',Math.min(x,1135),267)}
 for(const [value,color] of [[base+position,'#0066cc'],[$('start').value,'#008844'],[$('end').value,'#cc2222']]){
  if(value==='')continue;const x=(Number(value)-base)/duration*1200;ctx.strokeStyle=color;ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,245);ctx.stroke();
 }
 $('position').textContent='当前位置：片段'+position.toFixed(3)+'秒 / 原录音'+(base+position).toFixed(3)+'秒';
}
function seek(t){audio.pause();stopAt=null;position=Math.max(0,Math.min(duration,t));audio.currentTime=position;draw()}
canvas.onclick=e=>seek((e.clientX-canvas.getBoundingClientRect().left)/canvas.getBoundingClientRect().width*duration);
$('back').onclick=()=>seek(position-.01);$('forward').onclick=()=>seek(position+.01);
$('setStart').onclick=()=>{$('start').value=(base+position).toFixed(3);draw()};
$('setEnd').onclick=()=>{$('end').value=(base+position).toFixed(3);draw()};
$('start').oninput=draw;$('end').oninput=draw;
$('channel').onchange=()=>{audio.pause();stopAt=null;channel=Number($('channel').value);const keep=position;audio.onloadedmetadata=()=>{audio.currentTime=keep;draw()};audio.src='audio/FP01-02_focus-C'+channel+'.wav';draw()};
audio.ontimeupdate=()=>{position=audio.currentTime;if(stopAt!==null&&position>=stopAt){audio.pause();stopAt=null}draw()};
$('preview').onclick=()=>{try{const [a,b]=bounds();audio.currentTime=a-base;position=a-base;stopAt=b-base;audio.play().catch(e=>$('message').textContent=e.message)}catch(e){$('message').textContent=e.message}};
$('export').onclick=()=>{try{
 const [a,b]=bounds();const start=Math.round(a*data.rate),end=Math.round(b*data.rate);
 if(start>=end)throw Error('区间太短。');
 const value={schema:'manual-single-blow-correction-v1',filename:data.filename,source_sha256:data.sha256,sample_rate:data.rate,
  start_sample:start,end_sample:end,user_start_seconds:a,user_end_seconds:b,reference_channel:channel,
  kind:'whistle',source_identity:'unverified',boundary_method:'human_audio_waveform_estimate',exported_utc:new Date().toISOString()};
 const blob=new Blob([JSON.stringify(value,null,2)+'\\n'],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');
 link.href=url;link.download='FP01-02_manual_interval.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 $('message').textContent='已请求下载；请将保存路径发给本聊天。';
 }catch(e){$('message').textContent=e.message}};
draw();
</script></html>'''
page.write_text(template.replace('__DATA__',json.dumps(data)),encoding='utf-8')
(out/'single-boundary-page-data.json').write_text(json.dumps(data)+'\n',encoding='utf-8')
print('Focused manual marking page generated; bounds are blank.')

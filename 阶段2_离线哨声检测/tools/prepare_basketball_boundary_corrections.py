# -*- coding: utf-8 -*-
"""Focused correction of two user-identified annotation boundaries, no scores."""
from pathlib import Path
import json
from evaluate_testbench_events import read,write,sha

stage=Path(__file__).absolute().parent.parent
out=stage/'outputs/basketball-failure-review-20261012-01'
page=out/'correct-boundaries.html'
assert not page.exists()
truth_path=stage/'outputs/basketball-manual-review-20261012-02/manual_annotations_coverage_corrected.json'
cases=read(out/'cases.json')['cases']
selected=[r for key in ['FN01','L04'] for r in cases if r['id']==key]
data=dict(source_annotation_sha256=sha(truth_path),cases=[dict(
    id=r['id'],file_id=r['file_id'],label=r['label'],rate=r['rate'],
    clip_start_seconds=r['clip_start_seconds'],clip_end_seconds=r['clip_end_seconds'],
    audio=r['audio'],envelope_step=r['envelope_step'],envelopes=r['envelopes']) for r in selected])
template=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>只更正两条人工边界</title>
<style>body{max-width:1050px;margin:25px auto;font:18px system-ui;padding:0 15px;line-height:1.7}button,input,select{font:inherit;margin:7px;padding:7px}canvas{width:100%;height:240px;border:1px solid #999}audio{width:100%}</style>
<h1>只更正两条人工边界</h1><p>FN01：把哨声结束点设在计时器声之前。L04：把第一声开始点移到真实起音处，不标第二声。当前输入框是旧人工边界，供修改，不是模型推荐。这里只显示原声道波形，不显示分数。</p>
<select id="case"><option value="0">FN01 · B04 · 更正结束</option><option value="1">L04 · B05第一声 · 更正开始</option></select>
<audio id="audio" controls preload="metadata"></audio><p id="position"></p><canvas id="wave" width="1200" height="260"></canvas>
<button id="full">整段</button><button id="zoom">当前位置附近1秒</button><button id="back">后退0.01秒</button><button id="forward">前进0.01秒</button>
<p>横轴为原音轨秒数，绿线开始，红线结束，蓝线播放位置。试听沿用统一系数，音轨与时间不变。</p>
<button id="setStart">设为开始</button><button id="setEnd">设为结束</button>
<label>开始秒<input id="start" type="number" step="0.001"></label><label>结束秒<input id="end" type="number" step="0.001"></label>
<button id="preview">试听所选区间</button><button id="save">确认这条更正</button><p id="message"></p>
<button id="export">导出两条更正</button><p>两个材料都确认后导出basketball_boundary_corrections.json并回复路径。不会自动覆盖原真值；无需追求毫秒精度。FN01若哨声与计时器声重叠、无法确定结束，也可直接回复无法分开，不强行猜时间。</p>
<script>
const data=__DATA__,$=id=>document.getElementById(id),audio=$('audio'),ctx=$('wave').getContext('2d');
let index=0,pos=0,stop=null,view=[0,1],corrections={};
function current(){return data.cases[index]}
function draw(){const r=current(),step=r.envelope_step/r.rate,env=r.envelopes[0];ctx.clearRect(0,0,1200,260);const peak=Math.max(...env.flat().map(Math.abs),1e-6),x=t=>1200*(t-view[0])/(view[1]-view[0]);ctx.strokeStyle='#3569a8';ctx.beginPath();env.forEach((v,i)=>{const p=x(r.clip_start_seconds+i*step);ctx.moveTo(p,120-100*v[0]/peak);ctx.lineTo(p,120-100*v[1]/peak)});ctx.stroke();for(let j=0;j<=5;j++){ctx.fillStyle='#333';ctx.fillText((view[0]+(view[1]-view[0])*j/5).toFixed(3)+'s',Math.min(j*240,1130),255)}for(const[t,c]of[[r.clip_start_seconds+pos,'#0066cc'],[Number($('start').value),'#008844'],[Number($('end').value),'#cc2222']]){ctx.strokeStyle=c;ctx.beginPath();ctx.moveTo(x(t),0);ctx.lineTo(x(t),230);ctx.stroke()}$('position').textContent='原音轨位置 '+(r.clip_start_seconds+pos).toFixed(3)+' 秒'}
function load(){audio.pause();stop=null;pos=0;const r=current(),old=corrections[r.label.id];view=[r.clip_start_seconds,r.clip_end_seconds];$('start').value=(old?old.start_sample/r.rate:r.label.start_sample/r.rate).toFixed(6);$('end').value=(old?old.end_sample/r.rate:r.label.end_sample/r.rate).toFixed(6);audio.onloadedmetadata=()=>{audio.currentTime=0;draw()};audio.src=r.audio[0];$('message').textContent='请核查修改后确认，不用模型区间代填。';draw()}
function seek(t){audio.pause();stop=null;pos=Math.max(0,Math.min(t-current().clip_start_seconds,current().clip_end_seconds-current().clip_start_seconds));audio.currentTime=pos;draw()}
$('case').onchange=()=>{index=Number($('case').value);load()};$('wave').onclick=e=>{const r=e.target.getBoundingClientRect();seek(view[0]+(e.clientX-r.left)/r.width*(view[1]-view[0]))};
$('back').onclick=()=>seek(current().clip_start_seconds+pos-.01);$('forward').onclick=()=>seek(current().clip_start_seconds+pos+.01);
$('full').onclick=()=>{view=[current().clip_start_seconds,current().clip_end_seconds];draw()};$('zoom').onclick=()=>{const r=current(),n=Math.min(1,r.clip_end_seconds-r.clip_start_seconds),a=Math.max(r.clip_start_seconds,Math.min(r.clip_start_seconds+pos-n/2,r.clip_end_seconds-n));view=[a,a+n];draw()};
$('setStart').onclick=()=>{$('start').value=(current().clip_start_seconds+pos).toFixed(6);draw()};$('setEnd').onclick=()=>{$('end').value=(current().clip_start_seconds+pos).toFixed(6);draw()};$('start').oninput=draw;$('end').oninput=draw;
function bounds(){const r=current();if($('start').value===''||$('end').value==='')throw Error('起止不能为空。');const a=Number($('start').value),b=Number($('end').value);if(!Number.isFinite(a)||!Number.isFinite(b)||!(r.clip_start_seconds<=a&&a<b&&b<=r.clip_end_seconds))throw Error('起止顺序或范围不正确。');return[Math.round(a*r.rate),Math.round(b*r.rate)]}
audio.ontimeupdate=()=>{pos=audio.currentTime;if(stop!==null&&pos>=stop){audio.pause();stop=null}draw()};
$('preview').onclick=()=>{try{const[a,b]=bounds();audio.currentTime=a/current().rate-current().clip_start_seconds;pos=audio.currentTime;stop=b/current().rate-current().clip_start_seconds;audio.play().catch(e=>$('message').textContent=e.message)}catch(e){$('message').textContent=e.message}};
$('save').onclick=()=>{try{const[a,b]=bounds(),r=current();corrections[r.label.id]={event_id:r.label.id,file_id:r.file_id,old_start_sample:r.label.start_sample,old_end_sample:r.label.end_sample,start_sample:a,end_sample:b,rate:r.rate,method:'human_rechecked_audio_waveform'};$('message').textContent='这条更正已确认；完成两条后导出。'}catch(e){$('message').textContent=e.message}};
$('export').onclick=()=>{if(Object.keys(corrections).length!==2){$('message').textContent='请分别确认两条更正；不要为导出猜边界。';return}const value={schema:'basketball-boundary-corrections-v1',source_annotation_sha256:data.source_annotation_sha256,exported_utc:new Date().toISOString(),corrections:Object.values(corrections)},url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='basketball_boundary_corrections.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
load();
</script></html>'''
page.write_text(template.replace('__DATA__',json.dumps(data,ensure_ascii=False)),encoding='utf-8')
write(out/'boundary-correction-material.json',data)
print('Two-boundary correction page ready; no original annotations changed.')

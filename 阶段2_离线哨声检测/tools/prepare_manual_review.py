"""Create full-timeline, initially unlabelled local audio annotation materials."""
import argparse
import hashlib
import json
from pathlib import Path
import wave

import numpy as np

from continuous_whistle import read_float_wav


def sha(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


HTML=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>TestBench 人工哨声区间标注</title><style>
body{font:16px system-ui;margin:24px auto;max-width:1150px;padding:0 18px;background:#fafafa;color:#222}button,input,select{font:inherit;margin:5px;padding:7px}canvas{width:100%;height:210px;background:white;border:1px solid #aaa;cursor:crosshair}audio{width:100%}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:7px;text-align:left}.note{color:#555}.status{padding:12px;background:#e8eef9}button{cursor:pointer}#channels label{margin-right:16px}
</style><h1>TestBench 人工哨声区间标注</h1>
<p>每次独立吹哨一条。先听完整分段并检查四个声道，再按波形标记。模型候选默认隐藏，不能直接当真值。重叠边界的声音按起点所属核心段记录一次。</p>
<div class="status" id="status"></div><p><select id="segment"></select><button id="previous">上一段</button><button id="next">下一段</button></p>
<p id="range"></p><p>播放声道 <select id="channel"><option value="0">通道0</option><option value="1">通道1</option><option value="2">通道2</option><option value="3">通道3</option></select>
<label><input type="checkbox" id="showCandidates">显示模型候选（辅助，非真值）</label>
<select id="zoom"><option value="0">完整上下文</option><option value="5">播放位置附近5秒</option><option value="2">播放位置附近2秒</option></select></p>
<audio id="audio" controls preload="metadata"></audio><canvas id="waveform" width="1100" height="210"></canvas>
<p class="note">横轴为原录音内秒数。点击波形定位播放；Shift+点击设置开始，Alt+点击设置结束。蓝色为人工区间，橙色才是模型候选。可缩放后定位或手工填写秒数；像素/读数不代表真实起止已具备毫秒精度。</p>
<p>开始（原录音秒）<input id="start" type="number" step="0.001"><button id="setStart">用当前播放位置</button>
结束<input id="end" type="number" step="0.001"><button id="setEnd">用当前播放位置</button></p>
<p><select id="kind"><option value="whistle">可确认的独立哨声</option><option value="uncertain">不确定声音，待复核</option></select><input id="note" placeholder="备注：截断、回声、来源不明等"><button id="add">添加区间</button><button id="playSelection">播放所填区间</button></p>
<div id="channels"></div><p class="note">只在本声道核心段完整听过、包括未触发区域后勾选。四路都勾选才视为本段复核完成；没有标注也必须勾选，区分无哨声与没检查。相同吹哨跨声道不重复添加；回声不算另一吹哨。不确定区间保留待复核。</p>
<table><thead><tr><th>类别</th><th>开始/结束秒</th><th>参考声道</th><th>备注</th><th></th></tr></thead><tbody id="events"></tbody></table>
<p><button id="export">导出人工标注 JSON</button><label>导入以前导出的标注 <input type="file" id="import" accept=".json"></label></p>
<p>导出后将 JSON 保存到本材料目录，回到聊天说明保存路径。模型、源录音和此前输出不会被修改。浏览器可能不允许持久缓存，离开前务必导出；未完成或有不确定区间时不会被当作最终真值。</p>
<script>const DATA=__DATA__;
const $=id=>document.getElementById(id), key='manual-review-'+DATA.material_id+(location.search.includes('qa=1')?'-qa':'');
let state={schema:'manual-testbench-single-blow-v1',material_id:DATA.material_id,event_unit:'single_audible_whistle_blow',absolute_utc_start:null,source_files:DATA.files.map(f=>({filename:f.filename,sha256:f.sha256,sample_rate:f.rate,sample_frames:f.frames})),segments:DATA.segments.map(s=>({id:s.id,reviewed_channels:[false,false,false,false]})),events:[]};
let selected=0, channel=0,viewStart=0,viewEnd=0,selectionEnd=null,cacheNotice='';
try{const previous=localStorage.getItem(key);if(previous){const p=JSON.parse(previous);if(p.material_id===DATA.material_id)state=p;}}catch(e){cacheNotice='浏览器缓存不可用，请及时导出。';}
function seg(){return DATA.segments[selected]}function file(){return DATA.files[seg().file_index]}
function originalTime(){return seg().clip_start/file().rate+$('audio').currentTime}
function save(){try{localStorage.setItem(key,JSON.stringify(state));}catch(e){cacheNotice='自动缓存失败，请及时导出。';}status()}
function status(){let complete=state.segments.filter(s=>s.reviewed_channels.every(Boolean)).length;let uncertain=state.events.filter(e=>e.kind==='uncertain').length;$('status').textContent=`已复核 ${complete}/${DATA.segments.length} 段；人工哨声 ${state.events.filter(e=>e.kind==='whistle').length} 条；不确定 ${uncertain} 条。未确认起止前不计算性能。 ${cacheNotice}`;}
function updateView(){let s=seg(),f=file(),span=Number($('zoom').value),lo=s.clip_start/f.rate,hi=s.clip_end/f.rate;viewStart=span?Math.max(lo,Math.min(originalTime()-span/2,hi-span)):lo;viewEnd=span?Math.min(hi,viewStart+span):hi;draw()}
function draw(){let s=seg(),f=file(),ctx=$('waveform').getContext('2d'),w=1100,h=210;ctx.clearRect(0,0,w,h);let x=t=>(t-viewStart)/(viewEnd-viewStart)*w;
function band(start,end,color){ctx.fillStyle=color;ctx.fillRect(x(start),12,x(end)-x(start),170)}
if($('showCandidates').checked)f.candidates.forEach(e=>band(e.estimated_start_seconds,e.estimated_end_seconds,'#ffd59c'));
state.events.filter(e=>e.filename===f.filename).forEach(e=>band(e.start_sample/f.rate,e.end_sample/f.rate,e.kind==='whistle'?'#b9d8fa':'#eac1ed'));
ctx.strokeStyle='#3b526e';ctx.beginPath();let env=s.envelopes[channel],peak=Math.max(.01,...env.map(p=>Math.max(Math.abs(p[0]),Math.abs(p[1]))));
env.forEach((p,i)=>{let t=(s.clip_start+i*s.envelope_step)/f.rate;if(t>=viewStart&&t<=viewEnd){ctx.moveTo(x(t),100-75*p[0]/peak);ctx.lineTo(x(t),100-75*p[1]/peak)}});ctx.stroke();
ctx.strokeStyle='#bbb';ctx.beginPath();ctx.moveTo(0,100);ctx.lineTo(w,100);ctx.stroke();ctx.fillStyle='#222';for(let i=0;i<=10;i++){let t=viewStart+(viewEnd-viewStart)*i/10;ctx.fillText(t.toFixed(3),Math.min(w-50,i*w/10),202)}
ctx.strokeStyle='#d22';ctx.beginPath();ctx.moveTo(x(originalTime()),12);ctx.lineTo(x(originalTime()),182);ctx.stroke();}
function table(){let tbody=$('events');tbody.replaceChildren();state.events.filter(e=>e.filename===file().filename&&e.start_sample>=seg().core_start&&e.start_sample<seg().core_end).sort((a,b)=>a.start_sample-b.start_sample).forEach(e=>{let tr=document.createElement('tr');[e.kind,`${(e.start_sample/file().rate).toFixed(3)} ～ ${(e.end_sample/file().rate).toFixed(3)}`,e.reference_channel,e.note].forEach(value=>{let td=document.createElement('td');td.textContent=value;tr.append(td)});let td=document.createElement('td'),button=document.createElement('button');button.textContent='删除后重标';button.onclick=()=>{state.events=state.events.filter(v=>v.id!==e.id);save();table();draw()};td.append(button);tr.append(td);tbody.append(tr)});}
function load(){let s=seg(),f=file();$('segment').value=selected;$('audio').onloadedmetadata=()=>updateView();$('audio').src=s.audio[channel];selectionEnd=null;$('range').textContent=`${f.filename}；核心 ${(s.core_start/f.rate).toFixed(3)}～${(s.core_end/f.rate).toFixed(3)} 秒；上下文 ${(s.clip_start/f.rate).toFixed(3)}～${(s.clip_end/f.rate).toFixed(3)} 秒。只在本段添加起点属于核心范围的声音。`;
$('start').value='';$('end').value='';$('channels').replaceChildren();for(let i=0;i<4;i++){let label=document.createElement('label'),box=document.createElement('input');box.type='checkbox';box.checked=state.segments[selected].reviewed_channels[i];box.onchange=()=>{state.segments[selected].reviewed_channels[i]=box.checked;save()};label.append(box,`通道${i}核心段已复核`);$('channels').append(label)};table();updateView();status();}
DATA.segments.forEach((s,i)=>{let o=document.createElement('option');o.value=i;o.textContent=`${s.id} ｜ ${(s.core_start/DATA.files[s.file_index].rate).toFixed(1)}～${(s.core_end/DATA.files[s.file_index].rate).toFixed(1)}秒`; $('segment').append(o)});
$('segment').onchange=()=>{selected=Number($('segment').value);load()};$('channel').onchange=()=>{let t=$('audio').currentTime;channel=Number($('channel').value);$('audio').src=seg().audio[channel];$('audio').onloadedmetadata=()=>{$('audio').currentTime=Math.min(t,$('audio').duration);updateView()};};
$('previous').onclick=()=>{selected=Math.max(0,selected-1);load()};$('next').onclick=()=>{selected=Math.min(DATA.segments.length-1,selected+1);load()};$('showCandidates').onchange=draw;$('zoom').onchange=updateView;
$('audio').ontimeupdate=()=>{if(selectionEnd!==null&&originalTime()>=selectionEnd){$('audio').pause();selectionEnd=null}updateView()};
$('waveform').onclick=event=>{let box=$('waveform').getBoundingClientRect(),time=viewStart+(event.clientX-box.left)/box.width*(viewEnd-viewStart);if(event.shiftKey)$('start').value=time.toFixed(3);else if(event.altKey)$('end').value=time.toFixed(3);else $('audio').currentTime=Math.max(0,time-seg().clip_start/file().rate);draw()};
$('setStart').onclick=()=>{$('start').value=originalTime().toFixed(3)};$('setEnd').onclick=()=>{$('end').value=originalTime().toFixed(3)};
$('playSelection').onclick=()=>{if(!$('start').value||!$('end').value)return alert('先填起止');let a=Number($('start').value),b=Number($('end').value);if(b<=a)return alert('结束须晚于开始');$('audio').currentTime=Math.max(0,a-seg().clip_start/file().rate);selectionEnd=b;$('audio').play().catch(()=>alert('请使用播放器播放按钮'));};
$('add').onclick=()=>{if(!$('start').value||!$('end').value)return alert('先填起止');let f=file(),a=Math.round(Number($('start').value)*f.rate),b=Math.round(Number($('end').value)*f.rate);if(!Number.isFinite(a)||!Number.isFinite(b)||a<seg().core_start||a>=seg().core_end||b<=a||b>f.frames)return alert('起点须在本段核心范围；结束须晚于开始且不越文件末尾');if(b>seg().clip_end&&!confirm('结束超出本段上下文：请确认已在后续分段试听并核实终点。'))return;state.events.push({id:Date.now()+'-'+state.events.length,filename:f.filename,start_sample:a,end_sample:b,kind:$('kind').value,reference_channel:channel,note:$('note').value,source_identity:'unverified',boundary_method:'human_audio_waveform_estimate'});$('note').value='';save();table();draw()};
$('export').onclick=()=>{let out=JSON.parse(JSON.stringify(state));out.exported_utc=new Date().toISOString();out.complete_review=out.segments.every(s=>s.reviewed_channels.every(Boolean));out.unresolved_intervals=out.events.filter(e=>e.kind==='uncertain').length;let link=document.createElement('a');link.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:'application/json'}));link.download='manual_annotations.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000)};
$('import').onchange=async()=>{try{let p=JSON.parse(await $('import').files[0].text());if(p.material_id!==DATA.material_id||p.schema!==state.schema||JSON.stringify(p.source_files)!==JSON.stringify(state.source_files)||p.segments.length!==DATA.segments.length)throw Error('来源/格式不匹配');state=p;save();load()}catch(e){alert('导入失败：'+e.message)}};
load();</script></html>'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',required=True)
    parser.add_argument('--candidates',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    data,candidates,output=Path(args.data),Path(args.candidates),Path(args.output)
    output.mkdir(parents=True,exist_ok=False)
    (output/'audio').mkdir()
    files,segments=[],[]
    for fi,path in enumerate(sorted((data/'TestBench').glob('*.wav'))):
        before=sha(path)
        audio,rate=read_float_wav(path)
        event_path=candidates/(path.stem+'-events.json')
        events=json.loads(event_path.read_text(encoding='utf-8'))
        files.append(dict(filename=path.name,sha256=before,rate=rate,frames=len(audio),channels=audio.shape[1],
            candidates=events,candidate_file_sha256=sha(event_path)))
        for number,core_start in enumerate(range(0,len(audio),20*rate),1):
            core_end=min(len(audio),core_start+20*rate)
            clip_start=max(0,core_start-rate)
            clip_end=min(len(audio),core_end+rate)
            sid='F%d-S%02d'%(fi+1,number)
            relative=[]
            envelopes=[]
            step=max(1,int(np.ceil((clip_end-clip_start)/4000)))
            for channel in range(audio.shape[1]):
                selected=audio[clip_start:clip_end,channel]
                pcm=np.round(np.clip(selected,-1,1)*32767).astype('<i2')
                target=output/'audio'/('%s-C%d.wav'%(sid,channel))
                with wave.open(str(target),'wb') as stream:
                    stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(rate);stream.writeframes(pcm.tobytes())
                relative.append('audio/'+target.name)
                envelopes.append([[round(float(selected[i:i+step].min()),6),round(float(selected[i:i+step].max()),6)]
                    for i in range(0,len(selected),step)])
            segments.append(dict(id=sid,file_index=fi,core_start=core_start,core_end=core_end,clip_start=clip_start,
                clip_end=clip_end,audio=relative,envelope_step=step,envelopes=envelopes))
        if before!=sha(path):raise ValueError('Original audio changed')
    identity=hashlib.sha256(json.dumps([(f['filename'],f['sha256']) for f in files]).encode()).hexdigest()[:16]
    material=dict(material_id='testbench-manual-v1-'+identity,files=files,segments=segments,
        event_unit='single_audible_whistle_blow',core_seconds=20,context_seconds=1,
        initial_annotations='empty; all channel coverage unchecked; candidates hidden by default',
        no_event_metrics=True,no_training=True,no_board_upload=True)
    raw=json.dumps(material,ensure_ascii=False,separators=(',',':'))
    (output/'material.json').write_text(json.dumps(material,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'review.html').write_text(HTML.replace('__DATA__',raw.replace('</','<\/')),encoding='utf-8')
    print('Prepared',len(segments),'full-coverage core segments,',len(segments)*4,'mono playback files; no labels created')


if __name__=='__main__':
    main()

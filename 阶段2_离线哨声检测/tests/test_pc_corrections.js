// Real run data, software-only edits and mocked DOM; not user listening evidence.
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const C=require('../tools/pc_corrections.js');
const root=process.argv[2],out=path.join(root,'software-checks');fs.mkdirSync(out,{recursive:false});
const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
const m=read(path.join(root,'video-B01/material.json')),wav=read(path.join(root,'wav-TestBench/material.json'));
const fields={start_sample:103900,end_sample:108486,reference_channel:0,source_identity:'unverified',note:'Software-only draft of existing boundary reference',review_status:'pending'};
let s=C.initial(m);
assert.ok(s.events.every(e=>e.review_status==='pending'&&e.source_identity==='unverified'));
s=C.apply(s,m,'modify','pred-1',fields);
s=C.apply(s,m,'delete','pred-2',{note:'Software draft deletion; not listening-confirmed',review_status:'pending'});
let ws=C.apply(C.initial(wav),wav,'add','manual-1',{...fields,start_sample:195539,end_sample:198759,note:'Software draft of existing FN01 reference; not listening-confirmed'});
for(const [name,state,material]of [['video',s,m],['wav',ws,wav]]){
 const exported=C.exportState(state,material);assert.equal(exported.complete_review,false);
 const filename=path.join(out,name+'-draft-roundtrip.json');fs.writeFileSync(filename,JSON.stringify(exported,null,2)+'\n',{flag:'wx'});
 const imported=read(filename);C.validate(imported,material);assert.deepStrictEqual(imported.events,state.events);assert.deepStrictEqual(imported.operations,state.operations);
}
assert.equal(C.inspect(s,m).live_events,1);assert.equal(C.inspect(s,m).deleted,1);assert.equal(C.inspect(ws,wav).live_events,32);
function rejected(change,state=s,material=m){const bad=JSON.parse(JSON.stringify(state));change(bad);assert.throws(()=>C.validate(bad,material))}
rejected(x=>x.binding.input_sha256='wrong');rejected(x=>x.binding.run_sha256='wrong');rejected(x=>x.binding.original_events_sha256='wrong');
rejected(x=>x.events[0].start_sample=-1);rejected(x=>x.events[0].end_sample=m.binding.frames+1);
rejected(x=>x.events[0].reference_channel=99);rejected(x=>x.events[0].review_status='auto-confirmed');
rejected(x=>x.events[0].origin_event_id=2);rejected(x=>x.operations[0].before.end_sample=1);
rejected(x=>x.operations[0].human_confirmed=true);rejected(x=>x.complete_review=true);
assert.throws(()=>C.validate(s,wav));
assert.throws(()=>C.apply(C.initial(m),m,'add','manual-1',{...fields,source_identity:'target_field_referee',review_status:'confirmed',note:''}));
assert.throws(()=>C.apply(C.initial(m),m,'add','manual-1',{...fields,end_sample:fields.start_sample}));
assert.throws(()=>C.apply(s,m,'modify','pred-2',fields));
const confirmed=C.apply(C.initial(m),m,'confirm','pred-1',{review_status:'confirmed',note:'Synthetic unit-test confirmation only'});
assert.equal(confirmed.events[0].source_identity,'unverified');assert.equal(C.exportState(confirmed,m).complete_review,false);
function harness(filename){
 const page=fs.readFileSync(filename,'utf8'),elements={},blobs=[];
 function element(){return {value:'',checked:false,innerHTML:'',textContent:'',currentTime:0,duration:220,className:'',
  pause(){},play(){return Promise.resolve()},appendChild(){},click(){},getBoundingClientRect(){return {left:0,width:1200}},
  getContext(){return {clearRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fillText(){}}}}}
 const context={document:{getElementById(id){return elements[id]||(elements[id]=element())},createElement:element},localStorage:{getItem:()=>null,setItem(){}},Date,console,Blob:class{constructor(parts){this.parts=parts}},URL:{createObjectURL(b){blobs.push(b);return 'blob:test'},revokeObjectURL(){}},setTimeout(){}};
 vm.createContext(context);for(const match of page.matchAll(/<script>([\s\S]*?)<\/script>/g)){new vm.Script(match[1]);vm.runInContext(match[1],context)}
 return {elements,blobs,run:s=>vm.runInContext(s,context)};
}
(async()=>{
 const h=harness(path.join(root,'video-B01/review.html')),e=h.elements;
 assert.equal(e.confirmed.checked,false);
 e.start.value='2.356009070294785';e.end.value='2.460000000';e.note.value='Draft boundary correction';e.save.onclick();
 assert.equal(h.run('state.operations[0].action'),'modify');assert.equal(h.run('state.events[0].end_sample'),108486);
 e.selected.value='pred-2';e.selected.onchange();e.note.value='Draft false positive';e.delete.onclick();assert.equal(h.run('state.events[1].deleted'),true);
 e.export.onclick();const exported=JSON.parse(h.blobs.at(-1).parts.join(''));
 e.import.files=[{text:async()=>JSON.stringify(exported)}];await e.import.onchange();assert.ok(e.message.textContent.includes('校验通过'));
 assert.equal(h.run('state.operations.length'),2);assert.equal(h.run('state.events[0].source_identity'),'unverified');
 e.start.value='2.3';e.confirmed.checked=true;e.start.oninput();assert.equal(e.confirmed.checked,false);
 const wh=harness(path.join(root,'wav-TestBench/review.html')),we=wh.elements;
 we.new.onclick();we.start.value='8.867981859410431';we.end.value='9.014013605442177';we.note.value='Draft FN01';we.save.onclick();
 assert.equal(wh.run('state.operations[0].action'),'add');assert.equal(wh.run('state.events.length'),32);
 we.export.onclick();const wexport=JSON.parse(wh.blobs.at(-1).parts.join(''));
 we.import.files=[{text:async()=>JSON.stringify(wexport)}];await we.import.onchange();assert.ok(we.message.textContent.includes('校验通过'));
 assert.equal(wh.run('state.events.at(-1).start_sample'),195539);
 we.channel.value='3';we.channel.onchange();assert.ok(we.audio.src.endsWith('listen-C3.wav'));
 const message='PASS: source/log/bounds/confirmation rejection tests; delete/modify/add draft export-file-import; page syntax and mocked UI roundtrip. Not real browser playback or human truth.';
 fs.writeFileSync(path.join(out,'test-result.txt'),message+'\n');console.log(message);
})().catch(e=>{console.error(e);process.exitCode=1});

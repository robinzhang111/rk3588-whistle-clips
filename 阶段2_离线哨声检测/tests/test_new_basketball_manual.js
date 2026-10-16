// Mock DOM verification only; no claim of actual browser audio playback.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const page=fs.readFileSync(process.argv[2],'utf8');
const script=page.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
const elements={};
function element(){return {value:'',checked:false,innerHTML:'',textContent:'',currentTime:0,duration:60,
 pause(){},play(){return Promise.resolve()},appendChild(){},click(){},
 getBoundingClientRect(){return{left:0,width:1200}},
 getContext(){return{clearRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fillText(){}}}}}
const sandbox={document:{getElementById(id){return elements[id]||(elements[id]=element())},createElement:element,createTextNode:x=>x},
 localStorage:{getItem:()=>null,setItem(){}},Date,Blob:class{},URL:{createObjectURL:()=>'',revokeObjectURL(){}},setTimeout(){}};
vm.createContext(sandbox);vm.runInContext(script,sandbox);
const run=s=>vm.runInContext(s,sandbox);
assert.equal(run('material.files.length'),17);
assert.equal(run('material.model_predictions_present'),false);
assert.equal(run('state.events.length'),0);
assert.equal(run('exportState().complete_review'),false);
assert.ok(elements.status.textContent.includes('/17'));
assert.equal(run('state.files.every(f=>f.reviewed_channels.every(v=>v===false)&&f.context_reviewed===false)'),true);
for(let i=0;i<17;i++){
 run(`fi=${i};loadFile()`);
 elements.start.value='1';elements.end.value='1.2';
 assert.equal(run('bound()[0]'),run('current().rate'));
 assert.equal(run('bound()[1]'),run('Math.round(current().rate*1.2)'));
 assert.equal(run('review().reviewed_channels.length'),run('current().channels'));
 run('seek(current().duration)');elements.setEnd.onclick();
 assert.equal(run('bound()[1]'),run('current().frames'));
}
run('fi=0;loadFile()');elements.start.value='1';elements.end.value='1.2';
run('$("kind").value="whistle"');elements.identity.value='unverified';elements.note.value='';
elements.add.onclick();assert.equal(run('state.events.length'),1);
elements.identity.value='target_field_referee';elements.add.onclick();assert.equal(run('state.events.length'),1);
assert.throws(()=>run('const invalid=JSON.parse(JSON.stringify(state));invalid.events[0].end_sample=-1;validate(invalid)'));
assert.throws(()=>run('const wrong=JSON.parse(JSON.stringify(state));wrong.material_id="old-material";validate(wrong)'));
run('fi=8;loadFile();channel=1;loadChannel(0)');assert.ok(elements.audio.src.includes('C1.wav'));
run('state.files.forEach(f=>{f.reviewed_channels.fill(true);f.context_reviewed=true})');
assert.equal(run('exportState().complete_review'),true);
assert.ok(page.includes('basketball_new_manual_annotations.json'));
console.log('PASS: syntax; 17 files, mixed-rate positions/EOF, coverage, source binding, channel switching, export and invalid-event checks. Mock DOM only.');

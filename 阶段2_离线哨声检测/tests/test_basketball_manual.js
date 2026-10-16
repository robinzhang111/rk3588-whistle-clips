// Mock DOM checks only: no browser playback or user annotations are produced.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const page=fs.readFileSync(process.argv[2],'utf8');
const script=page.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
const elements={};
function element(){return {value:'',checked:false,innerHTML:'',textContent:'',currentTime:0,duration:20,
 pause(){},play(){return Promise.resolve()},appendChild(){},click(){},
 getBoundingClientRect(){return{left:0,width:1200}},
 getContext(){return{clearRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fillText(){}}}}}
const sandbox={document:{getElementById(id){return elements[id]||(elements[id]=element())},createElement:element,createTextNode:x=>x},
 localStorage:{getItem:()=>null,setItem(){}},console,Date,Blob:class{},URL:{createObjectURL:()=>'',revokeObjectURL(){}},setTimeout(){}};
vm.createContext(sandbox);vm.runInContext(script,sandbox);
const run=s=>vm.runInContext(s,sandbox);
assert.equal(run('state.events.length'),0);
assert.equal(run('exportState().complete_review'),false);
assert.equal(run('state.files.every(f=>f.reviewed_channels.every(v=>v===false)&&f.context_reviewed===false)'),true);
elements.start.value='1';elements.end.value='1.2';
assert.equal(run('bound()[0]'),44100);assert.equal(run('bound()[1]'),52920);
run('$("kind").value="whistle"');elements.identity.value='unverified';elements.note.value='';
elements.add.onclick();assert.equal(run('state.events.length'),1);
elements.identity.value='target_field_referee';elements.add.onclick();assert.equal(run('state.events.length'),1);
assert.throws(()=>run('const invalid=JSON.parse(JSON.stringify(state));invalid.events[0].end_sample=-1;validate(invalid)'));
run('fi=5;loadFile()');assert.equal(run('review().reviewed_channels.length'),2);
run('seek(current().duration)');elements.setEnd.onclick();
elements.start.value='0';assert.equal(run('bound()[1]'),run('current().frames'));
assert.throws(()=>run('const wrong=JSON.parse(JSON.stringify(state));wrong.material_id="wrong";validate(wrong)'));
console.log('PASS: syntax and 9 mock-DOM checks; not real browser playback.');

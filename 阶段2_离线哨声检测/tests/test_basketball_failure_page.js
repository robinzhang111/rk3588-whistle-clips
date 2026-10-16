const fs=require('fs'),vm=require('vm'),assert=require('assert');
const page=fs.readFileSync(process.argv[2],'utf8');
const code=page.match(/<script>([\s\S]*?)<\/script>/)[1];new vm.Script(code);
const elements={};
function node(){return{value:'',textContent:'',currentTime:0,duration:10,innerHTML:'',pause(){},play(){return Promise.resolve()},appendChild(){},click(){},getBoundingClientRect(){return{left:0,width:1200}},getContext(){return{clearRect(){},fillRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fillText(){},setLineDash(){}}}}}
const context={document:{getElementById(id){return elements[id]||(elements[id]=node())},createElement:node},localStorage:{getItem:()=>null,setItem(){}},Blob:class{},URL:{createObjectURL:()=>'',revokeObjectURL(){}},setTimeout(){},Date,console};
vm.createContext(context);vm.runInContext(code,context);
const run=s=>vm.runInContext(s,context);
assert.equal(run('data.cases.length'),21);
assert.equal(run('Object.keys(feedback).length'),0);
run('ci=data.cases.findIndex(r=>r.id==="FN01");load()');
elements.target.onclick();assert.ok(Math.abs(elements.audio.currentTime-.9)<.0001);
run('ci=data.cases.findIndex(r=>r.id==="L04");load()');elements.extension.onclick();
assert.ok(Math.abs(run('stop')-(run('current().prediction.estimated_end_seconds')+.1-run('current().clip_start_seconds')))<1e-9);
run('ci=data.cases.findIndex(r=>r.id==="L05");load()');assert.equal(run('current().channels'),2);
elements.feedback.value='synthetic test only';elements.feedback.oninput();assert.equal(run('feedback.L05'),'synthetic test only');
console.log('PASS: syntax and 6 mock-DOM checks; not browser playback, no user feedback saved.');

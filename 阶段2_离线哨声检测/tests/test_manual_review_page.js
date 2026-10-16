// Code-level DOM fixture checks; not a real browser/audio playback test.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8');
const source=html.split('<script>')[1].split('</script>')[0];
const elements={},alerts=[],urls=new Map();
const context2d=new Proxy({}, {get:()=>()=>{}});
function element(){return {value:'',checked:false,currentTime:0,duration:21,children:[],
 append(...children){this.children.push(...children)},replaceChildren(){this.children=[]},
 getContext(){return context2d},getBoundingClientRect(){return {left:0,width:1100}},
 pause(){},play(){return Promise.resolve()},click(){}}}
for(const id of ['segment','audio','channel','zoom','waveform','status','events','channels','range','start','end','kind','note','previous','next','showCandidates','setStart','setEnd','playSelection','add','export','import'])elements[id]=element();
elements.zoom.value='0';elements.kind.value='whistle';
const sandbox={console,document:{getElementById:id=>elements[id],createElement:element},location:{search:'?qa=1'},
 localStorage:{getItem:()=>null,setItem(){}},alert:text=>alerts.push(text),confirm:()=>true,Blob,
 URL:{createObjectURL:blob=>{urls.set('qa-export',blob);return 'qa-export'},revokeObjectURL(){}},setTimeout(){}};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
assert.strictEqual(vm.runInContext('state.events.length',sandbox),0);
assert.strictEqual(vm.runInContext('state.segments.filter(s=>s.reviewed_channels.some(Boolean)).length',sandbox),0);
assert.strictEqual(elements.showCandidates.checked,false);
elements.start.value='2';elements.end.value='2.4';elements.add.onclick();
assert.strictEqual(vm.runInContext('state.events[0].start_sample',sandbox),44100);
assert.strictEqual(vm.runInContext('state.events[0].end_sample',sandbox),52920);
elements.start.value='22';elements.end.value='22.4';elements.add.onclick();
assert.strictEqual(vm.runInContext('state.events.length',sandbox),1);
assert(alerts.length>0);
elements.start.value='19.8';elements.end.value='21.5';elements.add.onclick();
assert.strictEqual(vm.runInContext('state.events.length',sandbox),2);
elements.audio.currentTime=2;elements.setStart.onclick();assert.strictEqual(elements.start.value,'2.000');
elements.next.onclick();assert.strictEqual(vm.runInContext('selected',sandbox),1);
elements.export.onclick();
urls.get('qa-export').text().then(raw=>{const output=JSON.parse(raw);assert.strictEqual(output.complete_review,false);
 assert.strictEqual(output.unresolved_intervals,0);assert.strictEqual(output.events.length,2);
 console.log('PASS: empty initial state, hidden candidates, sample conversion, core guard, cross-segment end, seek-time entry, navigation, export status');
 console.log('Scope: simulated DOM only; no real browser playback or human annotations created.');});

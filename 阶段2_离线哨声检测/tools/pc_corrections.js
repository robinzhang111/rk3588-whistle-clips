/* Source-bound correction log. No model execution; pending is never truth. */
(function(root){
'use strict';
const clone=x=>JSON.parse(JSON.stringify(x));
const canonical=x=>JSON.stringify(x,(k,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.keys(v).sort().reduce((o,k)=>(o[k]=v[k],o),{}):v);
const same=(a,b)=>canonical(a)===canonical(b);
function fail(message){throw Error(message)}
function validateEvent(e,m){
 const keys=['id','origin_event_id','start_sample','end_sample','reference_channel','deleted','review_status','source_identity','note'];
 if(!e||!same(Object.keys(e).sort(),keys.sort()))fail('事件字段不符合纠错合同');
 if(typeof e.id!=='string'||!e.id)fail('事件ID非法');
 if(e.origin_event_id!==null&&!Number.isInteger(e.origin_event_id))fail('原事件引用非法');
 if(!Number.isInteger(e.start_sample)||!Number.isInteger(e.end_sample)||!(0<=e.start_sample&&e.start_sample<e.end_sample&&e.end_sample<=m.binding.frames))fail('起止越界或顺序错误');
 if(!Number.isInteger(e.reference_channel)||e.reference_channel<0||e.reference_channel>=m.binding.channels)fail('声道非法');
 if(typeof e.deleted!=='boolean'||!['pending','confirmed'].includes(e.review_status))fail('人工确认状态非法');
 if(!['unverified','target_field_referee','neighbour_field','replay_or_added_audio'].includes(e.source_identity)||typeof e.note!=='string')fail('声音来源或备注非法');
 if(e.source_identity!=='unverified'&&(!e.note.trim()||e.review_status!=='confirmed'))fail('身份判断须人工确认并填写依据；否则选未知');
}
function initial(m){return {schema:'pc-whistle-corrections-v1',binding:clone(m.binding),events:m.predictions.map(p=>({id:'pred-'+p.event_id,origin_event_id:p.event_id,start_sample:p.source_start_sample,end_sample:p.source_end_sample,reference_channel:p.audition_channel,deleted:false,review_status:'pending',source_identity:'unverified',note:''})),operations:[]}}
function validate(s,m){
 if(!s||s.schema!=='pc-whistle-corrections-v1'||!same(s.binding,m.binding))fail('输入哈希、运行记录或原预测来源不匹配');
 if(!Array.isArray(s.events)||!Array.isArray(s.operations))fail('纠错记录缺失');
 const replay=initial(m);replay.events.forEach(e=>validateEvent(e,m));
 for(let i=0;i<s.operations.length;i++){
  const op=s.operations[i];
  if(op.sequence!==i+1||!['delete','add','modify','confirm'].includes(op.action)||!Number.isFinite(Date.parse(op.created_utc)))fail('纠错操作序号、类型或时间非法');
  validateEvent(op.after,m);
  if(op.target_id!==op.after.id||op.human_confirmed!==(op.after.review_status==='confirmed'))fail('操作人工确认状态不一致');
  const pos=replay.events.findIndex(e=>e.id===op.target_id);
  if(op.action==='add'){
   if(op.before!==null||pos!==-1||op.after.origin_event_id!==null||op.after.deleted||!op.target_id.startsWith('manual-'))fail('补充事件来源或ID非法');
   replay.events.push(clone(op.after));
  }else{
   if(pos===-1||!same(op.before,replay.events[pos])||op.before.deleted)fail('操作前事件与原引用不一致');
   if(op.after.id!==op.before.id||op.after.origin_event_id!==op.before.origin_event_id)fail('不允许更换原事件引用');
   if(op.action==='delete'){
    const expected={...op.before,deleted:true,review_status:op.after.review_status,note:op.after.note};
    if(!same(expected,op.after))fail('删除操作不能暗改边界或来源');
   }else if(op.after.deleted)fail('修改操作不能暗删事件');
   if(op.action==='confirm'&&(op.after.start_sample!==op.before.start_sample||op.after.end_sample!==op.before.end_sample||op.after.reference_channel!==op.before.reference_channel))fail('确认操作不能暗改起止');
   replay.events[pos]=clone(op.after);
  }
 }
 if(!same(s.events,replay.events))fail('事件列表与纠错日志重放不一致');
 if(new Set(s.events.map(e=>e.id)).size!==s.events.length)fail('重复事件ID');
 const complete=s.events.every(e=>e.review_status==='confirmed');
 if('complete_review' in s&&s.complete_review!==complete)fail('完整人工确认标志与事件不一致');
 return true;
}
function apply(s,m,action,id,fields){
 validate(s,m);const next=clone(s);delete next.complete_review;delete next.exported_utc;
 const before=next.events.find(e=>e.id===id)||null;
 if(action==='add'&&before)fail('新增ID重复');
 if(action!=='add'&&!before)fail('找不到原事件');
 let after=action==='add'?{id,origin_event_id:null,deleted:false,...fields}:{...before,...fields};
 if(action==='delete')after.deleted=true;
 const op={sequence:next.operations.length+1,action,target_id:id,before:clone(before),after:clone(after),human_confirmed:after.review_status==='confirmed',created_utc:new Date().toISOString()};
 next.operations.push(op);
 if(before)next.events[next.events.findIndex(e=>e.id===id)]=after;else next.events.push(after);
 validate(next,m);return next;
}
function exportState(s,m){validate(s,m);return {...clone(s),exported_utc:new Date().toISOString(),complete_review:s.events.every(e=>e.review_status==='confirmed')}}
function inspect(s,m){validate(s,m);const live=s.events.filter(e=>!e.deleted),ordered=[...live].sort((a,b)=>a.start_sample-b.start_sample),overlaps=[];
 for(let i=0;i<ordered.length;i++)for(let j=i+1;j<ordered.length&&ordered[j].start_sample<ordered[i].end_sample;j++)overlaps.push([ordered[i].id,ordered[j].id]);
 return {live_events:live.length,deleted:s.events.filter(e=>e.deleted).length,pending:s.events.filter(e=>e.review_status==='pending').length,confirmed_live:live.filter(e=>e.review_status==='confirmed').length,overlaps};
}
const api={initial,validate,apply,exportState,inspect};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.PCCorrections=api;
})(typeof globalThis!=='undefined'?globalThis:this);

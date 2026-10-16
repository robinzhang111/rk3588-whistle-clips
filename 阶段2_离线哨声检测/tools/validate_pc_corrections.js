// Validate imported corrections and current source hashes; no model inference.
const fs=require('fs'),crypto=require('crypto'),C=require('./pc_corrections.js');
const {isDeepStrictEqual}=require('util');
const [materialPath,correctionPath,outputPath]=process.argv.slice(2);
if(!outputPath)throw Error('Usage: node validate_pc_corrections.js material.json corrections.json NEW-output.json');
if(fs.existsSync(outputPath))throw Error('Output already exists');
const read=p=>JSON.parse(fs.readFileSync(p,'utf8').replace(/^\uFEFF/,''));
const m=read(materialPath),s=read(correctionPath),b=m.binding;
C.validate(s,m);
const run=read(b.run_path+'/run.json'),summary=read(b.run_path+'/summary.json'),original=read(b.original_events_path);
if(!isDeepStrictEqual(original.events,m.predictions)||original.input_sha256!==b.input_sha256||run.input_path!==b.input_path||run.threshold!==.65||run.rule_version!=='v1'||run.model.sha256!==b.model_sha256||summary.adaptation.original_rate!==b.rate||summary.adaptation.original_frames!==b.frames||summary.adaptation.channels!==b.channels)throw Error('Material differs from original run');
const hashes=[[b.input_path,b.input_sha256],[b.run_path+'/run.json',b.run_sha256],[b.original_events_path,b.original_events_sha256],[b.run_path+'/summary.json',b.summary_sha256],[b.run_path+'/decoded.wav',b.decoded_audio_sha256],[summary.adaptation.adapted_path,b.adapted_audio_sha256],[run.model.path,b.model_sha256]];
for(const [p,h]of hashes)if(crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex')!==h)throw Error('Source changed: '+p);
const report={schema_source_bounds_log_confirmation_valid:true,current_source_hashes_match:true,...C.inspect(s,m),complete_review:s.events.every(e=>e.review_status==='confirmed'),scope:'Correction contract only; actual listening and identity require human evidence; no training labels automatically created'};
fs.writeFileSync(outputPath,JSON.stringify(report,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(report));

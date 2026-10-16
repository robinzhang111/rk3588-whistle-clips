# -*- coding: utf-8 -*-
"""Build review materials from frozen audio, annotations and existing scores."""
import csv
from pathlib import Path
import wave
import numpy as np
from continuous_whistle import read_float_wav,starts_for_samples
from evaluate_testbench_events import read,write,sha

STAGE=Path(__file__).absolute().parent.parent


def main():
    source=STAGE/'outputs/basketball-inference-20261012-01'
    out=STAGE/'outputs/basketball-failure-review-20261012-01'
    out.mkdir(exist_ok=False);(out/'audio').mkdir();(out/'windows').mkdir()
    material=read(STAGE/'outputs/basketball-manual-20261012-01/material.json')
    truth_path=STAGE/'outputs/basketball-manual-review-20261012-02/manual_annotations_coverage_corrected.json'
    truth=read(truth_path)
    protected=read(source/'integrity.json')['sha256']
    for path in source.iterdir():
        if path.is_file():protected[str(path)]=sha(path)
    assert all(sha(Path(p))==h for p,h in protected.items())
    write(out/'plan-before-material.json',dict(source_sha256=protected,tool_sha256=sha(Path(__file__)),
        selection='all14 FP,all2 FN,all5 matched events with positive late offset; no result-based exclusions',
        clip_context_seconds=1,playback_gain='same0.400065685 as original manual materials; raw audio unchanged',
        no_inference_retuning_truth_revision=True))
    count={'FP':0,'FN':0,'L':0};records=[];audio_count=0
    for result in read(source/'event-details.json'):
        f=next(f for f in material['files'] if f['id']==result['id'])
        audio,rate=read_float_wav(f['audio_path'])
        scores=np.load(str(source/(f['id']+'-scores.npy')),allow_pickle=False)
        starts=starts_for_samples((len(audio)+1)//2)*2
        assert len(starts)==len(scores)
        events=read(source/(f['id']+'-events.json'))
        labels=[e for e in truth['events'] if e['file_id']==f['id']]
        cases=[('FP',p['prediction'],None) for p in result['false_positives']]
        cases += [('FN',None,e) for e in result['false_negatives']]
        cases += [('L',m['prediction'],m['label']) for m in result['matches'] if m['offset_error_seconds'] is not None and m['offset_error_seconds']>0]
        for kind,prediction,label in cases:
            count[kind]+=1;cid=kind+str(count[kind]).zfill(2)
            a=label['start_sample'] if label else prediction['estimated_start_sample']
            b=label['end_sample'] if label else prediction['estimated_end_sample']
            lo=max(0,min(a,prediction['estimated_start_sample'] if prediction else a)-rate)
            hi=min(len(audio),max(b,prediction['estimated_end_sample'] if prediction else b)+rate)
            selected=np.flatnonzero((starts<hi)&(starts+2048>lo))
            target=(starts<b)&(starts+2048>a)
            playback=[];envelopes=[];step=max(1,int(np.ceil((hi-lo)/6000)))
            for ch in range(f['channels']):
                clip=audio[lo:hi,ch]
                pcm=np.round(clip.astype(float)*f['playback_gain']*32767).astype('<i2')
                assert np.max(np.abs(clip.astype(float)*f['playback_gain']))<=1
                path=out/'audio'/(cid+'-C'+str(ch)+'.wav')
                with wave.open(str(path),'wb') as w:
                    w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(pcm.tobytes())
                with wave.open(str(path),'rb') as w:
                    assert w.getnframes()==hi-lo and w.readframes(hi-lo)==pcm.tobytes()
                playback.append('audio/'+path.name);audio_count+=1
                envelopes.append([[float(clip[i:i+step].min()),float(clip[i:i+step].max())] for i in range(0,len(clip),step)])
            nearby_labels=[e for e in labels if e['start_sample']<hi and e['end_sample']>lo]
            nearby_events=[e for e in events if e['estimated_start_sample']<hi and e['estimated_end_sample']>lo]
            if kind=='FP':analysis='当前真值下未匹配候选；需要听辨目标位置的声音，不能自动称为非哨声或邻场。'
            elif f['id']=='B04':analysis='原人工哨声相交窗只有1阳性窗，未满足连续2窗条件。音色、音量与干扰原因待听辨。'
            elif kind=='FN':analysis='目标位于已有长事件内，未形成第二条事件；需要核查中间间隔与背景持续响应。'
            else:analysis='候选末端比匹配人工末端晚；需要听辨延长部分为回声、下一声还是其他声音，不改原标注。'
            score_rows=[]
            with (out/'windows'/(cid+'.csv')).open('w',encoding='utf-8',newline='') as stream:
                writer=csv.writer(stream)
                writer.writerow(['window_index','original_start_sample','original_end_sample','original_start_seconds','clip_start_seconds']+['C'+str(c) for c in range(f['channels'])]+['max_score','above_0_65','overlaps_target'])
                for i in selected:
                    writer.writerow([int(i),int(starts[i]),min(len(audio),int(starts[i])+2048),int(starts[i])/rate,(int(starts[i])-lo)/rate]+[format(float(v),'.9g') for v in scores[i]]+[float(scores[i].max()),int(float(scores[i].max())>=.65),int(target[i])])
                    score_rows.append([int(starts[i])/rate]+[float(v) for v in scores[i]])
            records.append(dict(id=cid,kind=kind,file_id=f['id'],video_filename=f['video_filename'],
                rate=rate,channels=f['channels'],clip_start_sample=lo,clip_end_sample=hi,
                clip_start_seconds=lo/rate,clip_end_seconds=hi/rate,target_seconds=[a/rate,b/rate],
                target_in_clip_seconds=[(a-lo)/rate,(b-lo)/rate],prediction=prediction,label=label,
                nearby_manual_labels=nearby_labels,nearby_candidates=nearby_events,
                target_score_max=float(scores[target].max()),target_positive_window_count=int((np.max(scores[target].astype(float),axis=1)>=.65).sum()),
                audio=playback,playback_gain=f['playback_gain'],envelope_step=step,envelopes=envelopes,
                score_rows=score_rows,objective_analysis=analysis,human_listening='pending',
                ends_at_original_audio_EOF=hi==len(audio)))
    assert count=={'FP':14,'FN':2,'L':5}
    assert all(sha(Path(p))==h for p,h in protected.items())
    payload=dict(material_id='basketball-failure-review-20261012-01',cases=records,
        source_annotation_sha256=sha(truth_path),scores_reused=True,threshold=.65)
    write(out/'cases.json',payload)
    template=(STAGE/'tools/basketball_failure_review.html').read_text(encoding='utf-8')
    import json
    (out/'review.html').write_text(template.replace('__DATA__',json.dumps(payload,ensure_ascii=False).replace('</','<\/')),encoding='utf-8')
    write(out/'verification.json',dict(protected_sha256=protected,all_inputs_unchanged=True,
        case_counts=count,audio_count=audio_count,all_audio_equal_recorded_gain_source_quantization=True,
        no_extra_clipping=True,no_new_model_scores=True,actual_browser_and_listening_pending=True))
    print([(r['id'],r['file_id'],r['target_seconds']) for r in records]);print('Audio count',audio_count)


if __name__=='__main__':main()

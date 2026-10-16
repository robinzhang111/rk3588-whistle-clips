"""Read frozen event results/scores; prepare auditions without model inference."""
import csv
import hashlib
import html
import json
from pathlib import Path
import wave

import numpy as np
from continuous_whistle import read_float_wav, starts_for_samples, candidate_events

STAGE = Path(__file__).absolute().parent.parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def main():
    evaluation = STAGE/'outputs/testbench-event-evaluation-20261011-01'
    continuous = STAGE/'outputs/naodevils-continuous-20261011-01'
    output = STAGE/'outputs/testbench-failure-review-20261011-01'
    output.mkdir(exist_ok=False)
    (output/'audio').mkdir()
    (output/'windows').mkdir()
    protected = {p: sha(Path(p)) for p in read(evaluation/'integrity.json')['sha256']}
    assert protected == read(evaluation/'integrity.json')['sha256']
    for p in evaluation.glob('*.json'):
        protected[str(p)] = sha(p)
    annotation = read(STAGE/'outputs/testbench-manual-review-20261011-03/manual_annotations_corrected.json')
    records = []
    cards = []
    counts = {'FP': 0, 'FN': 0}
    for file_number, result in enumerate(read(evaluation/'event-details.json'), 1):
        filename = result['filename']
        audio, rate = read_float_wav(Path('data/Robcup/TestBench')/filename)
        score_path = continuous/(Path(filename).stem+'-scores.npy')
        protected[str(score_path)] = sha(score_path)
        scores = np.load(str(score_path), allow_pickle=False)
        starts = starts_for_samples(len(audio))
        events = read(continuous/(Path(filename).stem+'-events.json'))
        assert candidate_events(scores, starts, len(audio), rate) == events
        labels = [e for e in annotation['events'] if e['filename'] == filename]
        cases = [('FP', e['prediction']) for e in result['false_positives']]
        cases += [('FN', e) for e in result['false_negatives']]
        for category, item in cases:
            counts[category] += 1
            case_id = category+str(counts[category]).zfill(2)
            a = item['estimated_start_sample'] if category == 'FP' else item['start_sample']
            b = item['estimated_end_sample'] if category == 'FP' else item['end_sample']
            lo, hi = max(0, a-2*rate), min(len(audio), b+2*rate)
            selected = np.flatnonzero((starts < hi) & (starts+1024 > lo))
            target = (starts < b) & (starts+1024 > a)
            positive = np.max(scores.astype(np.float64), axis=1) >= .65
            runs = []
            for index in np.flatnonzero(positive):
                if runs and index == runs[-1][-1]+1:
                    runs[-1].append(int(index))
                else:
                    runs.append([int(index)])
            overlapping_runs = [r for r in runs if any(target[i] for i in r)]
            nearby_events = [e for e in events if e['estimated_start_sample'] < hi and e['estimated_end_sample'] > lo]
            nearby_labels = [e for e in labels if e['start_sample'] < hi and e['end_sample'] > lo]
            if category == 'FP':
                cause = '现有人工真值下未匹配候选；需要听辨是否为非哨声、人工漏标或响应分裂，不能凭分数认定。'
            elif not overlapping_runs:
                cause = '与人工区间相交的全部窗口四路均未达到0.65；属于该区间内模型未过阈值。声音原因待听辨。'
            elif max(map(len, overlapping_runs)) == 1:
                cause = '人工区间附近只有孤立阳性窗，被至少2个连续阳性窗规则过滤；需听辨确认声音。'
            else:
                cause = '人工区间附近存在至少2窗连续响应，需结合候选起点及邻近吹哨检查合并、偏移或一对一归属。'
            clip_paths = []
            for channel in range(4):
                path = output/'audio'/(case_id+'-C'+str(channel)+'.wav')
                pcm = np.round(np.clip(audio[lo:hi, channel], -1, 1)*32767).astype('<i2')
                with wave.open(str(path), 'wb') as wav:
                    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
                    wav.writeframes(pcm.tobytes())
                with wave.open(str(path), 'rb') as wav:
                    assert wav.getnframes() == hi-lo and wav.readframes(hi-lo) == pcm.tobytes()
                clip_paths.append('audio/'+path.name)
            with (output/'windows'/(case_id+'.csv')).open('w', encoding='utf-8', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['window_index','start_sample','end_sample','original_start_seconds','clip_start_seconds','C0','C1','C2','C3','max_score','positive_at_0_65','overlaps_target'])
                for i in selected:
                    writer.writerow([int(i),int(starts[i]),int(starts[i])+1024,int(starts[i])/rate,(int(starts[i])-lo)/rate]+[format(float(x),'.9g') for x in scores[i]]+[float(max(scores[i])),int(positive[i]),int(target[i])])
            record = dict(id=case_id, category=category, filename=filename, file_number=file_number,
                original_item=item, target_start_seconds=a/rate, target_end_seconds=b/rate,
                clip_start_seconds=lo/rate, clip_end_seconds=hi/rate,
                target_in_clip_seconds=[(a-lo)/rate,(b-lo)/rate], audio=clip_paths,
                channel_peak_scores_on_intersecting_windows=np.max(scores[target],axis=0).tolist(),
                positive_window_count_intersecting_target=int(positive[target].sum()),
                positive_runs_intersecting_target=[dict(first_window=r[0],last_window=r[-1],length=len(r),start_seconds=int(starts[r[0]])/rate,end_seconds=(int(starts[r[-1]])+1024)/rate) for r in overlapping_runs],
                nearby_candidates=nearby_events, nearby_manual_labels=nearby_labels,
                objective_analysis=cause, human_listening='pending',
                audio_scope='4 mono PCM16 source-channel clips, no gain normalization; clipping/quantization only; channel indices do not assert microphone identity')
            records.append(record)
            x = lambda t: 55+820*(t-lo/rate)/((hi-lo)/rate)
            y = lambda s: 180-150*s
            svg = '<svg viewBox="0 0 900 215" role="img" aria-label="四路窗口分数，虚线为0.65"><rect width="900" height="215" fill="white"/>'
            svg += f'<rect x="{x(a/rate):.2f}" y="25" width="{x(b/rate)-x(a/rate):.2f}" height="155" fill="#eee"/><path d="M55 {y(.65)}H875" stroke="#333" stroke-dasharray="6 4"/>'
            for ch,color in enumerate(['#0066cc','#d95f02','#008844','#aa33aa']):
                points=' '.join(f'{x(int(starts[i])/rate):.2f},{y(float(scores[i,ch])):.2f}' for i in selected)
                svg += f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.5"/>'
            for j in range(5):
                t=lo/rate+(hi-lo)/rate*j/4
                svg+=f'<text x="{x(t):.2f}" y="202" font-size="12">{t:.3f}s</text>'
            svg += '<text x="5" y="30">1.0</text><text x="5" y="180">0</text></svg>'
            players=''.join(f'<p>C{ch} <audio controls preload="none" src="{p}"></audio></p>' for ch,p in enumerate(clip_paths))
            cards.append(f'<section id="{case_id}"><h2>{case_id} · 录音{file_number} · {a/rate:.3f}～{b/rate:.3f}s</h2><p>目标在片段{(a-lo)/rate:.3f}～{(b-lo)/rate:.3f}s；可能包含邻近其他吹哨。</p><p>{html.escape(cause)}</p>{players}{svg}<p>C0蓝 / C1橙 / C2绿 / C3紫；横轴原录音秒数，灰区为待核查区间，虚线0.65。曲线横坐标是窗口起点，不是精确声音发生位置。</p><a href="windows/{case_id}.csv">窗口分数表</a></section>')
    assert counts == {'FP':6,'FN':12}
    assert all(sha(Path(p)) == h for p,h in protected.items())
    write(output/'cases.json', records)
    write(output/'verification.json', dict(all_protected_inputs_unchanged=True,sha256=protected,
        reconstructed_candidates_equal_frozen=True, all_72_audio_clips_match_source_quantization=True,
        browser_playback_not_automatically_verified=True, human_listening_pending=True))
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>事件失败核查</title><style>body{max-width:1000px;margin:30px auto;font:17px sans-serif;line-height:1.6}section{border-top:2px solid #ddd;padding:20px 0}audio{width:80%}svg{width:100%}</style><h1>6 FP / 12 FN 核查材料</h1><p>试听各路并反馈哨声次数、目标区间是否有独立哨声、是否有失真/截断或难以判断。现有真值和评价不修改。FP/FN是当前评价身份，不预设声音类型。</p>'
    page += '<nav>'+' · '.join(f'<a href="#{r["id"]}">{r["id"]}</a>' for r in records)+'</nav>'
    (output/'review.html').write_text(page+''.join(cards)+'</html>', encoding='utf-8')
    print(json.dumps([dict(id=r['id'],target=[r['target_start_seconds'],r['target_end_seconds']],peaks=r['channel_peak_scores_on_intersecting_windows'],positive=r['positive_window_count_intersecting_target'],runs=r['positive_runs_intersecting_target'],analysis=r['objective_analysis']) for r in records],ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()

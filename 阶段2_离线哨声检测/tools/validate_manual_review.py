"""Validate user-exported annotations and review coverage; never score detections."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--material',required=True)
    parser.add_argument('--annotations',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    output=Path(args.output)
    if output.exists():raise FileExistsError(output)
    read=lambda p:json.loads(Path(p).read_text(encoding='utf-8-sig'))
    material,annotations=read(args.material),read(args.annotations)
    expected=[dict(filename=f['filename'],sha256=f['sha256'],sample_rate=f['rate'],sample_frames=f['frames']) for f in material['files']]
    if annotations['schema']!='manual-testbench-single-blow-v1' or annotations['material_id']!=material['material_id'] or annotations['source_files']!=expected:
        raise ValueError('Annotation sources or schema do not match materials')
    segments={s['id']:s for s in annotations['segments']}
    if len(segments)!=len(material['segments']) or set(segments)!={s['id'] for s in material['segments']}:
        raise ValueError('Missing or duplicate segment coverage records')
    reviewed=[]
    for s in material['segments']:
        channels=segments[s['id']]['reviewed_channels']
        if len(channels)!=4 or any(type(v) is not bool for v in channels):raise ValueError('Invalid review flags')
        if all(channels):reviewed.append(s['id'])
    files={f['filename']:f for f in material['files']}
    events=annotations['events']
    ids=set()
    for event in events:
        if event['id'] in ids:raise ValueError('Duplicate annotation id')
        ids.add(event['id'])
        f=files[event['filename']]
        a,b=event['start_sample'],event['end_sample']
        if type(a) is not int or type(b) is not int or not 0<=a<b<=f['frames']:raise ValueError('Invalid interval bounds')
        if event['kind'] not in ['whistle','uncertain'] or (event['reference_channel'] is not None and event['reference_channel'] not in range(4)):raise ValueError('Invalid event category/channel')
    overlaps=[]
    for filename in files:
        ordered=sorted([e for e in events if e['filename']==filename],key=lambda e:e['start_sample'])
        for i,event in enumerate(ordered):
            for following in ordered[i+1:]:
                if following['start_sample']>=event['end_sample']:break
                overlaps.append([event['id'],following['id']])
    uncertain=sum(e['kind']=='uncertain' for e in events)
    result=dict(schema_valid=True,reviewed_segments=reviewed,total_segments=len(segments),
        unreviewed_segments=[s['id'] for s in material['segments'] if s['id'] not in reviewed],
        confirmed_whistle_intervals=sum(e['kind']=='whistle' for e in events),uncertain_intervals=uncertain,
        overlapping_pairs_to_review=overlaps,coverage_complete=len(reviewed)==len(segments),
        ready_for_user_final_confirmation=len(reviewed)==len(segments) and uncertain==0 and not overlaps,
        scope='Structure/coverage check only. Human boundary correctness not automatically verified; no event performance computed.')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

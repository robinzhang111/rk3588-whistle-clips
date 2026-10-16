"""Validate manual source/bounds/coverage; no inference or event scoring."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--material',required=True)
    parser.add_argument('--annotations',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    read=lambda p:json.loads(Path(p).read_text(encoding='utf-8-sig'))
    material,a=read(args.material),read(args.annotations)
    output=Path(args.output)
    if output.exists():raise FileExistsError(output)
    expected=[{k:f[k] for k in ['id','video_sha256','audio_sha256','rate','channels','frames']} for f in material['files']]
    if a['schema']!='basketball-single-blow-manual-v1' or a['material_id']!=material['material_id'] or a['source_files']!=expected:
        raise ValueError('Source identity mismatch')
    files={f['id']:f for f in material['files']}
    coverage={r['id']:r for r in a['files']}
    if set(coverage)!=set(files) or len(a['files'])!=len(files):raise ValueError('Missing or duplicate review record')
    reviewed=[]
    for fid,f in files.items():
        r=coverage[fid]
        if len(r['reviewed_channels'])!=f['channels'] or any(type(v) is not bool for v in r['reviewed_channels']) or type(r['context_reviewed']) is not bool:
            raise ValueError('Invalid review flags')
        if all(r['reviewed_channels']) and r['context_reviewed']:reviewed.append(fid)
    ids=set()
    for e in a['events']:
        f=files[e['file_id']]
        if e['id'] in ids:raise ValueError('Duplicate event id')
        ids.add(e['id'])
        if type(e['start_sample']) is not int or type(e['end_sample']) is not int or not 0<=e['start_sample']<e['end_sample']<=f['frames']:
            raise ValueError('Invalid interval')
        if type(e['reference_channel']) is not int or e['reference_channel'] not in range(f['channels']) or e['kind'] not in ['whistle','uncertain']:
            raise ValueError('Invalid kind or channel')
        if e['source_identity'] not in ['unverified','target_field_referee','neighbour_field','replay_or_added_audio']:raise ValueError('Invalid sound identity')
        if e['source_identity'] in ['target_field_referee','neighbour_field'] and not e['note'].strip():raise ValueError('Identity evidence missing')
    overlaps=[]
    for fid in files:
        ordered=sorted([e for e in a['events'] if e['file_id']==fid],key=lambda e:e['start_sample'])
        for i,e in enumerate(ordered):
            for following in ordered[i+1:]:
                if following['start_sample']>=e['end_sample']:break
                overlaps.append([e['id'],following['id']])
    result=dict(schema_sources_bounds_valid=True,reviewed_files=reviewed,
        unreviewed_files=[f for f in files if f not in reviewed],event_count=len(a['events']),
        uncertain_count=sum(e['kind']=='uncertain' for e in a['events']),
        unverified_identity_count=sum(e['source_identity']=='unverified' for e in a['events']),
        overlaps_to_review=overlaps,coverage_complete=len(reviewed)==len(files),
        scope='Structural/coverage check; source identity and human accuracy still need confirmation; no scoring')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

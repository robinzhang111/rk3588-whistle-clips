"""Inventory newly added video sources only; no decoding or model operations."""
from pathlib import Path
import json
import subprocess
from evaluate_testbench_events import read,write,sha

stage=Path(__file__).absolute().parent.parent
root=stage.parent
out=stage/'outputs/basketball-new-video-audit-20261012-01'
out.mkdir(exist_ok=False)
old=read(stage/'outputs/basketball-video-audit-20261012-01/inventory.json')
old_names={r['filename'] for r in old}
probe=root/'tools/ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe'
paths=[p for p in (root/'video').iterdir() if p.is_file() and p.name not in old_names]
paths.sort(key=lambda p:int(p.stem) if p.stem.isdigit() else 1000000)
rows=[]
for p in paths:
    command=[str(probe),'-v','error','-show_entries','format=duration,start_time:stream=index,codec_type,codec_name,sample_rate,channels,start_time,duration','-of','json',str(p)]
    r=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert r.returncode==0
    metadata=json.loads(r.stdout)
    rows.append(dict(filename=p.name,path=str(p),bytes=p.stat().st_size,sha256=sha(p),
        metadata=metadata,probe_command=command,source_match_group='unknown',usage='unassigned'))
groups={}
for r in rows:groups.setdefault(r['sha256'],[]).append(r['filename'])
duplicates=[g for g in groups.values() if len(g)>1]
unique=[];seen=set()
for r in rows:
    if r['sha256'] not in seen:unique.append(r);seen.add(r['sha256'])
checks=[]
for r in old:
    p=Path(r['path'])
    checks.append(dict(filename=r['filename'],exists=p.exists(),hash_matches=sha(p)==r['sha256'] if p.exists() else None,old_basketball=r['include_as_basketball_candidate']))
assert all(r['exists'] and r['hash_matches'] for r in checks if r['old_basketball'])
summary=dict(new_file_count=len(rows),unique_file_count=len(unique),exact_duplicate_groups=duplicates,
    container_seconds_all_files=sum(float(r['metadata']['format']['duration']) for r in rows),
    container_seconds_unique_files=sum(float(r['metadata']['format']['duration']) for r in unique),
    all_new_files_have_audio=all(any(s['codec_type']=='audio' for s in r['metadata']['streams']) for r in rows),
    original_six_basketball_files_unchanged=True,old_inventory_checks=checks,
    scope='Read-only probe and SHA256; no full decoding/extraction/listening/model scoring; same-hash aliases retained, not deleted',
    source_grouping_pending=True)
write(out/'inventory.json',rows);write(out/'summary.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2))
print([(r['filename'],r['metadata']['format']['duration'],[(s.get('sample_rate'),s.get('channels')) for s in r['metadata']['streams'] if s['codec_type']=='audio']) for r in unique])

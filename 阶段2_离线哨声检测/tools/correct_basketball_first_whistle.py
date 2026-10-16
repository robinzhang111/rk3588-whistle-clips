# -*- coding: utf-8 -*-
"""Apply user's correction: B01 has one blow, update it rather than adding one."""
from pathlib import Path
from evaluate_testbench_events import read,write,sha

stage=Path(__file__).absolute().parent.parent
source=Path('C:/Users/YOUR_USER/Downloads/basketball_FP01_manual_interval.json')
base=stage/'outputs/basketball-manual-review-20261012-03/manual_annotations_corrected.json'
out=stage/'outputs/basketball-manual-review-20261012-04'
out.mkdir(exist_ok=False)
(out/'manual_interval_received.json').write_bytes(source.read_bytes())
patch=read(source);truth=read(base)
assert patch['schema']=='basketball-missing-whistle-interval-v1'
assert patch['source_annotation_sha256']==sha(base) and patch['file_id']=='B01' and patch['sample_rate']==44100
material=read(stage/'outputs/basketball-manual-20261012-01/material.json')
f=next(f for f in material['files'] if f['id']=='B01')
assert patch['filename']==f['audio_filename'] and patch['source_sha256']==f['audio_sha256']
assert type(patch['start_sample']) is int and type(patch['end_sample']) is int and 0<=patch['start_sample']<patch['end_sample']<=f['frames']
labels=[e for e in truth['events'] if e['file_id']=='B01'];assert len(labels)==1
event=labels[0];old=event.copy()
event.update(start_sample=patch['start_sample'],end_sample=patch['end_sample'],boundary_method='human_rechecked_audio_waveform')
original=read(base)
assert [e for e in truth['events'] if e['file_id']!='B01']==[e for e in original['events'] if e['file_id']!='B01']
assert len(truth['events'])==7
truth['derived_correction']=dict(source_sha256=sha(base),record='receipt.json',final_truth_confirmation_pending=False)
write(out/'manual_annotations_corrected.json',truth)
write(out/'receipt.json',dict(user_latest_reply='如果重叠的话就只有这一声，没有第二声，更正一下',
    downloaded_patch_sha256=sha(source),source_annotation_sha256=sha(base),
    earlier_second_blow_confirmation_withdrawn=True,patch_interpreted_as_existing_B01_boundary_correction=True,
    added_events=0,total_events=7,old=old,new=event,
    old_seconds=[old['start_sample']/44100,old['end_sample']/44100],
    new_seconds=[event['start_sample']/44100,event['end_sample']/44100],unchanged_other_six_events=True,
    scope='No claim that interval overlap alone proves same sound; same-blow decision is latest user clarification.'))
write(out/'user-final-confirmation.json',dict(user_reply='如果重叠的话就只有这一声，没有第二声，更正一下',
    annotation_sha256=sha(out/'manual_annotations_corrected.json'),scope='seven audible blows, B01 one blow; unknown identities'))
print('B01 existing event corrected, total remains seven; no event added.')

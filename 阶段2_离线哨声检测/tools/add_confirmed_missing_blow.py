# -*- coding: utf-8 -*-
"""Version the user's screenshot-confirmed interval; preserve all earlier truth."""
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

stage=Path(__file__).absolute().parent.parent
old=stage/'outputs/testbench-manual-review-20261011-03/manual_annotations_corrected.json'
out=stage/'outputs/testbench-manual-review-20261011-04'
out.mkdir(exist_ok=False)
old_hash=hashlib.sha256(old.read_bytes()).hexdigest()
assert old_hash=='6e25bd96838025f442074899496d0183a916c0d87fe4bf10b3346357277a6825'
a=json.loads(old.read_text(encoding='utf-8'))
image=Path('C:/Users/YOUR_USER/AppData/Local/Temp/codex-clipboard-42540353-0156-443b-b30f-eb753868309c.png')
(out/'user-confirmed-interval.png').write_bytes(image.read_bytes())
sample=lambda value:int((Decimal(value)*22050).to_integral_value(rounding=ROUND_HALF_UP))
event=dict(id='user-confirmed-missing-F1-178892',filename='Testing_20221104_091937_2.wav',
    start_sample=sample('178.892'),end_sample=sample('179.013'),kind='whistle',reference_channel=None,
    source_identity='unverified',boundary_method='user_confirmed_waveform_screenshot',
    note='用户确认截图输入框178.892～179.013秒；声道选择未出现在截图中，不假定参考声道。')
a['events'].append(event)
a['derived_correction']={'source_sha256':old_hash,'record':'correction.json','user_final_confirmation_pending':False}
write=lambda path,value:path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
write(out/'manual_annotations_corrected.json',a)
write(out/'correction.json',dict(user_reply='这个是确定的时间',source='user-confirmed-interval.png',
    screenshot_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
    user_start_seconds='178.892',user_end_seconds='179.013',added_event=event,
    rounding='decimal nearest original sample, half up; [start,end); manual accuracy not inferred from decimals',
    previous_truth_sha256=old_hash,total_intervals=68,counts_by_file=[34,34],unchanged_previous_67=True,
    reference_channel='unknown; nullable reference_channel, same file cross-channel event evaluation unaffected'))
assert json.loads(old.read_text(encoding='utf-8'))['events']==a['events'][:-1]
print(hashlib.sha256((out/'manual_annotations_corrected.json').read_bytes()).hexdigest())

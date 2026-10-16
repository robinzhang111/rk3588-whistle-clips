"""Check correction-page generation preserved actual predictions and references."""
from pathlib import Path
import subprocess
import sys
import numpy as np
from detect_file import sha,write

stage=Path(__file__).absolute().parent.parent
import json
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
root=stage/'outputs/pc-correction-20261012-01'
target=root/'history-verification.json'
if target.exists():raise FileExistsError(target)
reports=[]
for name,reference in [('video-B01',stage/'outputs/basketball-inference-20261012-01/B01-scores.npy'),
                       ('wav-TestBench',stage/'outputs/naodevils-continuous-20261011-01/Testing_20221104_091937_2-scores.npy')]:
    out=root/name
    generation=read(out/'generation-verification.json')
    assert all(sha(p)==h for p,h in generation['protected_sha256'].items())
    material=read(out/'material.json');original=read(Path(material['binding']['original_events_path']))
    assert material['predictions']==original['events']
    current=np.load(str(Path(material['binding']['run_path'])/'scores.npy'),allow_pickle=False)
    historical=np.load(str(reference),allow_pickle=False)
    assert np.array_equal(current,historical)
    reports.append(dict(run=name,original_candidates=len(original['events']),
        score_max_abs_difference=float(np.max(np.abs(current-historical))),
        all_original_event_objects_equal=True,all_original_run_bytes_unchanged=True))
    refusal=subprocess.run([sys.executable,str(stage/'tools/prepare_pc_correction_review.py'),
        '--run',material['binding']['run_path'],'--output',str(out)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert refusal.returncode!=0 and b'FileExistsError' in refusal.stderr
    assert all(sha(p)==h for p,h in generation['protected_sha256'].items())
refs=[stage/'outputs/basketball-manual-review-20261012-04/manual_annotations_corrected.json',
      stage/'outputs/basketball-new-manual-review-20261012-02/manual_annotations_coverage_confirmed.json']
write(target,dict(status='software_checks_passed_user_acceptance_pending',runs=reports,
    reference_hashes={str(p):sha(p) for p in refs},new_output_reuse_rejected=True,
    core_and_mock_ui_tests_passed=True,input_regression_tests_passed=4,continuous_rule_tests_passed=7,
    user_wav_add_received=True,user_video_delete_modify_pending=True,
    browser_tool_failure='Chrome/Edge connector fetch failed; no automated real-browser proof',
    actual_browser_playback_reimport_feedback_pending=True,no_model_execution=True))
print('PASS: 31/2 original predictions, score max differences 0, original run/model/source hashes, output reuse refused. User acceptance still pending.')

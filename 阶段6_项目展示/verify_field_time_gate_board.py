"""Run synthetic clock/REC regression on the new release; no hardware capture."""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).absolute().parent.parent
OUT=ROOT/'阶段6_项目展示/outputs/field-preparation-20261015-05'
RELEASE='/home/basketclip-project/stage2/runtime-field-rec-20261015-04'
manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
tests=(ROOT/'阶段2_离线哨声检测/tests/test_timed_input.py').read_text(encoding='utf-8')
rec_tests=(ROOT/'阶段2_离线哨声检测/tests/test_field_rec.py').read_text(encoding='utf-8')
fixture=(ROOT/'阶段2_离线哨声检测/tools/simulate_sampling_probe.py').read_text(encoding='utf-8')
script=f'''import hashlib,io,json,sys,types,unittest
from pathlib import Path
release=Path({RELEASE!r})
expected={manifest!r}
assert all(hashlib.sha256((release/p).read_bytes()).hexdigest()==h for p,h in expected.items())
pcm=Path('/proc/asound/card0/pcm0c/sub0/status')
assert pcm.read_text().strip()=='closed'
sys.path.insert(0,str(release/'阶段2_离线哨声检测/tools'))
fixture=types.ModuleType('simulate_sampling_probe')
exec(compile({fixture!r},'synthetic_fixture','exec'),fixture.__dict__)
sys.modules['simulate_sampling_probe']=fixture
namespace={{'__name__':'board_synthetic_clock_tests','__file__':str(release/'阶段2_离线哨声检测/tests/test_timed_input.py')}}
exec(compile({tests!r},namespace['__file__'],'exec'),namespace)
namespace['FFMPEG']=Path('/usr/bin/ffmpeg')
suite=unittest.TestSuite()
for name in unittest.defaultTestLoader.getTestCaseNames(namespace['TimedInputTests']):
    if name!='test_explicit_label_sink_receives_saved_software_candidate':
        suite.addTest(namespace['TimedInputTests'](name))
rec_namespace={{'__name__':'board_synthetic_rec_tests','__file__':str(release/'阶段2_离线哨声检测/tests/test_field_rec.py')}}
exec(compile({rec_tests!r},rec_namespace['__file__'],'exec'),rec_namespace)
suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(rec_namespace['FieldTests']))
log=io.StringIO()
result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
assert result.wasSuccessful(),log.getvalue()
assert all(hashlib.sha256((release/p).read_bytes()).hexdigest()==h for p,h in expected.items())
assert pcm.read_text().strip()=='closed'
print(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
    log=log.getvalue(),source_hashes_matched=len(expected),pcm_after='closed',
    real_capture_started=False,model_executed=False,hardware_600_seconds_verified=False),indent=2))
'''
(OUT/'board-synthetic-script.py').write_text(script,encoding='utf-8')
command=['ssh','-i','YOUR_SSH_IDENTITY_FILE',
         '-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes',
         'boarduser@192.0.2.10','/home/basketclip-project/stage2/deploy-20261013-01/venv-npu-run/bin/python -B -']
result=subprocess.run(command,input=script,text=True,encoding='utf-8',capture_output=True)
with (OUT/'board-synthetic.stdout.log').open('x',encoding='utf-8') as stream:stream.write(result.stdout)
with (OUT/'board-synthetic.stderr.log').open('x',encoding='utf-8') as stream:stream.write(result.stderr)
result.check_returncode()
value=json.loads(result.stdout)
with (OUT/'board-synthetic.json').open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
print(json.dumps({k:v for k,v in value.items() if k!='log'}))

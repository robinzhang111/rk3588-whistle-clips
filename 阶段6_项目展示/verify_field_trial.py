"""Verify field preparation using synthetic sources only; no REC or PCM opened."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).absolute().parent.parent
OUT = ROOT/'阶段6_项目展示/outputs/field-preparation-20261015-03'
tests = ROOT/'阶段2_离线哨声检测/tests'
suite = unittest.TestSuite()
for pattern in ('test_field_rec.py', 'test_board_runtime.py', 'test_start_space.py',
                'test_software_time.py', 'test_timed_input.py'):
    suite.addTests(unittest.defaultTestLoader.discover(str(tests), pattern=pattern))
log = io.StringIO()
result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
with (OUT/'tests.log').open('x', encoding='utf-8') as stream:
    stream.write(log.getvalue())
if not result.wasSuccessful():
    print(log.getvalue())
    sys.exit(1)
release='/home/basketclip-project/stage2/runtime-field-rec-20261015-02'
manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
test_code=(tests/'test_field_rec.py').read_text(encoding='utf-8')
script=f'''import hashlib,json,io,unittest
from pathlib import Path
release=Path({release!r})
expected={manifest!r}
assert all(hashlib.sha256((release/p).read_bytes()).hexdigest()==h for p,h in expected.items())
namespace={{'__file__':str(release/'阶段2_离线哨声检测/tests/test_field_rec.py'),'__name__':'synthetic_field_tests'}}
exec(compile({test_code!r},namespace['__file__'],'exec'),namespace)
log=io.StringIO()
result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(namespace['FieldTests']))
assert result.wasSuccessful(),log.getvalue()
print(json.dumps(dict(board_synthetic_tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
    log=log.getvalue(),sources_verified=len(expected),pcm_after=Path('/proc/asound/card0/pcm0c/sub0/status').read_text().strip(),
    real_capture_started=False,model_executed=False),indent=2))
'''
ssh=['ssh','-i','YOUR_SSH_IDENTITY_FILE',
     '-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes',
     'boarduser@192.0.2.10','/home/basketclip-project/stage2/deploy-20261013-01/venv-npu-run/bin/python -B -']
remote=subprocess.run(ssh,input=script,text=True,encoding='utf-8',capture_output=True)
with (OUT/'board-synthetic.stderr.log').open('x',encoding='utf-8') as stream:
    stream.write(remote.stderr)
remote.check_returncode()
board=json.loads(remote.stdout)
with (OUT/'board-synthetic.json').open('x',encoding='utf-8') as stream:
    json.dump(board,stream,ensure_ascii=False,indent=2)
summary=dict(local_tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
             board_synthetic_tests=board['board_synthetic_tests'],pcm_after=board['pcm_after'],
             real_capture_started=False,model_executed=False,
             entry_sha256=hashlib.sha256((ROOT/'阶段2_离线哨声检测/tools/field_rec.py').read_bytes()).hexdigest())
with (OUT/'verification.json').open('x',encoding='utf-8') as stream:
    json.dump(summary,stream,indent=2)
print(json.dumps(summary))

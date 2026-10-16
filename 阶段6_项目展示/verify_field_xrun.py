"""Local-only regression for the field XRUN mitigation; never connect to board."""
import io
import json
from pathlib import Path
import sys
import unittest

root = Path(__file__).absolute().parent.parent
output = root/'阶段6_项目展示/outputs/field-xrun-local-20261015-01'
output.mkdir(exist_ok=False)
suite = unittest.TestSuite()
for pattern in ('test_sampling_capture.py', 'test_field_rec.py', 'test_board_runtime.py',
                'test_start_space.py', 'test_software_time.py', 'test_timed_input.py'):
    suite.addTests(unittest.defaultTestLoader.discover(
        str(root/'阶段2_离线哨声检测/tests'), pattern=pattern))
log = io.StringIO()
result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
(output/'tests.log').write_text(log.getvalue(), encoding='utf-8')
summary = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
               board_connected=False, hardware_xrun_reproduced=False,
               real_capture_started=False, hardware_fix_verified=False)
(output/'verification.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary))
if not result.wasSuccessful():
    print(log.getvalue())
    sys.exit(1)

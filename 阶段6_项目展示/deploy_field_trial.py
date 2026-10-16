"""Prepare and deploy one independent REC trial release; check-only, never start."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).absolute().parent.parent
EVIDENCE = ROOT/'阶段6_项目展示/outputs/field-preparation-20261015-05'
RELEASE = '/home/basketclip-project/stage2/runtime-field-rec-20261015-04'
PYTHON = '/home/basketclip-project/stage2/deploy-20261013-01/venv-npu-run/bin/python'
OLD = '/home/basketclip-project/stage2/runtime-software-labels-20261015-01'
UNIT = 'basketclip-field-rec-20261015-03.service'


def main():
    EVIDENCE.mkdir(exist_ok=False)
    previous = json.loads((ROOT/'阶段6_项目展示/outputs/core-live-playback-20261015-04/board-manifest.snapshot.json').read_text(encoding='utf-8'))
    files = {p: (ROOT/p).read_bytes() for p in previous}
    assert all(hashlib.sha256(files[p]).hexdigest() == digest for p, digest in previous.items()
               if p not in ('阶段2_离线哨声检测/tools/sampling_capture.py',
                            '阶段2_离线哨声检测/tools/timed_input.py',
                            '阶段2_离线哨声检测/tools/board_runtime.py'))
    for p in ('阶段2_离线哨声检测/tools/field_rec.py',
              '阶段1_离线手动触发/basketclip/__init__.py',
              '阶段1_离线手动触发/basketclip/buttons.py'):
        files[p] = (ROOT/p).read_bytes()
    files['abi-source/upstream-pcm-1.2.8.h'] = (ROOT/'阶段2_离线哨声检测/outputs/sampling-probe-capture-prep-20261015-01/upstream-pcm-1.2.8.h').read_bytes()
    files['abi-source/source.json'] = (ROOT/'阶段2_离线哨声检测/outputs/sampling-probe-capture-prep-20261015-01/source.json').read_bytes()
    files['native-relation.json'] = (ROOT/'阶段2_离线哨声检测/outputs/software-time-20261015-01/board-preflight/native-relation.json').read_bytes()
    launcher = f'''#!/bin/bash
set -euo pipefail
[[ $# == 0 || ( $# == 1 && "$1" == --check-only ) ]] || exit 2
boot=$(cat /proc/sys/kernel/random/boot_id)
session=core-field-20261015-03-${{boot:0:8}}
output=/home/basketclip-project/stage2/outputs/$session
command=({PYTHON} -B '{RELEASE}/阶段2_离线哨声检测/tools/field_rec.py'
 --session "$session" --output "$output/capture"
 --model /home/basketclip-project/stage2/deploy-20261013-01/conversion-onnx-fp16/WhistleNetMk16-fp16.rknn
 --header {RELEASE}/abi-source/upstream-pcm-1.2.8.h
 --relation {RELEASE}/native-relation.json)
if [[ $# == 1 ]]; then exec "${{command[@]}}" --check-only; fi
set -o noclobber
exec "${{command[@]}}" > "$output.console.log" 2>&1
'''
    service = f'''[Unit]
Description=REC starts one offline 600 second field trial
After=local-fs.target sound.target

[Service]
Type=simple
ExecStart=/bin/bash {RELEASE}/run-field.sh
Restart=no
TimeoutStopSec=20
KillMode=control-group
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
'''
    files['run-field.sh'] = launcher.encode()
    files[UNIT] = service.encode()
    (EVIDENCE/'run-field.sh').write_bytes(files['run-field.sh'])
    (EVIDENCE/UNIT).write_bytes(files[UNIT])
    manifest = {p: hashlib.sha256(data).hexdigest() for p, data in files.items()}
    (EVIDENCE/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    payload = {p: base64.b64encode(data).decode() for p, data in files.items()}
    script = '''import base64,hashlib,json,socket,subprocess
from pathlib import Path
release=Path(RELEASE)
old=Path(OLD)
unit=UNIT
assert socket.gethostname()=='lubancat'
assert not release.exists()
assert not Path('/etc/systemd/system',unit).exists()
old_unit='basketclip-field-rec-20261015-02.service'
boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()[:8]
old_output=Path('/home/basketclip-project/stage2/outputs')/('core-field-20261015-02-'+boot)
def assert_no_capture():
    active=subprocess.run(['systemctl','is-active',old_unit],capture_output=True,text=True).stdout.strip()
    assert active not in ('activating','deactivating')
    if active=='active':
        assert json.loads((old_output/'status.json').read_text())['status']=='waiting'
        assert not (old_output/'capture').exists()
    return active
assert_no_capture()
pcm=Path('/proc/asound/card0/pcm0c/sub0/status')
assert pcm.read_text().strip()=='closed'
assert subprocess.run(['/usr/bin/fuser','-s','/dev/snd/pcmC0D0c']).returncode==1
assert all(hashlib.sha256((old/p).read_bytes()).hexdigest()==h for p,h in PREVIOUS.items())
led=Path('/sys/class/leds/sys_led/trigger').read_text()
release.mkdir()
payload=PAYLOAD
expected=EXPECTED
for name,data in payload.items():
    path=release/name
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream: stream.write(base64.b64decode(data))
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected[name]
    if path.suffix=='.py': compile(path.read_bytes(),str(path),'exec')
(release/'manifest.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2))
subprocess.run(['bash','-n',str(release/'run-field.sh')],check=True)
check=subprocess.run(['bash',str(release/'run-field.sh'),'--check-only'],capture_output=True,text=True)
if check.returncode: raise RuntimeError(check.stderr)
preflight=json.loads(check.stdout)
assert pcm.read_text().strip()=='closed'
assert Path('/sys/class/leds/sys_led/trigger').read_text()==led
assert not list(Path('/home/basketclip-project/stage2/outputs').glob('core-field-20261015-03-*'))
subprocess.run(['systemd-analyze','verify',str(release/unit)],check=True)
with Path('/etc/systemd/system',unit).open('xb') as stream: stream.write((release/unit).read_bytes())
subprocess.run(['systemctl','daemon-reload'],check=True)
if assert_no_capture()=='active':
    subprocess.run(['systemctl','stop',old_unit],check=True)
    assert not (old_output/'capture').exists()
assert pcm.read_text().strip()=='closed'
subprocess.run(['systemctl','enable',unit],check=True,capture_output=True)
subprocess.run(['systemctl','disable',old_unit],check=True,capture_output=True)
enabled=subprocess.check_output(['systemctl','is-enabled',unit],text=True).strip()
active=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True).stdout.strip()
assert enabled=='enabled' and active=='inactive'
print(json.dumps(dict(release=str(release),sources_verified=len(expected),preflight=preflight,
    service=unit,enabled=enabled,active=active,pcm_after=pcm.read_text().strip(),
    led_unchanged=True,capture_started=False,listener_started=False),ensure_ascii=False,indent=2))
'''
    for key, value in {'RELEASE':RELEASE, 'OLD':OLD, 'UNIT':UNIT,
                       'PREVIOUS':previous, 'PAYLOAD':payload, 'EXPECTED':manifest}.items():
        script = script.replace(key, repr(value))
    (EVIDENCE/'remote-deploy.py').write_text(script, encoding='utf-8')
    ssh = ['ssh','-i','YOUR_SSH_IDENTITY_FILE',
           '-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=8',
           '-o','StrictHostKeyChecking=yes','boarduser@192.0.2.10', PYTHON+' -B -']
    result = subprocess.run(ssh,input=script,capture_output=True,text=True,encoding='utf-8')
    (EVIDENCE/'deploy.stdout.log').write_text(result.stdout,encoding='utf-8')
    (EVIDENCE/'deploy.stderr.log').write_text(result.stderr,encoding='utf-8')
    result.check_returncode()
    value=json.loads(result.stdout)
    (EVIDENCE/'board-preflight.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in value.items() if k!='preflight'},ensure_ascii=False))


if __name__ == '__main__':
    main()

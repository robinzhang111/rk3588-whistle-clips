"""Read-only runtime metadata; no audio capture, ML imports or installation."""
import importlib.metadata as metadata
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import time


packages = {}
for name in ['pip', 'numpy', 'h5py', 'keras', 'tensorflow',
             'tensorflow-cpu', 'tensorflow-cpu-aws', 'tflite-runtime',
             'onnxruntime', 'rknn-toolkit-lite2']:
    try:
        packages[name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        packages[name] = None

media = {}
for name in ['ffmpeg', 'ffprobe']:
    executable = shutil.which(name)
    info = {'path': executable}
    if executable:
        result = subprocess.run([executable, '-version'], capture_output=True,
                                text=True, timeout=10)
        info.update(exit_code=result.returncode,
                    version=result.stdout.splitlines()[0] if result.stdout else None)
    media[name] = info

board = Path('/proc/device-tree/model')
disk = shutil.disk_usage('/home')
print(json.dumps(dict(
    scope='metadata only; no imports/forward/audio acceptance',
    measured_utc_epoch=time.time(), python=sys.version,
    executable=sys.executable, prefix=sys.prefix, machine=platform.machine(),
    libc=platform.libc_ver(),
    board_model=board.read_bytes().rstrip(b'\x00').decode() if board.exists() else None,
    packages=packages, media=media, home_free_bytes=disk.free,
    project_directories={p: Path(p).is_dir() for p in [
        '/home/basketclip-project/stage1', '/home/basketclip-project/stage2',
        '/home/basketclip-project/stage3']}
), ensure_ascii=False, indent=2))

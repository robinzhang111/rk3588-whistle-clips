"""Synthetic 48k -> 22050 relation check using the existing fixed FFmpeg filter."""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import threading

import numpy as np
from basketball_audio_adapter import FILTER


def producer_command(ffmpeg):
    return [str(ffmpeg), '-nostdin', '-hide_banner', '-v', 'error', '-xerror',
            '-probesize', '32', '-analyzeduration', '0',
            '-f', 's16le', '-ar', '48000', '-ac', '2', '-i', 'pipe:0',
            '-af', FILTER, '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1']


def check_relation(ffmpeg):
    # Positions that map exactly to output samples: raw multiples of 320.
    positions = [0, 320, 9600, 24000, 38400, 47680]
    audio = np.zeros((48000, 2), dtype='<i2')
    for position in positions:
        audio[position] = [16000, -12000]
    raw = audio.tobytes()
    command = producer_command(ffmpeg)
    reference = subprocess.run(command, input=raw, capture_output=True, check=True).stdout
    converted = np.frombuffer(reference, dtype='<f4').reshape(-1, 2)
    assert len(converted) == 22050 and np.isfinite(converted).all()
    for position in positions:
        expected = position*147//320
        for channel in [0, 1]:
            left, right = max(0, expected-20), min(len(converted), expected+21)
            peak = left+int(np.argmax(np.abs(converted[left:right, channel])))
            assert peak == expected, (position, channel, peak, expected)
    # Feed arbitrary short chunks through the same persistent process; check EOF.
    for frames in [512, 777]:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        errors = []
        def write():
            try:
                for index in range(0, len(raw), frames*4):
                    process.stdin.write(raw[index:index+frames*4])
                process.stdin.close()
            except BaseException as failure:
                errors.append(str(failure))
        writer = threading.Thread(target=write)
        writer.start()
        chunked = process.stdout.read()
        error = process.stderr.read()
        result = process.wait(timeout=10)
        writer.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
        assert result == 0 and not errors and not writer.is_alive(), (errors, error)
        assert chunked == reference, 'Chunk boundary changed output'
    version = subprocess.run([str(ffmpeg), '-version'], capture_output=True,
                             text=True, check=True).stdout.splitlines()[0]
    return dict(status='passed', ffmpeg_sha256=hashlib.sha256(Path(ffmpeg).read_bytes()).hexdigest(),
        ffmpeg_version=version, filter=FILTER, input_rate=48000, target_rate=22050,
        input_frames=48000, target_frames=len(converted), tested_raw_impulses=positions,
        original_at_target_zero='0', ratio=str(Fraction(48000, 22050)),
        arbitrary_chunks_bitwise_equal=True, scope='fixed resampler software coordinate check; '
        'start/interior/end impulses plus chunk/EOF consistency, not acoustic latency or driver UTC',
        real_capture=False, formal_utc_upload=False)


def validate_relation(path, ffmpeg):
    result = json.loads(Path(path).read_text(encoding='utf-8'))
    if (result.get('status') != 'passed' or result.get('filter') != FILTER
            or result.get('input_rate') != 48000 or result.get('target_rate') != 22050
            or result.get('original_at_target_zero') != '0'
            or result.get('ffmpeg_sha256') != hashlib.sha256(Path(ffmpeg).read_bytes()).hexdigest()):
        raise ValueError('Resampling evidence does not match this FFmpeg/filter')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a new evidence file')
    result = check_relation(args.ffmpeg)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2)
    print(json.dumps(result))


if __name__ == '__main__':
    main()

"""Persist and send an existing, explicitly selected candidate file; no recapture."""
import argparse
import hashlib
import json
from pathlib import Path

import eventlink
from auto_events import candidate_event
from automatic_labels import AutomaticLabels


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def candidates(path):
    with path.open(encoding='utf-8') as source:
        while True:
            line = source.readline(16385)
            if not line:
                return
            if len(line.encode('utf-8'))>16384 or not line.endswith('\n'):
                raise ValueError('Oversized or incomplete candidate evidence')
            yield json.loads(line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage1', required=True, type=Path)
    parser.add_argument('--candidates', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--session', required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--allow-software-time', action='store_true')
    args = parser.parse_args()
    eventlink.stage1_path(args.stage1)
    digest = sha(args.candidates)
    count = 0
    # Validate the whole saved candidate file before creating an uploader.
    for count, row in enumerate(candidates(args.candidates), 1):
        if row['session_id'] != args.session:
            raise ValueError('Candidate session mismatch')
        candidate_event(row['candidate'], row['timing'], args.session, count,
                        allow_software_time=args.allow_software_time)
    if not count:
        raise ValueError('No candidates to send')
    args.output.mkdir(parents=True, exist_ok=False)
    labels = AutomaticLabels(args.output, args.session, args.url,
                             allow_software_time=args.allow_software_time)
    try:
        for row in candidates(args.candidates):
            labels.save(row['candidate'], row['timing'])
            labels.pump()
    except BaseException:
        labels.failed = True
        raise
    finally:
        result = labels.close()
        result.update(source_sha256=digest,
            source_unchanged=sha(args.candidates)==digest,
            source_type='existing automatic detector candidates; not a new capture')
        with (args.output/'summary.json').open('x', encoding='utf-8') as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['all_acknowledged'] and result['source_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

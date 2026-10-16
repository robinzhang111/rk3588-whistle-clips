"""Check a deployment manifest against files in its new session directory."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    for name, info in manifest.items():
        path = args.root/name
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest.update(chunk)
        if path.stat().st_size!=info['bytes'] or digest.hexdigest()!=info['sha256']:
            raise ValueError('Upload mismatch: '+name)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(dict(status='passed',files=len(manifest),manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest()),stream,indent=2)
    print('Verified',len(manifest),'uploaded files')


if __name__=='__main__':
    main()

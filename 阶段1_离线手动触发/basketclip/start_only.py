"""Explicit user-selected start alignment; sample positions only, never UTC."""
import argparse
import json
from pathlib import Path

from .media import inspect_video
from .relative import sha
from .timing import clip_window, finite_number


def candidate_window(row, alignment, duration):
    if alignment.get('kind') != 'user_selected_start_only':
        raise ValueError('Explicit user-selected start alignment required')
    candidate = row['candidate']
    if (row.get('session_id') != alignment['session_id']
            or candidate.get('segment_id') != alignment['segment_id']):
        raise ValueError('Candidate and start alignment belong to different segments')
    sample = candidate['estimated_start_sample']
    if type(sample) is not int or sample < 0 or alignment.get('sample_rate') != 22050:
        raise ValueError('Nonnegative sample position at 22050 Hz required')
    if candidate.get('closed_by') in ('interrupted', 'incomplete_frame', 'source_or_processing_error'):
        raise ValueError('Candidate touches an input interruption; do not bridge a gap')
    offset = finite_number(alignment['video_seconds_at_sample_zero'], 'start offset')
    if offset < 0:
        raise ValueError('Start offset must not be negative')
    position = offset + sample/22050
    window = clip_window(position, 5, 3, duration)
    if window is None:
        raise ValueError('Candidate outside recording')
    return dict(video_seconds=position, start_seconds=window[0], end_seconds=window[1],
                marker_seconds=position-window[0], mapping_kind='user_selected_start_only',
                absolute_utc_used=False, synchronization_verified=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('video','candidates','alignment','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a new output directory; do not overwrite prior plans')
    alignment=json.loads(args.alignment.read_text(encoding='utf-8'))
    digest=sha(args.video)
    if digest!=alignment['source_video_sha256']:
        raise ValueError('Video differs from user-selected start alignment')
    info,duration=inspect_video(args.video)
    stream=next(s for s in info['streams'] if s['codec_type']=='video')
    if float(stream['start_time'])!=0 or float(info['format']['start_time'])!=0:
        raise ValueError('Start alignment requires zero media start PTS')
    rows=[json.loads(line) for line in args.candidates.read_text(encoding='utf-8').splitlines()]
    results=[]
    for number,row in enumerate(rows,1):
        job=dict(candidate_number=number,review_status='pending')
        try:
            job.update(candidate_window(row,alignment,duration),status='ready')
        except ValueError as failure:
            job.update(status='rejected',reason=str(failure))
        results.append(job)
    args.output.mkdir(parents=True,exist_ok=False)
    manifest=dict(kind='user_selected_start_only',alignment=alignment,
        protected_sha256={str(args.video):digest,str(args.candidates):sha(args.candidates),
                          str(args.alignment):sha(args.alignment)},
        duration_seconds=duration,drift_compensation_applied=False,
        total_alignment_error_seconds=None,formal_utc_mapping=False,results=results)
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(args.output),ready=sum(r['status']=='ready' for r in results),
                         rejected=sum(r['status']=='rejected' for r in results))))


if __name__=='__main__':
    main()

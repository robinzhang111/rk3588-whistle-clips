"""Bounded UTC-to-video intervals; synthetic preparation is explicit."""
import argparse
import json
from pathlib import Path
import subprocess

from .media import extract_clip, inspect_video
from .relative import sha
from .storage import load_events, load_json, save_json
from .timing import clip_window, event_position, finite_number, utc_time


def validate_mapping(mapping):
    if (mapping.get('schema') != 'bounded-video-utc-v1'
            or mapping.get('kind') not in ('synthetic', 'verified_video_capture', 'user_supplied_video_anchor', 'software_playback_observation')
            or not isinstance(mapping.get('evidence'), str) or not mapping['evidence'].strip()
            or not isinstance(mapping.get('session_id'), str) or not mapping['session_id'].strip()):
        raise ValueError('Unknown or unsupported video mapping')
    digest = mapping.get('source_video_sha256', '')
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Video source hash required')
    duration = finite_number(mapping['video_duration'], 'duration')
    first = finite_number(mapping['video_first_pts'], 'first PTS')
    origin = finite_number(mapping['format_start_pts'], 'format PTS')
    if duration <= 0 or first < origin:
        raise ValueError('Invalid video extent')
    intervals = mapping['intervals']
    if not isinstance(intervals, list) or not intervals:
        raise ValueError('Known continuous intervals required')
    previous_utc = previous_end = None
    for interval in intervals:
        begin, end = utc_time(interval['utc_start']), utc_time(interval['utc_end'])
        lo, hi = [finite_number(interval[k], k) for k in ('video_start', 'video_end')]
        alignment = interval['alignment']
        start = event_position(dict(session_id=mapping['session_id'], event_time_utc=interval['utc_start']), alignment)
        finish = event_position(dict(session_id=mapping['session_id'], event_time_utc=interval['utc_end']), alignment)
        if (begin >= end or not 0 <= lo < hi <= duration
                or abs(start-lo) > 1e-6 or abs(finish-hi) > 1e-6
                or (previous_utc is not None and (begin < previous_utc or lo < previous_end))):
            raise ValueError('Overlapping or inconsistent mapping intervals')
        previous_utc, previous_end = end, hi


def mapped_window(event, mapping):
    validate_mapping(mapping)
    if event['session_id'] != mapping['session_id']:
        raise ValueError('Session mismatch')
    instant = utc_time(event['event_time_utc'])
    for interval in mapping['intervals']:
        if utc_time(interval['utc_start']) <= instant < utc_time(interval['utc_end']):
            position = event_position(event, interval['alignment'])
            window = clip_window(position, 5, 3, mapping['video_duration'])
            if window is None:
                raise ValueError('Event outside video')
            start, end = window
            if (start < finite_number(interval['video_start'], 'video start')
                    or end > finite_number(interval['video_end'], 'video end')):
                raise ValueError('Requested clip crosses gap or mapping boundary')
            offset = finite_number(mapping['video_first_pts'], 'first PTS') - finite_number(mapping['format_start_pts'], 'format PTS')
            return dict(video_event_seconds=position, start_seconds=start, end_seconds=end,
                        source_seek_start=start+offset, source_seek_end=end+offset,
                        clipped_at_start=start > position-5, clipped_at_end=end < position+3)
    raise ValueError('No valid mapping for event; gap or outside known interval')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('video', 'events', 'mapping', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--synthetic-test', action='store_true')
    parser.add_argument('--user-anchor', action='store_true')
    parser.add_argument('--software-playback', action='store_true')
    args = parser.parse_args()
    mapping = load_json(args.mapping)
    validate_mapping(mapping)
    # Never silently treat synthetic UTC fixtures as real capture evidence.
    if (mapping['kind'] == 'synthetic') != args.synthetic_test:
        raise ValueError('Synthetic mapping requires explicit synthetic-test mode')
    if (mapping['kind'] == 'user_supplied_video_anchor') != args.user_anchor:
        raise ValueError('User-provided mapping requires explicit user-anchor mode')
    if (mapping['kind'] == 'software_playback_observation') != args.software_playback:
        raise ValueError('Software playback mapping requires explicit software-playback mode')
    protected = {str(p.absolute()): sha(p) for p in (args.video, args.events, args.mapping)}
    if sha(args.video) != mapping['source_video_sha256']:
        raise ValueError('Source video hash mismatch')
    info, duration = inspect_video(args.video)
    stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
    if (abs(float(info['format']['start_time'])-mapping['format_start_pts']) > 1e-6
            or abs(float(stream['start_time'])-mapping['video_first_pts']) > 1e-6
            or float(mapping['video_duration'])+float(mapping['video_first_pts'])-float(mapping['format_start_pts']) > duration+1e-6):
        raise ValueError('Media extent differs from mapping')
    events = load_events(args.events)
    jobs = []
    for event in events:
        result = dict(event=event)
        try:
            result.update(mapped_window(event, mapping), status='ready')
        except ValueError as error:
            result.update(status='rejected', reason=str(error))
        jobs.append(result)
    args.output.mkdir(parents=True, exist_ok=False)
    for result in jobs:
        if result['status'] == 'ready':
            filename = 'event_%06d.mp4' % result['event']['event_id']
            result['file'] = filename
            try:
                extract_clip(args.video, args.output/filename,
                             result['source_seek_start'], result['source_seek_end'])
                result['status'] = 'completed'
            except subprocess.CalledProcessError as error:
                result.update(status='failed', reason=error.stderr or str(error))
    if any(sha(Path(p)) != digest for p, digest in protected.items()):
        raise ValueError('Protected input changed')
    save_json(args.output/'manifest.json', dict(mapping_kind=mapping['kind'],
              real_capture_utc_verified=False if args.synthetic_test or args.user_anchor or args.software_playback else None,
              mapping_evidence=mapping['evidence'], event_time_quality=mapping.get('event_time_quality', 'unknown'),
              protected_sha256=protected, before_seconds=5, after_seconds=3, results=jobs))
    print(json.dumps({state: sum(r['status'] == state for r in jobs)
                      for state in ('completed', 'rejected', 'failed')}))
    return int(any(r['status'] == 'failed' for r in jobs))


if __name__ == '__main__':
    raise SystemExit(main())

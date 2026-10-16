"""Same-source relative candidates; no synthetic UTC alignment."""
import argparse
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import subprocess

from .media import extract_clip,inspect_video
from .timing import clip_window,finite_number


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def relative_position(event,mapping):
    if mapping['schema']!='same-source-relative-media-v1' or 'event_time_utc' in event:
        raise ValueError('Expected relative same-source candidate/mapping')
    start,end=event['estimated_start_sample'],event['estimated_end_sample']
    if type(start) is not int or type(end) is not int or not 0<=start<end<=mapping['audio_frames']:
        raise ValueError('Candidate sample bounds invalid')
    rate=finite_number(mapping['audio_source_rate'],'source rate')
    target=finite_number(mapping['sample_rate'],'target rate')
    if rate<=0 or target<=0:
        raise ValueError('Invalid sample rate')
    source_start,source_end=start*rate/target,end*rate/target
    frames=mapping['audio_frame_sample_map']
    if any(not frame['mapping_valid'] and source_start<frame['source_end_sample'] and
           source_end>frame['source_start_sample'] for frame in frames):
        raise ValueError('Candidate touches ambiguous/discontinuous audio frames')
    index=bisect_right([frame['source_start_sample'] for frame in frames],source_start)-1
    frame=frames[index]
    if index<0 or source_start>=frame['source_end_sample']:
        raise ValueError('Candidate start lacks measured frame mapping')
    media=finite_number(frame['media_start_pts'],'audio PTS')+(source_start-frame['source_start_sample'])/rate
    return media-finite_number(mapping['video_first_pts'],'video first PTS')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',type=Path,required=True)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--mapping',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--before',type=float,default=5.)
    parser.add_argument('--after',type=float,default=3.)
    args=parser.parse_args()
    load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    mapping=load(args.mapping)
    summary=load(args.run/'summary.json')
    events=[json.loads(line) for line in (args.run/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    protected={str(p):sha(p) for p in [args.video,args.mapping,args.run/'summary.json',args.run/'events.jsonl']}
    if sha(args.video)!=mapping['source_video_sha256'] or mapping['audio_sha256'] not in summary['protected_sha256'].values():
        raise ValueError('Video/audio source hash mismatch')
    if summary['status']!='completed' or summary['backend']!='npu' or summary['buffer']['samples']!=mapping['audio_frames']:
        raise ValueError('Unverified detection run or sample count')
    info,_=inspect_video(args.video)
    if float(info['format']['start_time'])!=mapping['format_start_pts']:
        raise ValueError('Source media start differs from mapping')
    duration=mapping['video_end_pts']-mapping['video_first_pts']
    clip_window(0,args.before,args.after,duration)
    ids=[event['event_id'] for event in events]
    if any(type(i) is not int or i<1 for i in ids) or len(set(ids))!=len(ids):
        raise ValueError('Invalid/duplicate candidate IDs')
    args.output.mkdir(parents=True,exist_ok=False)
    results=[]
    seek_offset=mapping['video_first_pts']-mapping['format_start_pts']
    for event in events:
        result=dict(event=event,center='candidate onset',time_basis='video first-frame relative; no UTC')
        try:
            position=relative_position(event,mapping)
            result['video_event_seconds']=position
            window=clip_window(position,args.before,args.after,duration)
            if window is None:
                result.update(status='skipped',reason='Candidate outside valid video range')
            else:
                start,end=window
                filename='event_%06d.mp4'%event['event_id']
                result.update(start_seconds=start,end_seconds=end,requested_duration=end-start,
                    source_seek_start=start+seek_offset,source_seek_end=end+seek_offset,
                    event_position_in_clip=position-start,file=filename,
                    clipped_at_start=start>position-args.before,clipped_at_end=end<position+args.after)
                extract_clip(args.video,args.output/filename,start+seek_offset,end+seek_offset)
                result['status']='completed'
        except (ValueError,subprocess.CalledProcessError) as error:
            result.update(status='failed',reason=error.stderr if isinstance(error,subprocess.CalledProcessError) else str(error))
        results.append(result)
        (args.output/('event_%06d.json'%event['event_id'])).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if any(sha(Path(p))!=h for p,h in protected.items()):
        raise ValueError('Protected video/candidate/mapping changed')
    manifest=dict(source_video=str(args.video.absolute()),protected_sha256=protected,
        before_seconds=args.before,after_seconds=args.after,window_rule='[onset-5,onset+3] with valid-video clipping',
        video_duration=duration,seek_offset=seek_offset,absolute_utc_start=None,results=results)
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print({status:sum(r['status']==status for r in results) for status in ['completed','skipped','failed']})


if __name__=='__main__':
    main()

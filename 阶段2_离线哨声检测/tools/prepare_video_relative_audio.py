"""Source-bound video/audio PTS mapping for an explicitly selected file test."""
import argparse
from fractions import Fraction
import json
from pathlib import Path

import detect_file as d


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    source=args.input.absolute()
    original=d.sha(source)
    args.output.mkdir(parents=True,exist_ok=False)
    metadata=json.loads(d.run([str(d.FFPROBE),'-v','error','-show_streams','-show_format',
        '-of','json',str(source)],args.output/'probe.log'))
    d.write(args.output/'input-metadata.json',metadata)
    audio_stream=next(s for s in metadata['streams'] if s['codec_type']=='audio')
    source_rate=int(audio_stream['sample_rate'])
    audio_frames=json.loads(d.run([str(d.FFPROBE),'-v','error','-select_streams','a:0',
        '-show_frames','-show_entries','frame=best_effort_timestamp_time,nb_samples',
        '-of','json',str(source)],args.output/'audio-probe.log'))['frames']
    d.write(args.output/'decoded-frame-timeline.json',audio_frames)
    frame_map=[]
    source_count=0
    audio_gaps=[]
    for index,frame in enumerate(audio_frames):
        pts=float(frame['best_effort_timestamp_time'])
        count=int(frame['nb_samples'])
        row=dict(source_start_sample=source_count,source_end_sample=source_count+count,
                 media_start_pts=pts,mapping_valid=True)
        if frame_map:
            previous=frame_map[-1]
            gap=pts-(previous['media_start_pts']+(previous['source_end_sample']-previous['source_start_sample'])/source_rate)
            if abs(gap)>2/source_rate:
                previous['mapping_valid']=False
                row['mapping_valid']=False
                audio_gaps.append(dict(frame_index=index,gap_seconds=gap))
        frame_map.append(row)
        source_count+=count
    decoded=args.output/'decoded.wav'
    d.run([str(d.FFMPEG),'-nostdin','-v','warning','-xerror','-n','-i',str(source),
        '-map','0:a:0','-vn','-c:a','pcm_f32le',str(decoded)],args.output/'decode.log')
    original_audio,decoded_rate=d.c.read_float_wav(decoded)
    if decoded_rate!=source_rate or len(original_audio)!=source_count:
        raise ValueError('Decoded source samples differ from measured frames')
    from basketball_audio_adapter import FILTER
    adapted=args.output/'adapted.wav'
    d.run([str(d.FFMPEG),'-nostdin','-v','warning','-xerror','-n','-i',str(decoded),
        '-map','0:a:0','-af',FILTER,'-c:a','pcm_f32le',str(adapted)],args.output/'adapt.log')
    audio,rate=d.c.read_float_wav(adapted)
    if rate!=22050 or audio.shape[1]!=original_audio.shape[1] or abs(len(audio)/rate-source_count/source_rate)>2/rate:
        raise ValueError('Resampling format/duration differs')
    video=next(s for s in metadata['streams'] if s['codec_type']=='video')
    frames=json.loads(d.run([str(d.FFPROBE),'-v','error','-select_streams','v:0',
        '-show_frames','-show_entries','frame=best_effort_timestamp_time,duration_time',
        '-of','json',str(source)],args.output/'video-probe.log'))['frames']
    d.write(args.output/'video-frame-timeline.json',frames)
    if not frames:
        raise ValueError('No decoded video frames')
    pts=[float(f['best_effort_timestamp_time']) for f in frames]
    durations=[float(f['duration_time']) for f in frames]
    if any(duration<=0 for duration in durations) or any(b<=a for a,b in zip(pts,pts[1:])):
        raise ValueError('Invalid video timestamp/duration sequence')
    maximum_gap=max(abs(b-a-duration) for a,b,duration in zip(pts,pts[1:],durations))
    tolerance=2*float(Fraction(video['time_base']))
    if maximum_gap>tolerance:
        raise ValueError('Video timeline discontinuity needs separate mapping')
    format_start=float(metadata['format']['start_time'])
    format_end=format_start+float(metadata['format']['duration'])
    end=min(pts[-1]+durations[-1],format_end)
    if end<=pts[0]:
        raise ValueError('No valid video range')
    report=dict(schema='same-source-relative-media-v1',source_video=str(source),
        source_video_sha256=original,audio_path=str(adapted.absolute()),
        audio_sha256=d.sha(adapted),sample_rate=22050,audio_frames=len(audio),
        channels=audio.shape[1],audio_first_pts=frame_map[0]['media_start_pts'],
        audio_source_rate=source_rate,audio_source_frames=source_count,
        audio_frame_sample_map=frame_map,audio_discontinuities=audio_gaps,
        invalid_audio_frame_indices=[i for i,f in enumerate(frame_map) if not f['mapping_valid']],
        video_first_pts=pts[0],video_end_pts=end,video_frames=len(frames),
        format_start_pts=format_start,video_maximum_pts_gap=maximum_gap,
        video_gap_tolerance_seconds=tolerance,video_time_base=video['time_base'],
        position_rule='target sample to decoded source sample to measured frame PTS; subtract video_first_pts; reject events touching invalid frames',
        seek_rule='input seek seconds = video-relative position + video_first_pts - format_start_pts',
        absolute_utc_start=None,same_source_file_not_cross_device_sync=True,
        audio_retention='explicit selected-video test asset; not microphone normal recording')
    if d.sha(source)!=original:
        raise ValueError('Original video changed')
    d.write(args.output/'audio-mapping.json',report)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()

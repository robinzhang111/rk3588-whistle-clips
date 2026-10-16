"""Verify generated same-source clips and create a local review page."""
import argparse
from fractions import Fraction
import html
import json
from pathlib import Path
import subprocess

from .media import inspect_video,require_tool
from .relative import sha


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    manifest=json.loads((args.output/'manifest.json').read_text(encoding='utf-8'))
    source=Path(manifest['source_video'])
    info,_=inspect_video(source)
    video=next(s for s in info['streams'] if s['codec_type']=='video')
    tolerance=1/float(Fraction(video['avg_frame_rate']))
    assert manifest['before_seconds']==5 and manifest['after_seconds']==3
    assert manifest['absolute_utc_start'] is None
    checks=[]
    cards=[]
    for result in manifest['results']:
        assert result['status']=='completed',result
        position=result['video_event_seconds']
        assert result['start_seconds']==max(0,position-5)
        assert result['end_seconds']==min(manifest['video_duration'],position+3)
        path=args.output/result['file']
        streams,duration=inspect_video(path)
        types={s['codec_type']:s['codec_name'] for s in streams['streams']}
        assert types.get('video')=='h264' and types.get('audio')=='aac',types
        decode=subprocess.run([require_tool('ffmpeg'),'-nostdin','-v','error','-xerror',
            '-i',str(path),'-map','0:v:0','-map','0:a:0','-f','null','-'],
            capture_output=True,check=True)
        assert not decode.stderr,decode.stderr
        difference=abs(duration-result['requested_duration'])
        assert difference<=tolerance,(path,duration,result['requested_duration'])
        checks.append(dict(file=result['file'],sha256=sha(path),actual_duration=duration,
            requested_duration=result['requested_duration'],duration_abs_error=difference,
            streams=types,full_decode_exit=decode.returncode,
            event_position_in_clip=result['event_position_in_clip'],
            source_seek_start=result['source_seek_start'],source_seek_end=result['source_seek_end']))
        cards.append('<section><h2>候选 %d · 起点 %.6f 秒</h2><p>源视频 %.6f～%.6f 秒；实际片段 %.6f 秒；目标在片段内 %.6f 秒</p><video controls preload="metadata" src="%s"></video><p><a href="%s">打开片段</a></p></section>'%
            (result['event']['event_id'],position,result['start_seconds'],result['end_seconds'],
             duration,result['event_position_in_clip'],html.escape(result['file']),html.escape(result['file'])))
    for p,h in manifest['protected_sha256'].items():
        assert sha(Path(p))==h,p
    report=dict(status='passed',clips=len(checks),checks=checks,
        duration_tolerance_one_source_frame_seconds=tolerance,source_and_labels_unchanged=True,
        window='candidate onset minus 5 seconds to onset plus 3 seconds; edge clipping',
        playback_scope='streams and complete decode checked; human sound/video confirmation pending',
        no_utc_or_detection_quality_acceptance=True)
    (args.output/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    page='<!doctype html><meta charset="utf-8"><title>候选片段核对</title><style>body{font-family:system-ui;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px}video{width:100%}section{border:1px solid #ddd;padding:14px}</style><h1>短视频候选片段 · 人工核对</h1><p>起点前5秒、后3秒；首尾不足按可用范围裁切。候选不是人工真值，尚未确认声音类别或判罚。</p><p>共源相对媒体映射，不是跨设备UTC同步；原音轨末尾有PTS重叠，相关异常已记录。</p><main>'+''.join(cards)+'</main>'
    (args.output/'review.html').write_text(page,encoding='utf-8')
    print('Verified',len(checks),'clips; human listening/viewing pending')


if __name__=='__main__':
    main()

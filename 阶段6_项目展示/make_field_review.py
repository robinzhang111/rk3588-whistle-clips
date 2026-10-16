"""Create review-only clips from board candidates and a provisional camera start."""
import html
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).absolute().parent.parent
sys.path.insert(0, str(ROOT/'阶段1_离线手动触发'))
from basketclip.media import extract_clip, inspect_video
from basketclip.relative import sha
from basketclip.timing import clip_window, utc_time

OUT = ROOT/'阶段1_离线手动触发/outputs/field-candidate-review-20261015-01'


def plan(row, assumed_start, duration):
    timing = row['timing']
    if timing['status'] != 'software_estimate' or not timing.get('estimated_utc'):
        raise ValueError('Candidate has no usable software time estimate')
    position = (utc_time(timing['estimated_utc'])-utc_time(assumed_start)).total_seconds()
    window = clip_window(position, 5, 3, duration)
    if window is None:
        raise ValueError('Candidate outside provisional video range')
    return dict(provisional_video_seconds=position, start_seconds=window[0],
                end_seconds=window[1], provisional_marker_seconds=position-window[0])


def save(path, value):
    # Current progress in this exclusively created review directory only.
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    audit_file = ROOT/'阶段6_项目展示/outputs/acepro2-time-audit-20261015-01/time-audit.json'
    audit = json.loads(audit_file.read_text(encoding='utf-8'))
    video = Path(audit['video'])
    candidates = ROOT/'阶段6_项目展示/outputs/field-return-20261015-01/core-field-20261015-02-06f3633f/capture/candidates.relative.jsonl'
    rows = [json.loads(line) for line in candidates.read_text(encoding='utf-8').splitlines()]
    if len(rows) != 16 or any(row['session_id'] != 'core-field-20261015-02-06f3633f' for row in rows):
        raise ValueError('Expected the preserved 16-candidate field session')
    os.environ['PATH'] = str(ROOT/'tools/ffmpeg-9.0.2-essentials_build/bin')+os.pathsep+os.environ['PATH']
    info, duration = inspect_video(video)
    if info['format']['tags']['creation_time'] != audit['creation_time_utc']:
        raise ValueError('Video creation metadata changed')
    if float(info['format']['start_time']) != 0 or float(info['streams'][0]['start_time']) != 0:
        raise ValueError('This review expects zero media start PTS')
    jobs = []
    for number, row in enumerate(rows, 1):
        job = plan(row, audit['creation_time_utc'], duration)
        if abs(job['provisional_video_seconds']-audit['candidates'][number-1]['provisional_video_seconds']) > 1e-6:
            raise ValueError('Candidate position differs from preserved time audit')
        jobs.append(dict(candidate_number=number, file='candidate_%02d.mp4'%number,
                         estimated_utc=row['timing']['estimated_utc'], status='pending',
                         human_review='pending', mapping_verified=False, **job))
    OUT.mkdir(parents=True, exist_ok=False)
    source_stat = (video.stat().st_size, video.stat().st_mtime_ns)
    print('Hashing original video once; source is read-only.', flush=True)
    protected = {str(path):sha(path) for path in (video, candidates, audit_file)}
    manifest = dict(kind='provisional-camera-creation-time-review-only',
        assumed_video_zero_utc=audit['creation_time_utc'],
        assumption='container creation_time is first-frame UTC; not independently verified',
        conflicting_timecode=audit['camera_timecode'], total_sync_uncertainty_seconds=None,
        source_sha256=protected, formal_mapping_created=False, audio_detection_run=False,
        before_seconds=5, after_seconds=3, source_resolution_preserved=True, results=jobs)
    save(OUT/'manifest.json', manifest)
    cards = []
    try:
        for job in jobs:
            print('Extracting %02d/16 at provisional video %.6fs' %
                  (job['candidate_number'],job['provisional_video_seconds']), flush=True)
            output = OUT/job['file']
            extract_clip(video, output, job['start_seconds'], job['end_seconds'])
            clip_info, actual_duration = inspect_video(output)
            if abs(actual_duration-(job['end_seconds']-job['start_seconds'])) > .15:
                raise ValueError('Unexpected clip duration: '+job['file'])
            if not any(s['codec_type']=='audio' for s in clip_info['streams']):
                raise ValueError('Clip audio missing: '+job['file'])
            check = subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-i',str(output),
                                    '-map','0:v:0','-map','0:a:0','-f','null','-'],
                                   capture_output=True,text=True,encoding='utf-8')
            if check.returncode:
                raise RuntimeError(check.stderr)
            job.update(status='completed', full_decode_verified=True,
                       actual_duration_seconds=actual_duration, sha256=sha(output))
            save(OUT/'manifest.json', manifest)
            n=job['candidate_number']
            cards.append('<section><h2>候选%02d · 原视频 %.3f秒</h2>'
                '<p>暂定候选位置：本片第%.3f秒；前5秒、后3秒。是否哨声、位置和重复均待确认。</p>'
                '<video id="v%d" controls preload="none" src="%s"></video>'
                '<p><button onclick="document.getElementById(\'v%d\').currentTime=%.6f">跳到暂定候选位置</button>'
                ' <a href="%s">单独打开片段</a></p></section>' %
                (n,job['provisional_video_seconds'],job['provisional_marker_seconds'],n,
                 html.escape(job['file']),n,job['provisional_marker_seconds'],html.escape(job['file'])))
        if (video.stat().st_size, video.stat().st_mtime_ns) != source_stat:
            raise ValueError('Original video size/mtime changed')
        if any(sha(path)!=protected[str(path)] for path in (candidates,audit_file)):
            raise ValueError('Candidate or audit input changed')
        page='''<!doctype html><meta charset="utf-8"><title>16条板端候选复核</title>
<style>body{font:16px/1.6 system-ui;margin:24px auto;max-width:1100px;padding:0 16px;background:#f5f5f5;color:#222}section{background:white;padding:18px;margin:20px 0;border-radius:10px}video{width:100%;max-height:65vh}button{padding:8px}aside{background:#fff0c7;padding:16px}</style>
<h1>16条板端候选 · 待人工复核</h1>
<aside>暂按原视频第0秒为北京时间2026-10-15 16:24:43计算。创建时间与timecode含义尚未统一；板端是软件时间估计，总误差未知。本页不能作为正式同步验收。未重新检测录像音轨。</aside>
<p>请分别记录：真实哨声／非哨声／不确定，声音实际出现在片段第几秒，是否与其他候选同一声。偏移不自动回填，原候选保留。</p>'''+''.join(cards)
        (OUT/'review.html').write_text(page,encoding='utf-8')
        save(OUT/'human-review.json',dict(status='pending',items=[dict(candidate_number=j['candidate_number'],
            whistle='pending',actual_clip_seconds=None,same_whistle_as=None,position_confirmed=False,note='') for j in jobs]))
        save(OUT/'summary.json',dict(clips_completed=len(jobs),full_decode_verified=len(jobs),
            human_review='pending',formal_mapping_verified=False,source_sha256=protected[str(video)],
            source_size_mtime_unchanged=True,source_rehashed_after=False))
        print('DONE: '+str(OUT/'review.html'),flush=True)
    except BaseException as failure:
        save(OUT/'failure.json',dict(error=str(failure)))
        raise


if __name__ == '__main__':
    main()

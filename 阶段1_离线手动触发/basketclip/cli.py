import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from .media import extract_clip, inspect_video, require_tool
from .storage import load_events, load_json, save_json
from .timing import clip_window, event_position, make_alignment
from .buttons import rec_presses


def trigger_times(args, anchor_utc, anchor_monotonic):
    if args.board_button:
        for occurred in rec_presses():
            yield anchor_utc + timedelta(seconds=occurred - anchor_monotonic)
        return
    while True:
        try:
            action = input("> ").strip().lower()
        except EOFError:
            return
        if action == "q":
            return
        if action != "m":
            print("请输入 m 或 q")
            continue
        yield anchor_utc + timedelta(seconds=time.monotonic() - anchor_monotonic)


def record(args, on_saved=None):
    if not args.session.strip():
        raise ValueError("会话标识不能为空")
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    anchor_utc = datetime.now(timezone.utc)
    anchor_monotonic = time.monotonic()
    alignment_output = getattr(args, "alignment_output", None)
    video_seconds = getattr(args, "video_seconds", 0.0)
    alignment = None
    if alignment_output:
        alignment = make_alignment(args.session, anchor_utc.isoformat(), video_seconds)
        if Path(alignment_output).resolve() == path.resolve():
            raise ValueError("事件文件与对齐文件不能使用同一路径")
        if Path(alignment_output).exists():
            raise FileExistsError("对齐文件已存在，拒绝覆盖")
    with path.open("x", encoding="utf-8") as stream:
        if alignment is not None:
            save_json(alignment_output, alignment)
            print("模拟映射：记录开始对应录像 %.3f 秒；不代表真实音视频同步。" % video_seconds)
        if not args.board_button:
            print("输入 m 并回车记录事件；q 退出。时间是按回车后读取命令的时刻。")
        print("使用启动时 UTC 与单调时钟映射；这不能代替音视频同步标定。")
        event_id = 0
        for occurred in trigger_times(args, anchor_utc, anchor_monotonic):
            event_id += 1
            event = {"session_id": args.session, "event_id": event_id,
                     "event_type": "manual_trigger",
                     "event_time_utc": occurred.isoformat(timespec="microseconds").replace("+00:00", "Z")}
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            if on_saved is not None:
                on_saved(dict(event))
            print(json.dumps(event, ensure_ascii=False), flush=True)
            if alignment is not None:
                print("模拟录像位置：%.3f 秒" % event_position(event, alignment), flush=True)


def extract(args):
    video = Path(args.video).resolve()
    if not video.is_file():
        raise ValueError("视频文件不存在")
    events = load_events(args.events)
    alignment = load_json(args.alignment)
    _, duration = inspect_video(video)
    require_tool("ffmpeg")
    # Validate every event before creating outputs, avoiding partial input rejection.
    jobs = []
    for event in events:
        position = event_position(event, alignment)
        jobs.append((event, position, clip_window(position, args.before, args.after, duration)))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for event, position, window in jobs:
        result = {"event": event, "video_event_seconds": position}
        if window is None:
            result.update(status="skipped", reason="事件位于录像范围外")
        else:
            start, end = window
            filename = "event_%06d.mp4" % event["event_id"]
            result.update(start_seconds=start, end_seconds=end, file=filename)
            try:
                extract_clip(video, output / filename, start, end)
                result["status"] = "completed"
            except subprocess.CalledProcessError as error:
                result.update(status="failed", reason=error.stderr or str(error))
                # Remove only this attempt's incomplete derived clip.
                incomplete = output / filename
                if incomplete.exists():
                    incomplete.unlink()
        results.append(result)
        save_json(output / ("event_%06d.json" % event["event_id"]), result)
    save_json(output / "manifest.json", {
        "source_video": str(video), "duration_seconds": duration,
        "alignment": alignment, "before_seconds": args.before,
        "after_seconds": args.after, "results": results,
    })
    counts = {status: sum(item["status"] == status for item in results)
              for status in ("completed", "skipped", "failed")}
    print(json.dumps(counts, ensure_ascii=False))
    return 1 if counts["failed"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线事件与录像片段处理原型")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="检查 Python 与媒体工具")
    recorder = commands.add_parser("record", help="RK3588 端手动记录事件")
    recorder.add_argument("--session", required=True)
    recorder.add_argument("--output", required=True)
    recorder.add_argument("--board-button", action="store_true",
                          help="在 Linux 板卡读取 adc-rec-keys 的实体 REC 按键")
    recorder.add_argument("--alignment-output", help="可选：保存记录起点到录像位置的模拟映射")
    recorder.add_argument("--video-seconds", type=float, default=0.0,
                          help="模拟映射的录像起点秒数，默认 0，配合 --alignment-output")
    align = commands.add_parser("align", help="根据人工同步点保存时间映射")
    align.add_argument("--session", required=True)
    align.add_argument("--event-utc", required=True)
    align.add_argument("--video-seconds", required=True, type=float)
    align.add_argument("--end-event-utc")
    align.add_argument("--end-video-seconds", type=float)
    align.add_argument("--output", required=True)
    inspector = commands.add_parser("inspect", help="查看视频流与时长")
    inspector.add_argument("--video", required=True)
    cutter = commands.add_parser("extract", help="按事件批量截取视频")
    cutter.add_argument("--video", required=True)
    cutter.add_argument("--events", required=True)
    cutter.add_argument("--alignment", required=True)
    cutter.add_argument("--output", required=True)
    cutter.add_argument("--before", type=float, default=8.0)
    cutter.add_argument("--after", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            missing = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
            print("Python: " + sys.version.split()[0])
            for name in ("ffmpeg", "ffprobe"):
                print(name + ": " + (shutil.which(name) or "未安装或不在 PATH"))
            return 1 if missing else 0
        if args.command == "record":
            record(args)
        elif args.command == "align":
            save_json(args.output, make_alignment(args.session, args.event_utc,
                      args.video_seconds, args.end_event_utc, args.end_video_seconds))
            print("已保存时间映射：" + args.output)
        elif args.command == "inspect":
            info, _ = inspect_video(args.video)
            print(json.dumps(info, ensure_ascii=False, indent=2))
        elif args.command == "extract":
            return extract(args)
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        print("错误：" + str(detail), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中止", file=sys.stderr)
        return 130
    return 0

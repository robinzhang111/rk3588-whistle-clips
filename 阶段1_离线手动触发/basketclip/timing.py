"""Time mapping independent of video input and event transport."""

import math
from datetime import datetime, timezone


def utc_time(value):
    if not isinstance(value, str):
        raise ValueError("时间必须是带 UTC 时区的 ISO 8601 字符串")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("时间必须显式使用 UTC，例如 2026-10-10T12:00:00Z")
    return parsed.astimezone(timezone.utc)


def finite_number(value, name):
    if isinstance(value, bool):
        raise ValueError(name + " 必须是有限数值")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(name + " 必须是有限数值")
    return number


def make_alignment(session_id, event_utc, video_seconds, end_event_utc=None,
                   end_video_seconds=None):
    anchor = utc_time(event_utc)
    start = finite_number(video_seconds, "video_seconds")
    if not session_id or start < 0:
        raise ValueError("会话标识不能为空，同步点视频位置不能为负")
    if (end_event_utc is None) != (end_video_seconds is None):
        raise ValueError("第二同步点的时间和视频位置必须同时提供")
    scale = 1.0
    if end_event_utc is not None:
        elapsed = (utc_time(end_event_utc) - anchor).total_seconds()
        end = finite_number(end_video_seconds, "end_video_seconds")
        if elapsed <= 0 or end <= start:
            raise ValueError("第二同步点必须晚于第一同步点")
        scale = (end - start) / elapsed
    return {
        "session_id": session_id,
        "anchor_event_utc": anchor.isoformat().replace("+00:00", "Z"),
        "anchor_video_seconds": start,
        "video_seconds_per_event_second": scale,
        "end_event_utc": end_event_utc,
        "end_video_seconds": end_video_seconds,
    }


def event_position(event, alignment):
    if event["session_id"] != alignment["session_id"]:
        raise ValueError("事件与时间映射不属于同一会话")
    scale = finite_number(alignment["video_seconds_per_event_second"], "时间比例")
    anchor = finite_number(alignment["anchor_video_seconds"], "同步点位置")
    if scale <= 0 or anchor < 0:
        raise ValueError("时间比例必须为正，同步点位置不能为负")
    elapsed = (utc_time(event["event_time_utc"]) -
               utc_time(alignment["anchor_event_utc"])).total_seconds()
    return anchor + elapsed * scale


def clip_window(position, before, after, duration):
    position = finite_number(position, "事件位置")
    before = finite_number(before, "前置时长")
    after = finite_number(after, "后置时长")
    duration = finite_number(duration, "视频时长")
    if before < 0 or after < 0 or before + after <= 0 or duration <= 0:
        raise ValueError("片段时长参数无效")
    # Events outside this recording must not be silently mapped to its edge.
    if position < 0 or position >= duration:
        return None
    start, end = max(0.0, position - before), min(duration, position + after)
    return (start, end) if end > start else None

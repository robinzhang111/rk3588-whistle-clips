import json
from pathlib import Path

from .timing import utc_time


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def load_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def load_events(path):
    events = []
    seen = {}
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            event = json.loads(line)
            event_id = event["event_id"]
            if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 1:
                raise ValueError("第 %d 行的 event_id 必须是正整数" % line_no)
            if not isinstance(event["session_id"], str) or not event["session_id"]:
                raise ValueError("第 %d 行缺少有效会话标识" % line_no)
            if event["event_type"] not in ("manual_trigger", "whistle"):
                raise ValueError("第 %d 行的事件类型无效" % line_no)
            utc_time(event["event_time_utc"])
            key = (event["session_id"], event_id)
            if key in seen:
                if seen[key] != event:
                    raise ValueError("同一事件编号存在不同内容：%s" % (key,))
                continue
            seen[key] = event
            events.append(event)
    if not events:
        raise ValueError("事件文件为空")
    return events

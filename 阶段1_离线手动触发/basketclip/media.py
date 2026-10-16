import json
import shutil
import subprocess

from .timing import finite_number


def require_tool(name):
    found = shutil.which(name)
    if not found:
        raise ValueError("未找到 %s，请安装并加入 PATH" % name)
    return found


def inspect_video(path):
    command = [require_tool("ffprobe"), "-v", "error", "-show_format",
               "-show_streams", "-of", "json", str(path)]
    result = subprocess.run(command, check=True, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    info = json.loads(result.stdout)
    if not any(item.get("codec_type") == "video" for item in info["streams"]):
        raise ValueError("输入文件没有视频流")
    duration = finite_number(info["format"]["duration"], "视频时长")
    if duration <= 0:
        raise ValueError("视频时长必须为正")
    return info, duration


def extract_clip(video, output, start, end):
    # Re-encode for accurate seeking; do not overwrite source or existing outputs.
    command = [require_tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
               "-n", "-ss", "%.6f" % start, "-i", str(video),
               "-t", "%.6f" % (end - start), "-map", "0:v:0", "-map", "0:a:0?",
               "-c:v", "libx264", "-preset", "fast", "-crf", "20",
               "-c:a", "aac", "-movflags", "+faststart", str(output)]
    subprocess.run(command, check=True, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")

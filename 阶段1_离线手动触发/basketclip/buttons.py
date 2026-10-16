"""Read the running Linux board's adc-rec-keys device without GPIO changes."""

from pathlib import Path
import struct
import sys


INPUT_EVENT = struct.Struct("@llHHi")
REC_KEY_CODE = 250


def rec_press_timestamp(packet):
    seconds, microseconds, event_type, code, value = INPUT_EVENT.unpack(packet)
    if event_type == 1 and code == REC_KEY_CODE and value == 1:
        return seconds + microseconds / 1000000.0
    return None


def find_rec_device():
    for entry in sorted(Path("/sys/class/input").glob("event*")):
        if (entry / "device/name").read_text().strip() == "adc-rec-keys":
            return Path("/dev/input") / entry.name
    raise ValueError("未找到 adc-rec-keys；不自动改用电源或其他按键")


def rec_presses():
    if sys.platform != "linux":
        raise ValueError("实体 REC 按键仅在 Linux 板卡上使用")
    import fcntl
    import time

    device = find_rec_device()
    # These ioctl values are from Linux input.h (_IOW with a 4-byte int).
    eviosclockid = 0x400445A0
    eviocgrab = 0x40044590
    with device.open("rb", buffering=0) as stream:
        fcntl.ioctl(stream, eviosclockid, struct.pack("i", time.CLOCK_MONOTONIC))
        fcntl.ioctl(stream, eviocgrab, 1)
        print("已监听 %s（adc-rec-keys），短按 REC 记录，Ctrl+C 结束。" % device, flush=True)
        try:
            while True:
                packet = stream.read(INPUT_EVENT.size)
                if len(packet) != INPUT_EVENT.size:
                    raise ValueError("按键输入流中断或事件长度异常")
                occurred = rec_press_timestamp(packet)
                if occurred is not None:
                    yield occurred
        finally:
            fcntl.ioctl(stream, eviocgrab, 0)

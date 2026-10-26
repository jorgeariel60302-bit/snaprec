"""冒烟测试：真实录几秒屏幕（中间暂停一次），检查生成的 MP4 是否正常。

用法：python tests/smoke_test.py
Linux 下需要有图形环境，例如：xvfb-run -s "-screen 0 1280x720x24" python tests/smoke_test.py
"""

import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import snaprec  # noqa: E402


def probe(ffmpeg, path):
    out = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                         capture_output=True).stderr.decode("utf-8", "replace")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
    return duration, out


def main():
    snaprec.enable_dpi_awareness()
    ffmpeg = snaprec.find_ffmpeg()
    assert ffmpeg, "找不到 ffmpeg"
    print("ffmpeg:", ffmpeg)
    size = (1920, 1080)
    if not snaprec.IS_WIN:  # Linux 测试环境：用 tkinter 取虚拟屏幕的真实尺寸
        import tkinter
        root = tkinter.Tk()
        size = (root.winfo_screenwidth(), root.winfo_screenheight())
        root.destroy()
    screens = snaprec.list_screens(size)
    print("屏幕:", screens)
    print("麦克风:", snaprec.list_mics(ffmpeg))

    quality = next(q[2] for q in snaprec.QUALITIES if q[0] == snaprec.DEFAULT_QUALITY)
    out = Path(tempfile.mkdtemp()) / "测试 录屏's.mp4"  # 带中文、空格和引号的文件名
    rec = snaprec.Recorder(ffmpeg, screens[0], quality, None, out)
    rec.start_segment()
    time.sleep(3)
    assert rec.is_running(), "录制进程提前退出：\n" + rec.log_tail()
    rec.stop_segment()  # 暂停
    time.sleep(1)
    rec.start_segment()  # 继续
    time.sleep(2)
    path = rec.finish()

    assert path.is_file(), "没有生成文件"
    duration, info = probe(ffmpeg, path)
    print(info)
    print(f"结果: {path}  大小 {snaprec.human_size(path.stat().st_size)}  时长 {duration:.1f}s")
    assert "h264" in info and "yuv420p" in info, "编码格式不对"
    assert 3.5 <= duration <= 7, f"时长异常: {duration}"
    print("OK")


if __name__ == "__main__":
    main()

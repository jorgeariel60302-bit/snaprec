#!/usr/bin/env python3
"""SnapRec —— 一个简单的录屏工具。

录下电脑屏幕操作（可选麦克风解说），保存为体积小、文字清晰的 MP4，
方便通过微信等发送给同事。录制和压缩由 ffmpeg 完成。
"""

import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

APP_NAME = "SnapRec 录屏"
IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW：调用 ffmpeg 时不弹黑框

# 画质档位：crf 越大文件越小（画面越糊）；fps 是每秒帧数；max_w 是最大宽度（超过就缩小）
QUALITIES = [
    ("清晰", "文字最锐利，文件稍大", dict(crf=23, fps=20, max_w=None)),
    ("标准（推荐）", "文字清楚，文件小，适合微信发送", dict(crf=28, fps=15, max_w=1920)),
    ("省空间", "文件最小，适合很长的录屏", dict(crf=32, fps=10, max_w=1920)),
]
DEFAULT_QUALITY = "标准（推荐）"
ALL_SCREENS = "全部屏幕"


# ---------------------------------------------------------------- 系统相关

def enable_dpi_awareness():
    """让程序拿到真实像素尺寸，避免在 125%/150% 缩放的屏幕上只录到一部分。"""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def find_ffmpeg():
    """依次在：环境变量、程序所在文件夹、exe 内置、PATH 中查找 ffmpeg。"""
    exe = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    candidates = []
    if os.environ.get("SNAPREC_FFMPEG"):
        candidates.append(Path(os.environ["SNAPREC_FFMPEG"]))
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / exe)
        if hasattr(sys, "_MEIPASS"):  # 单文件 exe 运行时解压出的内置 ffmpeg
            candidates.append(Path(sys._MEIPASS) / exe)
    candidates.append(Path(__file__).resolve().parent / exe)
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("ffmpeg")


def list_screens(fallback_size=(1920, 1080)):
    """返回屏幕列表，每项是 dict(x, y, w, h, primary)，主屏幕排在最前。"""
    screens = []
    if IS_WIN:
        try:
            user32 = ctypes.windll.user32

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                            ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

            proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.POINTER(RECT), ctypes.c_void_p)

            def callback(hmon, _hdc, _rect, _data):
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(info)):
                    r = info.rcMonitor
                    screens.append(dict(x=r.left, y=r.top, w=r.right - r.left,
                                        h=r.bottom - r.top, primary=bool(info.dwFlags & 1)))
                return 1

            user32.EnumDisplayMonitors(None, None, proc_type(callback), 0)
        except Exception:
            screens = []
    if not screens:
        w, h = fallback_size
        screens = [dict(x=0, y=0, w=w, h=h, primary=True)]
    screens.sort(key=lambda s: (not s["primary"], s["x"], s["y"]))
    return screens


def screen_label(index, screen):
    tag = "（主屏幕）" if screen["primary"] else ""
    return f"屏幕{index + 1}{tag}  {screen['w']}×{screen['h']}"


def list_mics(ffmpeg):
    """列出可用的麦克风（仅 Windows）。"""
    if not IS_WIN or not ffmpeg:
        return []
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, timeout=15, creationflags=NO_WINDOW)
    except Exception:
        return []
    mics, section = [], None
    for line in result.stderr.decode("utf-8", "replace").splitlines():
        if "DirectShow video devices" in line:
            section = "video"
            continue
        if "DirectShow audio devices" in line:
            section = "audio"
            continue
        if "Alternative name" in line:
            continue
        m = re.search(r'"(.+)"(?:\s*\((audio|video|none)[^)]*\))?\s*$', line)
        if m and (m.group(2) or section) == "audio" and m.group(1) not in mics:
            mics.append(m.group(1))
    return mics


def open_folder(path):
    path = Path(path)
    try:
        if IS_WIN:
            if path.is_file():
                subprocess.Popen(["explorer", f"/select,{path}"])
            else:
                os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path if path.is_dir() else path.parent)])
        else:
            subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])
    except Exception:
        pass


def open_file(path):
    try:
        if IS_WIN:
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def exclude_from_capture(window):
    """让录制时的小控制条不出现在视频里（Windows 10 2004 及以上有效）。"""
    if not IS_WIN:
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT
        user32.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
    except Exception:
        pass


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_time(seconds):
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------- 录制

def build_record_cmd(ffmpeg, screen, quality, mic, out_path):
    """screen 为 None 表示录制全部屏幕。"""
    q = quality
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "warning", "-y"]
    if IS_WIN:
        cmd += ["-f", "gdigrab", "-framerate", str(q["fps"]),
                "-draw_mouse", "1"]
        if screen is not None:
            cmd += ["-offset_x", str(screen["x"]), "-offset_y", str(screen["y"]),
                    "-video_size", f"{screen['w']}x{screen['h']}"]
        cmd += ["-i", "desktop"]
    else:
        screen = screen or list_screens()[0]
        display = os.environ.get("DISPLAY", ":0")
        cmd += ["-f", "x11grab", "-framerate", str(q["fps"]),
                "-draw_mouse", "1", "-video_size", f"{screen['w']}x{screen['h']}",
                "-i", f"{display}+{screen['x']},{screen['y']}"]
    if mic:
        if IS_WIN:
            cmd += ["-f", "dshow", "-i", f"audio={mic}"]
        else:
            cmd += ["-f", "pulse", "-i", mic]

    # 宽高必须是偶数；超过 max_w 的高分屏等比缩小，既省体积又不影响看清文字
    if q["max_w"]:
        vf = f"scale='trunc(min(iw,{q['max_w']})/2)*2':-2:flags=lanczos"
    else:
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", str(q["crf"]),
            "-pix_fmt", "yuv420p"]
    if mic:
        cmd += ["-c:a", "aac", "-b:a", "96k"]
    cmd += ["-movflags", "+faststart", str(out_path)]
    return cmd


class Recorder:
    """管理一次录制。暂停 = 结束当前片段，继续 = 开始新片段，停止时把片段拼接起来。"""

    def __init__(self, ffmpeg, screen, quality, mic, out_path):
        self.ffmpeg = ffmpeg
        self.screen = screen
        self.quality = quality
        self.mic = mic
        self.out_path = Path(out_path)
        self.tmpdir = Path(tempfile.mkdtemp(prefix="snaprec_"))
        self.log_path = self.tmpdir / "ffmpeg.log"
        self.segments = []
        self.proc = None
        self._log = None

    def start_segment(self):
        seg = self.tmpdir / f"part{len(self.segments) + 1:03d}.mp4"
        cmd = build_record_cmd(self.ffmpeg, self.screen, self.quality, self.mic, seg)
        self._log = open(self.log_path, "ab")
        self._log.write(("\n> " + " ".join(cmd) + "\n").encode("utf-8", "replace"))
        self._log.flush()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=self._log, creationflags=NO_WINDOW)
        self.segments.append(seg)

    def stop_segment(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.stdin.write(b"q")  # 让 ffmpeg 正常收尾，否则 MP4 文件会损坏
            proc.stdin.flush()
        except OSError:
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if self._log:
            self._log.close()
            self._log = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def log_tail(self, lines=15):
        try:
            text = self.log_path.read_text("utf-8", "replace")
        except OSError:
            return ""
        return "\n".join(text.strip().splitlines()[-lines:])

    def finish(self):
        """停止录制并生成最终的 MP4，返回文件路径。"""
        self.stop_segment()
        try:
            segs = [s for s in self.segments if s.is_file() and s.stat().st_size > 0]
            if not segs:
                raise RuntimeError("没有录到任何画面。\n\n" + self.log_tail())
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            if len(segs) == 1:
                shutil.move(str(segs[0]), str(self.out_path))
            else:
                list_file = self.tmpdir / "list.txt"
                list_file.write_text(
                    "".join("file '{}'\n".format(str(s).replace("'", "'\\''")) for s in segs),
                    encoding="utf-8")
                result = subprocess.run(
                    [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                     "-safe", "0", "-i", str(list_file), "-c", "copy",
                     "-movflags", "+faststart", str(self.out_path)],
                    capture_output=True, creationflags=NO_WINDOW)
                if result.returncode != 0 or not self.out_path.is_file():
                    raise RuntimeError("合并视频片段失败。\n\n"
                                       + result.stderr.decode("utf-8", "replace")[-1500:])
            return self.out_path
        finally:
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def discard(self):
        self.stop_segment()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ---------------------------------------------------------------- 设置

def settings_path():
    base = os.environ.get("APPDATA") if IS_WIN else None
    base = Path(base) if base else Path.home() / ".config"
    return base / "SnapRec" / "settings.json"


def default_save_dir():
    videos = Path.home() / "Videos"
    return str((videos if videos.is_dir() else Path.home()) / "SnapRec录屏")


def load_settings():
    try:
        return json.loads(settings_path().read_text("utf-8"))
    except Exception:
        return {}


def save_settings(data):
    try:
        p = settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- 界面

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class ControlBar(tk.Toplevel):
        """录制时浮在屏幕顶部的小控制条（不会被录进视频）。"""

        def __init__(self, master, screen, on_pause, on_stop):
            super().__init__(master)
            self.overrideredirect(True)
            self.attributes("-topmost", True)
            self.configure(bg="#222222")
            frame = tk.Frame(self, bg="#222222", padx=10, pady=6)
            frame.pack()
            self.dot = tk.Label(frame, text="●", fg="#ff4040", bg="#222222",
                                font=("", 14, "bold"))
            self.dot.pack(side="left")
            self.time_label = tk.Label(frame, text="准备", fg="white", bg="#222222",
                                       font=("", 13, "bold"), width=8)
            self.time_label.pack(side="left", padx=(4, 10))
            btn = dict(relief="flat", bd=0, padx=12, pady=4, font=("", 11),
                       cursor="hand2", activeforeground="white")
            self.pause_btn = tk.Button(frame, text="暂停", command=on_pause, bg="#444444",
                                       fg="white", activebackground="#555555", **btn)
            self.pause_btn.pack(side="left", padx=3)
            self.stop_btn = tk.Button(frame, text="■ 停止", command=on_stop, bg="#d93636",
                                      fg="white", activebackground="#e85050", **btn)
            self.stop_btn.pack(side="left", padx=3)
            for w in (self, frame, self.dot, self.time_label):
                w.bind("<ButtonPress-1>", self._drag_start)
                w.bind("<B1-Motion>", self._drag_move)
            self.update_idletasks()
            sx, sy, sw = (screen["x"], screen["y"], screen["w"]) if screen else (0, 0, 0)
            sw = sw or self.winfo_screenwidth()
            x = sx + (sw - self.winfo_reqwidth()) // 2
            self.geometry(f"+{x}+{sy + 8}")
            self.update_idletasks()
            exclude_from_capture(self)
            self._drag = (0, 0)

        def _drag_start(self, e):
            self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

        def _drag_move(self, e):
            self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

        def set_buttons(self, enabled):
            state = "normal" if enabled else "disabled"
            self.pause_btn.configure(state=state)
            self.stop_btn.configure(state=state)

    class App:
        def __init__(self, root):
            self.root = root
            self.ffmpeg = find_ffmpeg()
            self.settings = load_settings()
            self.recorder = None
            self.bar = None
            self.paused = False
            self.elapsed = 0.0
            self.seg_started = None
            self.countdown = 0
            self.last_file = None
            self.results = queue.Queue()

            root.title(APP_NAME)
            root.resizable(False, False)
            root.protocol("WM_DELETE_WINDOW", self.on_close)
            if IS_WIN:
                for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                    try:
                        tk.font.nametofont(name).configure(family="Microsoft YaHei UI")
                    except Exception:
                        pass

            pad = dict(padx=14, pady=(10, 0))
            main = ttk.Frame(root, padding=(4, 4, 4, 14))
            main.pack(fill="both", expand=True)

            # 录制范围
            ttk.Label(main, text="录制范围").pack(anchor="w", **pad)
            self.screens = list_screens((root.winfo_screenwidth(), root.winfo_screenheight()))
            choices = [screen_label(i, s) for i, s in enumerate(self.screens)]
            if IS_WIN and len(self.screens) > 1:
                choices.append(ALL_SCREENS)
            self.screen_var = tk.StringVar(value=choices[0])
            if self.settings.get("screen") in choices:
                self.screen_var.set(self.settings["screen"])
            ttk.Combobox(main, textvariable=self.screen_var, values=choices, state="readonly",
                         width=36).pack(anchor="w", padx=14, pady=(4, 0))
            self.screen_choices = choices

            # 画质
            ttk.Label(main, text="画质").pack(anchor="w", **pad)
            self.quality_var = tk.StringVar(value=self.settings.get("quality", DEFAULT_QUALITY))
            if self.quality_var.get() not in [q[0] for q in QUALITIES]:
                self.quality_var.set(DEFAULT_QUALITY)
            for name, desc, _ in QUALITIES:
                ttk.Radiobutton(main, text=f"{name} —— {desc}", value=name,
                                variable=self.quality_var).pack(anchor="w", padx=24, pady=1)

            # 麦克风
            self.mic_on = tk.BooleanVar(value=False)
            self.mic_var = tk.StringVar()
            mic_row = ttk.Frame(main)
            mic_row.pack(anchor="w", fill="x", **pad)
            self.mic_check = ttk.Checkbutton(mic_row, text="同时录麦克风（边操作边讲解）",
                                             variable=self.mic_on, command=self.update_mic_state)
            self.mic_check.pack(anchor="w")
            self.mic_box = ttk.Combobox(main, textvariable=self.mic_var, state="disabled",
                                        width=36, values=["正在查找麦克风…"])
            self.mic_box.pack(anchor="w", padx=24, pady=(4, 0))
            self.mic_var.set("正在查找麦克风…")
            self.mics = []
            if not IS_WIN:
                self.mic_check.configure(state="disabled")
                self.mic_var.set("当前系统暂不支持")

            # 保存位置
            ttk.Label(main, text="保存到").pack(anchor="w", **pad)
            dir_row = ttk.Frame(main)
            dir_row.pack(anchor="w", fill="x", padx=14, pady=(4, 0))
            self.dir_var = tk.StringVar(value=self.settings.get("save_dir") or default_save_dir())
            ttk.Entry(dir_row, textvariable=self.dir_var, width=30).pack(side="left", fill="x",
                                                                          expand=True)
            ttk.Button(dir_row, text="更改…", command=self.choose_dir).pack(side="left", padx=(6, 0))

            # 开始按钮
            style = ttk.Style()
            style.configure("Big.TButton", font=("Microsoft YaHei UI" if IS_WIN else "", 13, "bold"),
                            padding=(10, 10))
            self.start_btn = ttk.Button(main, text="●  开始录制", style="Big.TButton",
                                        command=self.start)
            self.start_btn.pack(fill="x", padx=14, pady=(16, 0))

            # 状态与结果
            self.status = ttk.Label(main, text="录制开始后，本窗口会隐藏，屏幕顶部会出现停止按钮。",
                                    foreground="#666666", wraplength=360, justify="left")
            self.status.pack(anchor="w", padx=14, pady=(10, 0))
            result_row = ttk.Frame(main)
            result_row.pack(anchor="w", padx=14, pady=(6, 0))
            self.play_btn = ttk.Button(result_row, text="▶ 播放", state="disabled",
                                       command=lambda: open_file(self.last_file))
            self.play_btn.pack(side="left")
            self.folder_btn = ttk.Button(result_row, text="打开所在文件夹",
                                         command=self.open_result_folder)
            self.folder_btn.pack(side="left", padx=(6, 0))

            if not self.ffmpeg:
                self.start_btn.configure(state="disabled")
                self.status.configure(
                    foreground="#c00000",
                    text="找不到录制组件 ffmpeg，请重新下载 SnapRec.exe。")
            elif IS_WIN:
                threading.Thread(target=self._load_mics, daemon=True).start()
            self.root.after(200, self.poll_results)

        # ---- 麦克风
        def _load_mics(self):
            self.results.put(("mics", list_mics(self.ffmpeg)))

        def apply_mics(self, mics):
            self.mics = mics
            if mics:
                self.mic_box.configure(values=mics)
                saved = self.settings.get("mic")
                self.mic_var.set(saved if saved in mics else mics[0])
                self.mic_on.set(bool(self.settings.get("mic_on")) and saved in mics)
            else:
                self.mic_var.set("未检测到麦克风")
                self.mic_on.set(False)
                self.mic_check.configure(state="disabled")
            self.update_mic_state()

        def update_mic_state(self):
            self.mic_box.configure(state="readonly" if (self.mic_on.get() and self.mics)
                                   else "disabled")

        # ---- 其他操作
        def choose_dir(self):
            d = filedialog.askdirectory(initialdir=self.dir_var.get() or str(Path.home()))
            if d:
                self.dir_var.set(d)

        def open_result_folder(self):
            target = self.last_file if self.last_file and Path(self.last_file).exists() \
                else self.dir_var.get()
            if not Path(target).exists():
                Path(target).mkdir(parents=True, exist_ok=True)
            open_folder(target)

        def selected_screen(self):
            choice = self.screen_var.get()
            if choice == ALL_SCREENS:
                return None
            idx = self.screen_choices.index(choice) if choice in self.screen_choices else 0
            return self.screens[idx]

        def set_status(self, text, color="#666666"):
            self.status.configure(text=text, foreground=color)

        # ---- 录制流程
        def start(self):
            save_dir = Path(self.dir_var.get().strip() or default_save_dir())
            try:
                save_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                messagebox.showerror(APP_NAME, f"无法使用这个保存位置：\n{save_dir}\n\n{e}")
                return
            quality = next(q[2] for q in QUALITIES if q[0] == self.quality_var.get())
            mic = self.mic_var.get() if (self.mic_on.get() and self.mics) else None
            name = datetime.now().strftime("录屏_%Y-%m-%d_%H-%M-%S.mp4")
            self.settings.update(screen=self.screen_var.get(), quality=self.quality_var.get(),
                                 mic=self.mic_var.get() if self.mics else None,
                                 mic_on=self.mic_on.get(), save_dir=str(save_dir))
            save_settings(self.settings)

            screen = self.selected_screen()
            self.recorder = Recorder(self.ffmpeg, screen, quality, mic, save_dir / name)
            self.paused = False
            self.elapsed = 0.0
            self.seg_started = None
            self.root.withdraw()
            self.bar = ControlBar(self.root, screen, self.toggle_pause, self.stop)
            self.bar.set_buttons(False)
            self.countdown = 3
            self.tick_countdown()

        def tick_countdown(self):
            if self.countdown > 0:
                self.bar.time_label.configure(text=f"{self.countdown} 秒后开始")
                self.countdown -= 1
                self.root.after(1000, self.tick_countdown)
                return
            try:
                self.recorder.start_segment()
            except Exception as e:
                self.fail(f"无法启动录制：{e}")
                return
            self.seg_started = time.monotonic()
            self.bar.set_buttons(True)
            self.tick()

        def tick(self):
            if not self.bar or not self.recorder:
                return
            if not self.paused and not self.recorder.is_running():
                self.fail("录制意外停止了。\n\n" + self.recorder.log_tail())
                return
            total = self.elapsed + (time.monotonic() - self.seg_started if not self.paused else 0)
            self.bar.time_label.configure(text=fmt_time(total))
            self.bar.dot.configure(fg="#ffb000" if self.paused else
                                   ("#ff4040" if int(total * 2) % 2 == 0 else "#222222"))
            self.root.after(250, self.tick)

        def toggle_pause(self):
            if not self.recorder:
                return
            if self.paused:
                try:
                    self.recorder.start_segment()
                except Exception as e:
                    self.fail(f"无法继续录制：{e}")
                    return
                self.seg_started = time.monotonic()
                self.paused = False
                self.bar.pause_btn.configure(text="暂停")
            else:
                self.bar.set_buttons(False)
                self.bar.update_idletasks()
                self.elapsed += time.monotonic() - self.seg_started
                self.recorder.stop_segment()
                self.paused = True
                self.bar.pause_btn.configure(text="继续")
                self.bar.set_buttons(True)

        def stop(self):
            if not self.recorder or not self.bar:
                return
            if not self.paused and self.seg_started:
                self.elapsed += time.monotonic() - self.seg_started
            self.paused = True
            self.bar.set_buttons(False)
            self.bar.time_label.configure(text="保存中…")
            recorder, duration = self.recorder, self.elapsed
            self.recorder = None

            def work():
                try:
                    self.results.put(("done", recorder.finish(), duration))
                except Exception as e:
                    self.results.put(("error", str(e)))

            threading.Thread(target=work, daemon=True).start()

        def poll_results(self):
            try:
                while True:
                    item = self.results.get_nowait()
                    if item[0] == "mics":
                        self.apply_mics(item[1])
                    elif item[0] == "done":
                        self.on_saved(item[1], item[2])
                    elif item[0] == "error":
                        self.close_bar()
                        self.set_status("保存失败。", "#c00000")
                        messagebox.showerror(APP_NAME, item[1])
            except queue.Empty:
                pass
            self.root.after(200, self.poll_results)

        def on_saved(self, path, duration):
            self.close_bar()
            self.last_file = str(path)
            size = Path(path).stat().st_size
            self.set_status(f"已保存：{Path(path).name}\n时长 {fmt_time(duration)}，"
                            f"大小 {human_size(size)}", "#1a7f37")
            self.play_btn.configure(state="normal")

        def fail(self, message):
            if self.recorder:
                self.recorder.discard()
                self.recorder = None
            self.close_bar()
            self.set_status("录制失败。", "#c00000")
            messagebox.showerror(APP_NAME, message)

        def close_bar(self):
            if self.bar:
                self.bar.destroy()
                self.bar = None
            self.root.deiconify()
            self.root.lift()

        def on_close(self):
            if self.bar:
                if not messagebox.askyesno(APP_NAME, "正在录制，要停止并保存后再退出吗？"):
                    return
                if self.recorder:
                    recorder, self.recorder = self.recorder, None
                    try:
                        recorder.finish()
                    except Exception:
                        pass
            self.root.destroy()

    import tkinter.font  # noqa: F401  (App 中设置字体时使用)
    root = tk.Tk()
    App(root)
    root.mainloop()


def selftest():
    """录 2 秒主屏幕，返回结果文字（供打包后自动检查用：SnapRec.exe --selftest 结果.txt）。"""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return "FAIL 找不到 ffmpeg"
    quality = next(q[2] for q in QUALITIES if q[0] == DEFAULT_QUALITY)
    out = Path(tempfile.mkdtemp(prefix="snaprec_selftest_")) / "selftest.mp4"
    rec = Recorder(ffmpeg, list_screens()[0], quality, None, out)
    try:
        rec.start_segment()
        time.sleep(2)
        if not rec.is_running():
            return "FAIL 录制进程提前退出\n" + rec.log_tail()
        path = rec.finish()
        return f"OK ffmpeg={ffmpeg} size={path.stat().st_size}"
    except Exception as e:
        return f"FAIL {e}"
    finally:
        shutil.rmtree(out.parent, ignore_errors=True)


def main():
    enable_dpi_awareness()
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        Path(sys.argv[2]).write_text(selftest(), encoding="utf-8")
        return
    run_gui()


if __name__ == "__main__":
    main()

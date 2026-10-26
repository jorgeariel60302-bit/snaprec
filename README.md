# SnapRec 录屏

一个简单的 Windows 录屏小工具：把电脑上的操作（比如 ArcGIS 的操作步骤）录下来发给同事看。

- **简单**：选好屏幕 → 开始录制 → 停止，就这三步；支持暂停/继续，可选录麦克风讲解。
- **常见格式**：MP4（H.264），微信、手机、电脑都能直接播放。
- **文件小**：针对屏幕操作画面优化，“标准”画质下即使地图一直在平移，每分钟也只有约 2–3 MB；
  画面静止的操作会更小。
- **字清楚**：保持原始分辨率（2K/4K 屏缩到 1920 宽），9 号小字也能看清。
- 录制时屏幕顶部的控制条不会被录进视频。

## 下载使用

只有一个 `SnapRec.exe`，不用安装，双击就能运行（录制组件 ffmpeg 已经内置在里面）。

- 下载：[SnapRec.exe](https://github.com/jorgeariel60302-bit/snaprec/releases/download/snaprec-latest/SnapRec.exe)
- 双击 `SnapRec.exe` 即可。每次启动要先解压内置组件，会等 2–3 秒。

详细说明见 [docs/使用说明.txt](docs/使用说明.txt)。

## 画质档位

| 档位 | 帧率 | 适用 |
|---|---|---|
| 清晰 | 20 帧 | 字特别小、要求最清楚 |
| 标准（推荐） | 15 帧 | 日常操作演示，适合微信发送 |
| 省空间 | 10 帧 | 很长的录屏 |

## 开发

- 代码只有一个文件 `snaprec.py`，只用 Python 标准库（tkinter），录制和编码由 ffmpeg 完成。
- 本地运行：安装 Python 3 和 ffmpeg，执行 `python snaprec.py`。
- 测试：`python tests/smoke_test.py`（会真实录几秒屏幕）。
- 图标：企鹅系列图标（和 [snaptool](https://github.com/jorgeariel60302-bit/snaptool) 是一套），
  由 `python tools/make_icons.py 输出目录` 生成，改颜色或造型后重新生成即可。
- 每次推送代码，GitHub Actions 会自动在 Windows 上测试并打包；合并到 main 后会自动更新上面的下载链接。

打包内置的 ffmpeg 来自 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 的稳定版（GPL 许可）。

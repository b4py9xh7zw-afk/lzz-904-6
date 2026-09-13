# 课程录屏画质优化器

老师上传录屏片段后，在页面上调节 **文字锐化 / 背景降噪 / 音画片段长度 / 输出分辨率**，
后端调用 ffmpeg 生成预览视频，并提供可下载的 **复现命令行（.sh）** 与 **参数报告（.md）**。

## 快速开始

```bash
pip install flask          # 唯一依赖
python3 app.py             # 默认 http://127.0.0.1:5000
```

## ffmpeg 检测

服务启动与每次请求前都会按以下顺序查找 `ffmpeg` / `ffprobe`：

1. 环境变量 `FFMPEG_PATH` / `FFPROBE_PATH`
2. 系统 `PATH`
3. 项目同级 `tools/` 目录（可放静态构建）

检测失败时，`GET /api/health` 与处理接口返回 **503 + 分平台安装指引**
（apt / brew / choco / 静态构建 + 环境变量），页面顶部同步显示红色告警与「重新检测」按钮。

## 参数 → ffmpeg 映射

| 页面参数 | ffmpeg 实现 |
| --- | --- |
| 文字锐化 0–3 | `unsharp`（仅亮度平面，避免放大色度噪点） |
| 背景降噪 0–10 | `hqdn3d`（空间+时间域，色度按比例联动） |
| 音画片段长度（秒，0=整段） | `-t` |
| 输出分辨率 | `scale=WxH:force_original_aspect_ratio=decrease,pad=WxH`（等比缩放补黑边） |

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | ffmpeg 状态；失败返回 503 与修复指引 |
| POST | `/api/upload` | 表单字段 `file`，返回 `file_id` 与媒体元数据 |
| POST | `/api/process` | JSON `{file_id, sharpen, denoise, duration, resolution}`，返回预览/下载地址 |
| GET | `/outputs/<job>/preview.mp4` | 预览视频 |
| GET | `/outputs/<job>/command.sh` | 可复现命令行（附件下载） |
| GET | `/outputs/<job>/report.md` | 参数报告（附件下载） |

## 环境变量

- `FFMPEG_PATH` / `FFPROBE_PATH`：显式指定二进制路径
- `MAX_UPLOAD_MB`：上传大小上限（默认 1024）
- `PROCESS_TIMEOUT`：单次处理超时秒数（默认 600）

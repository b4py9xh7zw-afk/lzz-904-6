# -*- coding: utf-8 -*-
"""课程录屏画质优化器 - 后端服务

功能:
  - 检测 ffmpeg / ffprobe 路径, 失败时返回可操作的安装指引
  - 接收录屏上传, 读取媒体元数据
  - 按参数(文字锐化 / 背景降噪 / 片段长度 / 输出分辨率)调用 ffmpeg 生成预览
  - 提供可下载的复现命令行(.sh)与参数报告(.md)
"""
import glob
import json
import os
import shlex
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from flask import (Flask, jsonify, render_template, request,
                   send_from_directory)
from werkzeug.utils import secure_filename

APP_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = APP_DIR / "uploads"
OUTPUT_DIR = APP_DIR / "outputs"
for d in (UPLOAD_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

ALLOWED_EXT = {"mp4", "mov", "mkv", "avi", "webm", "ts", "flv", "m4v"}
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "1024"))
PROCESS_TIMEOUT = int(os.environ.get("PROCESS_TIMEOUT", "600"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# ---------------------------------------------------------------------------
# ffmpeg / ffprobe 检测
# ---------------------------------------------------------------------------

def find_tool(name):
    """按优先级查找可执行文件: 环境变量 -> PATH -> 本地 tools 目录。"""
    env = os.environ.get(f"{name.upper()}_PATH")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    found = shutil.which(name)
    if found:
        return found
    for base in (APP_DIR / "tools", APP_DIR.parent / "tools"):
        for p in glob.glob(str(base / "**" / name), recursive=True):
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    return None


def tool_version(path):
    try:
        r = subprocess.run([path, "-version"], capture_output=True,
                           text=True, timeout=10)
        if r.returncode == 0 and r.stdout:
            return r.stdout.splitlines()[0].strip()
    except Exception:
        pass
    return None


INSTALL_GUIDE = [
    "Ubuntu / Debian:  sudo apt-get update && sudo apt-get install -y ffmpeg",
    "macOS (Homebrew):  brew install ffmpeg",
    "Windows:  choco install ffmpeg  或从 https://www.gyan.dev/ffmpeg/builds/ 下载后将 bin 目录加入 PATH",
    "任意系统: 下载静态构建包(https://johnvansickle.com/ffmpeg/ 或 https://ffmpeg.org/download.html), "
    "解压后设置环境变量  export FFMPEG_PATH=/路径/到/ffmpeg  并重启本服务",
    "也可以把 ffmpeg / ffprobe 可执行文件放到项目同级 tools/ 目录下, 然后点击页面上的「重新检测」",
]


def ffmpeg_status():
    ff, fp = find_tool("ffmpeg"), find_tool("ffprobe")
    status = {
        "ok": bool(ff and fp),
        "ffmpeg_path": ff,
        "ffprobe_path": fp,
        "ffmpeg_version": tool_version(ff) if ff else None,
    }
    if not status["ok"]:
        missing = [n for n, p in (("ffmpeg", ff), ("ffprobe", fp)) if not p]
        status["error"] = "未找到可执行文件: " + ", ".join(missing)
        status["instructions"] = INSTALL_GUIDE
    return status


def require_ffmpeg():
    """返回 (ffmpeg, ffprobe, error_response)。失败时 error_response 为 503。"""
    st = ffmpeg_status()
    if not st["ok"]:
        return None, None, (jsonify(st), 503)
    return st["ffmpeg_path"], st["ffprobe_path"], None


# ---------------------------------------------------------------------------
# 参数 -> ffmpeg 滤镜/命令
# ---------------------------------------------------------------------------

RESOLUTIONS = {
    "1080p": (1920, 1080),
    "720p": (1280, 720),
    "480p": (854, 480),
}


def build_filter_chain(sharpen, denoise, width, height):
    """构造 -vf 滤镜链。

    sharpen : 0~3   文字锐化(unsharp 亮度锐化量)
    denoise : 0~10  背景降噪(hqdn3d 亮度空间降噪强度)
    width/height: 输出分辨率, None 表示保持原始
    """
    parts = []
    if sharpen > 0:
        parts.append(
            "unsharp=luma_msize_x=5:luma_msize_y=5:"
            f"luma_amount={sharpen:.2f}:chroma_msize_x=5:"
            "chroma_msize_y=5:chroma_amount=0"
        )
    if denoise > 0:
        parts.append(
            f"hqdn3d=luma_spatial={denoise:.2f}:"
            f"chroma_spatial={denoise * 0.75:.2f}:"
            f"luma_tmp={denoise * 1.5:.2f}:"
            f"chroma_tmp={denoise * 1.1:.2f}"
        )
    if width and height:
        parts.append(
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:-1:-1:color=black"
        )
    return ",".join(parts)


def build_command(ffmpeg, src, dst, sharpen, denoise, duration, width, height):
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(src)]
    if duration and duration > 0:
        cmd += ["-t", f"{duration:g}"]
    vf = build_filter_chain(sharpen, denoise, width, height)
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(dst)]
    return cmd, vf


def probe(ffprobe, path):
    r = subprocess.run(
        [ffprobe, "-v", "error",
         "-show_entries", "stream=codec_type,codec_name,width,height",
         "-show_entries", "format=duration,size",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return None
    data = json.loads(r.stdout or "{}")
    info = {"duration": None, "size": None, "width": None, "height": None,
            "video_codec": None, "audio_codec": None}
    fmt = data.get("format", {})
    if fmt.get("duration"):
        info["duration"] = round(float(fmt["duration"]), 2)
    if fmt.get("size"):
        info["size"] = int(fmt["size"])
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and info["width"] is None:
            info["width"], info["height"] = s.get("width"), s.get("height")
            info["video_codec"] = s.get("codec_name")
        elif s.get("codec_type") == "audio" and info["audio_codec"] is None:
            info["audio_codec"] = s.get("codec_name")
    return info


# ---------------------------------------------------------------------------
# 可下载文件: 复现命令行 + 参数报告
# ---------------------------------------------------------------------------

def render_command_sh(ffmpeg, src_name, params, vf):
    lines = [
        "#!/usr/bin/env bash",
        "# 课程录屏画质优化 - 复现命令",
        f"# 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "# 用法: 将本脚本与原始录屏文件放在同一目录后执行  bash command.sh",
        "set -euo pipefail",
        'FFMPEG="${FFMPEG_PATH:-ffmpeg}"',
        "",
    ]
    args = ["-hide_banner", "-y", "-i", src_name]
    if params["duration"] > 0:
        args += ["-t", f'{params["duration"]:g}']
    if vf:
        args += ["-vf", vf]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart", "output.mp4"]
    lines.append('"$FFMPEG" ' + " ".join(shlex.quote(a) for a in args))
    lines.append("")
    return "\n".join(lines)


def render_report_md(job_id, params, vf, in_info, out_size, src_name):
    res = (f'{params["width"]}x{params["height"]}'
           if params["width"] else "原始分辨率")
    dur = f'{params["duration"]:g} 秒' if params["duration"] > 0 else "整段"
    rows = [
        ("文字锐化 (unsharp luma_amount)", f'{params["sharpen"]:.2f}'),
        ("背景降噪 (hqdn3d luma_spatial)", f'{params["denoise"]:.2f}'),
        ("音画片段长度", dur),
        ("输出分辨率", res),
    ]
    param_table = "\n".join(f"| {k} | {v} |" for k, v in rows)
    return f"""# 课程录屏画质优化 - 参数报告

- 任务 ID: `{job_id}`
- 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}
- 输入文件: `{src_name}`
- 输入信息: {in_info.get('width')}x{in_info.get('height')}, 时长 {in_info.get('duration')}s, 视频编码 {in_info.get('video_codec')}, 音频编码 {in_info.get('audio_codec') or '无'}
- 输出文件: `preview.mp4` ({out_size / 1024:.1f} KB)

## 优化参数

| 参数 | 取值 |
| --- | --- |
{param_table}

## 滤镜链

```
{vf or '(无滤镜, 仅转码)'}
```

说明:
- `unsharp`: 对亮度平面做非锐化掩模, 增强幻灯片/代码文字边缘, 色度不处理以避免噪点放大。
- `hqdn3d`: 高质量 3D 降噪(空间+时间域), 抑制录屏背景噪点与压缩块效应。
- `scale`+`pad`: 等比缩放到目标分辨率, 不足部分补黑边, 保证画面不变形。

## 复现命令

完整命令见随附 `command.sh`, 核心形式:

```bash
ffmpeg -hide_banner -y -i "{src_name}" \\
  {f'-t {params["duration"]:g} ' if params["duration"] > 0 else ''}-vf "{vf}" \\
  -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p \\
  -c:a aac -b:a 128k -movflags +faststart output.mp4
```
"""


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    st = ffmpeg_status()
    return jsonify(st), 200 if st["ok"] else 503


@app.route("/api/upload", methods=["POST"])
def upload():
    _, fp, err = require_ffmpeg()
    if err:
        return err
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "未选择文件"}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXT:
        return jsonify({"error": f"不支持的格式 .{ext}, 支持: {sorted(ALLOWED_EXT)}"}), 400
    file_id = uuid.uuid4().hex[:12]
    saved = secure_filename(f.filename) or f"video.{ext}"
    src = UPLOAD_DIR / f"{file_id}_{saved}"
    f.save(src)
    info = probe(fp, src)
    if info is None or info["width"] is None:
        src.unlink(missing_ok=True)
        return jsonify({"error": "无法解析该文件, 请确认是有效的视频"}), 422
    return jsonify({"file_id": file_id, "filename": f.filename, "info": info})


@app.route("/api/process", methods=["POST"])
def process():
    ff, fp, err = require_ffmpeg()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    file_id = str(data.get("file_id", ""))
    if not file_id.isalnum():
        return jsonify({"error": "file_id 不合法"}), 400
    matches = list(UPLOAD_DIR.glob(f"{file_id}_*"))
    if not matches:
        return jsonify({"error": "找不到已上传的文件, 请重新上传"}), 404
    src = matches[0]
    src_name = src.name.split("_", 1)[1]

    try:
        sharpen = min(max(float(data.get("sharpen", 1.0)), 0.0), 3.0)
        denoise = min(max(float(data.get("denoise", 4.0)), 0.0), 10.0)
        duration = min(max(float(data.get("duration", 0)), 0.0), 6 * 3600.0)
    except (TypeError, ValueError):
        return jsonify({"error": "参数必须是数字"}), 400
    res_key = data.get("resolution", "original")
    if res_key == "original":
        width = height = None
    elif res_key in RESOLUTIONS:
        width, height = RESOLUTIONS[res_key]
    else:
        return jsonify({"error": f"未知分辨率档位: {res_key}"}), 400

    params = {"sharpen": sharpen, "denoise": denoise,
              "duration": duration, "width": width, "height": height}

    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    dst = job_dir / "preview.mp4"

    cmd, vf = build_command(ff, src, dst, sharpen, denoise,
                            duration, width, height)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=PROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        return jsonify({"error": f"处理超时(>{PROCESS_TIMEOUT}s), 请缩短片段长度"}), 504
    if r.returncode != 0 or not dst.exists():
        tail = (r.stderr or "").strip().splitlines()[-8:]
        return jsonify({"error": "ffmpeg 处理失败",
                        "detail": "\n".join(tail)}), 500

    in_info = probe(fp, src) or {}
    out_size = dst.stat().st_size
    (job_dir / "command.sh").write_text(
        render_command_sh(ff, src_name, params, vf), encoding="utf-8")
    (job_dir / "report.md").write_text(
        render_report_md(job_id, params, vf, in_info, out_size, src_name),
        encoding="utf-8")
    (job_dir / "meta.json").write_text(json.dumps(
        {"params": params, "command": cmd, "input": in_info,
         "source": src_name, "created": datetime.now().isoformat()},
        ensure_ascii=False, indent=2), encoding="utf-8")

    return jsonify({
        "job_id": job_id,
        "preview_url": f"/outputs/{job_id}/preview.mp4",
        "command_url": f"/outputs/{job_id}/command.sh",
        "report_url": f"/outputs/{job_id}/report.md",
        "command_text": " ".join(shlex.quote(c) for c in cmd),
        "filter_chain": vf,
        "output_size": out_size,
    })


@app.route("/outputs/<job_id>/<path:filename>")
def serve_output(job_id, filename):
    if not job_id.isalnum():
        return "invalid job", 400
    as_dl = filename in ("command.sh", "report.md")
    return send_from_directory(OUTPUT_DIR / job_id, filename,
                               as_attachment=as_dl)


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": f"文件超过大小限制 ({MAX_UPLOAD_MB} MB)"}), 413


if __name__ == "__main__":
    st = ffmpeg_status()
    if st["ok"]:
        print(f"[OK] ffmpeg: {st['ffmpeg_path']}\n      {st['ffmpeg_version']}")
    else:
        print(f"[WARN] {st['error']}")
        for line in st["instructions"]:
            print("   - " + line)
    app.run(host="0.0.0.0", port=5000, debug=False)

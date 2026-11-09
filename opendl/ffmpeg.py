"""ffmpeg 支持：DASH（mpd）下载与流媒体封装。

ffmpeg 能把音视频两条轨分别拉下来再合成，正好补上我们自己实现不了的 DASH。
它不随程序打包（体积太大），所以按以下顺序查找：
  1. 环境变量 OPEN_DL_FFMPEG 指定的路径
  2. 程序目录 / bin 子目录里的 ffmpeg.exe
  3. PATH
  4. winget 安装目录
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import threading
import time

# 这些头由 ffmpeg 自己决定，硬塞会破坏请求
_SKIP_HEADERS = {"accept-encoding", "host", "connection", "content-length"}


class FfmpegError(Exception):
    pass


def find_ffmpeg() -> str | None:
    """Locate ffmpeg.exe, or None when it is not installed."""
    override = os.environ.get("OPEN_DL_FFMPEG")
    if override and os.path.isfile(override):
        return override

    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (
        os.path.join(here, "ffmpeg.exe"),
        os.path.join(here, "bin", "ffmpeg.exe"),
        os.path.join(os.path.dirname(here), "ffmpeg.exe"),
        os.path.join(os.path.dirname(here), "bin", "ffmpeg.exe"),
    ):
        if os.path.isfile(candidate):
            return candidate

    found = shutil.which("ffmpeg")
    if found:
        return found

    packages = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    if os.path.isdir(packages):
        hits = glob.glob(os.path.join(packages, "Gyan.FFmpeg*", "**", "bin", "ffmpeg.exe"), recursive=True)
        if hits:
            return sorted(hits)[-1]
    return None


def find_ffprobe(ffmpeg_path: str | None) -> str | None:
    if ffmpeg_path:
        sibling = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
        if os.path.isfile(sibling):
            return sibling
    return shutil.which("ffprobe")


def header_block(headers: dict | None) -> str:
    """ffmpeg 的 -headers 需要 CRLF 分隔，且不能包含连接级头部。"""
    if not headers:
        return ""
    lines = [
        f"{key}: {value}"
        for key, value in headers.items()
        if value and key.lower() not in _SKIP_HEADERS
    ]
    return "\r\n".join(lines) + "\r\n" if lines else ""


def probe_duration(ffprobe: str | None, url: str, headers: dict | None,
                   timeout: float = 25.0) -> float | None:
    """Total duration in seconds, used only to turn ffmpeg's progress into a ratio."""
    if not ffprobe:
        return None
    command = [ffprobe, "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1"]
    block = header_block(headers)
    if block:
        command += ["-headers", block]
    command.append(url)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        value = float(result.stdout.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def download_stream(ffmpeg: str, url: str, dest: str, headers: dict | None = None,
                    on_progress=None, stop_event: threading.Event | None = None,
                    duration: float | None = None) -> None:
    """Let ffmpeg pull the stream and remux it into `dest`.

    stderr is merged into stdout so a chatty stream cannot deadlock the pipe;
    progress lines (`key=value`) and error text are told apart by key.
    """
    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
               "-progress", "pipe:1", "-nostats"]
    block = header_block(headers)
    if block:
        command += ["-headers", block]
    command += ["-i", url, "-c", "copy", "-movflags", "+faststart", dest]

    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        raise FfmpegError(f"无法启动 ffmpeg：{exc}") from exc

    tail: list[str] = []

    def reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip()
            if not line:
                continue
            key, sep, value = line.partition("=")
            if sep and key in {"out_time_us", "out_time_ms"} and duration:
                if value.strip().isdigit():
                    seconds = int(value.strip()) / 1_000_000
                    if on_progress:
                        on_progress(min(1.0, seconds / duration))
            elif sep and key in {"progress", "frame", "fps", "bitrate", "total_size",
                                 "out_time", "dup_frames", "drop_frames", "speed"}:
                continue
            else:
                tail.append(line)
                del tail[:-20]

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    while process.poll() is None:
        if stop_event is not None and stop_event.is_set():
            process.kill()
            process.wait(timeout=5)
            thread.join(timeout=2)
            raise FfmpegError("已取消")
        time.sleep(0.2)

    process.wait()
    thread.join(timeout=2)

    if process.returncode != 0:
        message = "\n".join(tail).strip()
        raise FfmpegError(message or f"ffmpeg 退出码 {process.returncode}")
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        raise FfmpegError("ffmpeg 未产出文件")
    if on_progress:
        on_progress(1.0)

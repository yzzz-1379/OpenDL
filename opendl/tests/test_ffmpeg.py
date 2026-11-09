"""DASH 下载测试：用 ffmpeg 现场生成一条 mpd，再由 opendl 下载并校验。

没有安装 ffmpeg 时跳过（退出码 0），避免在别的机器上误报失败。
"""

from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from opendl.core import Download  # noqa: E402
from opendl.ffmpeg import find_ffmpeg, find_ffprobe  # noqa: E402

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    failures += 0 if ok else 1


def make_dash(directory: str, ffmpeg: str) -> None:
    """Produce a real mpd with one video track and one audio track.

    ffmpeg 把分片写到**工作目录**而不是清单所在目录，所以必须 cwd=directory，
    否则只会有 manifest.mpd，分片全散落在调用者的当前目录里。
    """
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-f", "dash", "-seg_duration", "1", "-use_template", "1", "-use_timeline", "1",
        "manifest.mpd",
    ]
    subprocess.run(command, check=True, timeout=240, cwd=directory)


def serve(directory: str):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.RequestHandlerClass.log_message = lambda *a, **k: None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_port


def probe_streams(ffprobe: str, path: str) -> tuple[list[str], float]:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type",
         "-show_entries", "format=duration", "-of", "default=nw=1", path],
        capture_output=True, text=True, timeout=60,
    )
    kinds, duration = [], 0.0
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("codec_type="):
            kinds.append(line.split("=", 1)[1])
        elif line.startswith("duration="):
            try:
                duration = float(line.split("=", 1)[1])
            except ValueError:
                pass
    return kinds, duration


def main() -> int:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("跳过：未安装 ffmpeg")
        return 0
    ffprobe = find_ffprobe(ffmpeg)

    workdir = tempfile.mkdtemp(prefix="opendl-dash-")
    origin = os.path.join(workdir, "origin")
    target = os.path.join(workdir, "dl")
    os.makedirs(origin)
    os.makedirs(target)
    httpd = None
    try:
        make_dash(origin, ffmpeg)
        check("已生成测试用 mpd", os.path.exists(os.path.join(origin, "manifest.mpd")))

        httpd, port = serve(origin)
        url = f"http://127.0.0.1:{port}/manifest.mpd"

        download = Download(url, target, connections=4)
        download.start()
        download.wait(240)

        check("DASH 下载完成", download.stats.status == "complete", download.stats.error)
        path = download.path or ""
        check("输出为 mp4", path.lower().endswith(".mp4"), path)
        check("文件非空", os.path.exists(path) and os.path.getsize(path) > 0,
              f"{os.path.getsize(path) if os.path.exists(path) else 0} bytes")
        check("进度到 100%", download.progress > 0.99, f"{download.progress:.2f}")

        kinds, duration = probe_streams(ffprobe, path)
        check("输出含视频轨", "video" in kinds, str(kinds))
        check("输出含音频轨", "audio" in kinds, str(kinds))
        check("时长接近 4 秒", 3.0 <= duration <= 5.0, f"{duration:.2f}s")
    finally:
        if httpd:
            httpd.shutdown()
        shutil.rmtree(workdir, ignore_errors=True)

    print("\n全部通过" if failures == 0 else f"\n{failures} 项失败")
    return failures


if __name__ == "__main__":
    sys.exit(main())

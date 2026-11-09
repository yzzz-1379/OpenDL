"""HLS end-to-end check: master playlist -> variant -> segments -> one file."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from opendl.core import Download  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="opendl-hls-src-")
SEGMENTS = [f"segment{i}.ts".encode() and (b"TS" + bytes([i]) * 4096) for i in range(6)]


def build_source() -> str:
    for index, payload in enumerate(SEGMENTS):
        with open(os.path.join(ROOT, f"segment{index}.ts"), "wb") as handle:
            handle.write(payload)

    media = "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:0\n"
    for index in range(len(SEGMENTS)):
        media += "#EXTINF:10.0,\n" + f"segment{index}.ts\n"
    media += "#EXT-X-ENDLIST\n"
    with open(os.path.join(ROOT, "media.m3u8"), "w", encoding="utf-8") as handle:
        handle.write(media)

    master = (
        "#EXTM3U\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720\nmedia.m3u8\n'
    )
    with open(os.path.join(ROOT, "master.m3u8"), "w", encoding="utf-8") as handle:
        handle.write(master)
    return ROOT


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802
        name = self.path.split("?")[0].lstrip("/")
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            self.send_response(404)
            self.end_headers()
            return
        data = open(path, "rb").read()
        content_type = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    build_source()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    outdir = tempfile.mkdtemp(prefix="opendl-hls-out-")
    url = f"http://127.0.0.1:{server.server_port}/master.m3u8"
    task = Download(url, outdir, connections=3)
    task.start()
    task.wait(60)

    expected = b"".join(SEGMENTS)
    actual = open(task.path, "rb").read() if task.path and os.path.exists(task.path) else b""

    checks = [
        ("任务完成", task.stats.status == "complete"),
        ("输出为 .ts", (task.path or "").endswith(".ts")),
        ("选了高码率变体并拼全 6 片", len(actual) == len(expected)),
        ("内容逐字节一致", hashlib.sha256(actual).hexdigest() == hashlib.sha256(expected).hexdigest()),
    ]
    failed = 0
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        failed += 0 if ok else 1
    print(f"\n输出: {task.path} ({len(actual)} bytes, 期望 {len(expected)}) status={task.stats.status}")
    server.shutdown()
    return failed


if __name__ == "__main__":
    raise SystemExit(main())

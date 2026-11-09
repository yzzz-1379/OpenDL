"""End-to-end checks for the OpenDL engine against a local HTTP server."""

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

PAYLOAD_SIZE = 12 * 1024 * 1024


def build_payload(path: str) -> str:
    with open(path, "wb") as handle:
        seed = b"opendl-test-block-"
        chunk = (seed * 4096)[:65536]
        written = 0
        while written < PAYLOAD_SIZE:
            handle.write(chunk)
            written += len(chunk)
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class RangeHandler(BaseHTTPRequestHandler):
    file_path = ""
    support_ranges = True
    range_requests = 0
    bytes_served = 0
    delay = 0.0

    def log_message(self, *_args):
        pass

    def _size(self):
        return os.path.getsize(self.file_path)

    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(self._size()))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        size = self._size()
        start, end = 0, size - 1
        partial = False
        header = self.headers.get("Range", "")
        if self.support_ranges and header.startswith("bytes="):
            spec = header[6:].split("-", 1)
            start = int(spec[0] or 0)
            end = int(spec[1]) if len(spec) > 1 and spec[1] else size - 1
            end = min(end, size - 1)
            partial = True
            type(self).range_requests += 1

        if start >= size:
            self.send_response(416)
            self.end_headers()
            return

        with open(self.file_path, "rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Length", str(remaining))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            try:
                while remaining > 0:
                    block = handle.read(min(65536, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    type(self).bytes_served += len(block)
                    remaining -= len(block)
                    if self.delay:
                        time.sleep(self.delay)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass


def serve(template, file_path, support_ranges=True, delay=0.0):
    handler = type("H", (template,), {"file_path": file_path, "support_ranges": support_ranges,
                                      "range_requests": 0, "bytes_served": 0, "delay": delay})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, handler


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run() -> int:
    failures = 0
    workdir = tempfile.mkdtemp(prefix="opendl-test-")
    source = os.path.join(workdir, "source.bin")
    expected = build_payload(source)

    # ---- case 1: multi-connection
    server, handler = serve(RangeHandler, source, True)
    outdir = os.path.join(workdir, "out1")
    task = Download(f"http://127.0.0.1:{server.server_port}/file.bin", outdir, connections=4)
    task.start()
    task.wait(120)
    ok = task.stats.status == "complete" and sha256(task.path) == expected
    print(f"[1] 分段下载 4 连接: {'PASS' if ok else 'FAIL'} "
          f"(status={task.stats.status}, ranges={handler.range_requests}, got={task.stats.downloaded})")
    failures += 0 if ok else 1
    server.shutdown()

    # ---- case 2: pause then resume (throttled so the pause lands mid-transfer)
    server, handler = serve(RangeHandler, source, True, delay=0.05)
    outdir = os.path.join(workdir, "out2")
    task = Download(f"http://127.0.0.1:{server.server_port}/resume.bin", outdir, connections=4)
    task.start()
    deadline = time.time() + 45
    while time.time() < deadline and task.progress < 0.25:
        time.sleep(0.02)
    task.pause()
    task.wait(20)
    paused_at = task.stats.downloaded
    time.sleep(0.3)
    task.start()
    task.wait(120)
    served = handler.bytes_served
    ok = (task.stats.status == "complete" and sha256(task.path) == expected
          and paused_at < PAYLOAD_SIZE * 0.9 and served < PAYLOAD_SIZE * 1.5)
    print(f"[2] 暂停后断点续传: {'PASS' if ok else 'FAIL'} "
          f"(paused_at={paused_at}, 总共传输={served}, 状态={task.stats.status})")
    failures += 0 if ok else 1
    server.shutdown()

    # ---- case 3: server without Range support
    server, _ = serve(RangeHandler, source, False)
    outdir = os.path.join(workdir, "out3")
    task = Download(f"http://127.0.0.1:{server.server_port}/single.bin", outdir, connections=4)
    task.start()
    task.wait(120)
    ok = task.stats.status == "complete" and sha256(task.path) == expected
    print(f"[3] 服务器不支持分段: {'PASS' if ok else 'FAIL'} "
          f"(status={task.stats.status}, size={task.stats.downloaded})")
    failures += 0 if ok else 1
    server.shutdown()

    # ---- case 4: filename from Content-Disposition is honoured (unit level)
    from opendl.core import filename_from_response
    class H(dict):
        pass
    name = filename_from_response(
        "http://x/y.bin",
        H({"Content-Disposition": "attachment; filename*=UTF-8''%E6%B5%8B%E8%AF%95.zip"}),
    )
    ok = name == "测试.zip"
    print(f"[4] Content-Disposition 文件名: {'PASS' if ok else 'FAIL'} (got {name!r})")
    failures += 0 if ok else 1

    print(f"\n结果: {'全部通过' if failures == 0 else str(failures) + ' 项失败'}")
    return failures


if __name__ == "__main__":
    raise SystemExit(run())

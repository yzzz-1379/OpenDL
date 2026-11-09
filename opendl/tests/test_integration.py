"""Checks the launcher handoff, the token gate, and the /save endpoint.

Uses an ephemeral port so a running OpenDL on 6688 cannot answer for us.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from opendl.integrations import BrowserAPI  # noqa: E402

received = []
export_dir = tempfile.mkdtemp(prefix="opendl-export-")
api = BrowserAPI(lambda url, headers: received.append((url, headers)), port=0,
                 export_dir=export_dir)
api.start()
time.sleep(0.3)
BASE = f"http://127.0.0.1:{api.port}"

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    failures += 0 if ok else 1


def post(path: str, payload: dict, token: str | None = None):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    if token:
        request.add_header("X-OpenDL-Token", token)
    return request


# 1) no token -> 403
try:
    urllib.request.urlopen(post("/add", {"url": "https://evil.test/x"}), timeout=5)
    check("无令牌被拒绝", False, "竟然成功了")
except urllib.error.HTTPError as exc:
    check("无令牌被拒绝", exc.code == 403, f"HTTP {exc.code}")

# 2) wrong token -> 403
try:
    urllib.request.urlopen(post("/add", {"url": "https://evil.test/x"}, "wrong"), timeout=5)
    check("错误令牌被拒绝", False)
except urllib.error.HTTPError as exc:
    check("错误令牌被拒绝", exc.code == 403, f"HTTP {exc.code}")

# 3) correct token -> 200 and headers forwarded
with urllib.request.urlopen(
    post("/add", {"url": "https://a.test/v.mp4", "referer": "https://a.test"}, api.token),
    timeout=5,
) as response:
    check("正确令牌被接受", response.status == 200)
check("回调收到 URL", len(received) == 1, str(received[:1]))

# 4) /save writes the settings backup into the configured directory
body = {"filename": "opendl-catcher-2026-11-09.json", "content": '{"token":"abc"}'}
try:
    urllib.request.urlopen(post("/save", body), timeout=5)
    check("/save 需要令牌", False, "无令牌也写成功了")
except urllib.error.HTTPError as exc:
    check("/save 需要令牌", exc.code == 403, f"HTTP {exc.code}")

with urllib.request.urlopen(post("/save", body, api.token), timeout=5) as response:
    saved = json.loads(response.read().decode())
check("/save 返回 ok", saved.get("ok") is True, str(saved))
check("/save 写到了指定目录", os.path.dirname(saved.get("path", "")) == export_dir, str(saved.get("path")))
check("/save 内容正确", open(saved["path"], encoding="utf-8").read() == '{"token":"abc"}')

# 5) path traversal is flattened
with urllib.request.urlopen(
    post("/save", {"filename": "../../evil.json", "content": "x"}, api.token), timeout=5
) as response:
    saved = json.loads(response.read().decode())
check("路径穿越被压平", os.path.dirname(saved.get("path", "")) == export_dir, str(saved.get("path")))

# 6) launcher forwards opendl:// to the running instance
before = len(received)
env = dict(os.environ, OPENDL_API=BASE)
proc = subprocess.run(
    [sys.executable, os.path.join(ROOT, "opendl", "launch.py"),
     "opendl://add?url=https%3A%2F%2Fexample.com%2Fmovie.mp4"],
    timeout=20, capture_output=True, env=env,
)
check("启动器转发成功", proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-200:])
check("转发内容正确",
      len(received) == before + 1 and received[-1][0] == "https://example.com/movie.mp4",
      str(received[-1:]))

api.stop()
print(f"\n{'全部通过' if failures == 0 else str(failures) + ' 项失败'}")
sys.exit(failures)

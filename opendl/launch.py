"""OpenDL 启动器：解析 opendl:// 调用、单实例转发、失败弹窗。

双击 OpenDL.bat 时用 pythonw.exe，没有控制台，所以任何异常都必须自己兜住并落盘。
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "launch-error.log")
API = os.environ.get("OPENDL_API", "http://127.0.0.1:6688")


def extract_url(argv: list[str]) -> str | None:
    """Pull a target URL out of `opendl://add?url=...` or a bare http(s) link."""
    for raw in argv:
        if not raw:
            continue
        candidate = raw.strip().strip('"')
        if candidate.lower().startswith("opendl:"):
            rest = candidate[len("opendl:"):].lstrip("/")
            if rest.lower().startswith("add"):
                query = urllib.parse.urlparse("opendl://" + rest).query
                value = (urllib.parse.parse_qs(query).get("url") or [""])[0]
                if value:
                    return value
            elif rest.lower().startswith("http"):
                return rest
        if candidate.lower().startswith(("http://", "https://")):
            return candidate
    return None


def _token() -> str:
    from .paths import config_file

    try:
        with open(config_file("token.txt"), "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def forward_to_running(url: str) -> bool:
    """Hand the URL to an already-running instance and exit."""
    payload = json.dumps({"url": url}).encode("utf-8")
    request = urllib.request.Request(
        API + "/add",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-OpenDL-Token": _token(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001 - not running is the normal case
        return False


def _report(message: str) -> None:
    try:
        with open(LOG_PATH, "w", encoding="utf-8") as handle:
            handle.write(message)
    except OSError:
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("OpenDL 启动失败", f"{message[-1200:]}\n\n完整日志：{LOG_PATH}")
        root.destroy()
    except Exception:  # noqa: BLE001
        print(message, file=sys.stderr)


def main() -> None:
    parent = os.path.dirname(HERE)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    pending = extract_url(sys.argv[1:])
    if pending and forward_to_running(pending):
        return

    from opendl.ui import main as run_ui

    run_ui(pending_url=pending)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        _report(traceback.format_exc())
        sys.exit(1)

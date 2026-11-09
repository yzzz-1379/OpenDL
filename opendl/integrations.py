"""Browser-side integration: clipboard capture and a token-guarded localhost API.

Any web page can POST to 127.0.0.1, so the endpoint is not trusted by address
alone: callers must present a token that only the desktop app and the paired
extension know. The origin header is checked as a second, cheaper signal.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .paths import config_file

URL_RE = re.compile(r"^https?://\S+$", re.I)
TOKEN_HEADER = "X-OpenDL-Token"
DEFAULT_PORT = 6688


def token_path() -> str:
    return config_file("token.txt")


def load_or_create_token() -> str:
    path = token_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            token = handle.read().strip()
            if token:
                return token
    except OSError:
        pass
    token = secrets.token_hex(16)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(token)
    except OSError:
        pass
    return token


class ClipboardMonitor:
    """Polls the clipboard and reports unseen http(s) links once."""

    def __init__(self, get_text, on_url):
        self._get_text = get_text
        self._on_url = on_url
        self._seen: set[str] = set()
        self.enabled = True

    def poll(self) -> str | None:
        if not self.enabled:
            return None
        try:
            text = (self._get_text() or "").strip()
        except Exception:  # noqa: BLE001 - clipboard can be locked by another app
            return None
        if not text or len(text) > 4096 or not URL_RE.match(text):
            return None
        if text in self._seen:
            return None
        self._seen.add(text)
        self._on_url(text)
        return text


class BrowserAPI:
    """Local HTTP endpoint the extension pushes URLs to.

    GET  /ping                      -> {"ok": true, "app": "opendl"}   (no token)
    POST /add {url, referer, cookie, user_agent}
    GET  /add?url=...&referer=...
    """

    def __init__(self, on_url, port: int = DEFAULT_PORT, host: str = "127.0.0.1",
                 token: str | None = None, export_dir=None):
        self.on_url = on_url
        self.port = port
        self.host = host
        self.token = token or load_or_create_token()
        # Where /save writes. A callable keeps it in sync with the UI setting.
        self.export_dir = export_dir
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _target_dir(self) -> str:
        target = self.export_dir() if callable(self.export_dir) else self.export_dir
        return target or os.path.expanduser("~")

    @property
    def running(self) -> bool:
        return self._server is not None

    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        supplied = handler.headers.get(TOKEN_HEADER, "")
        if not supplied or not secrets.compare_digest(supplied, self.token):
            return False
        origin = handler.headers.get("Origin", "")
        # A page in a tab sends its own origin; the extension sends chrome-extension://.
        return origin == "" or origin.startswith(("chrome-extension://", "moz-extension://"))

    def start(self) -> None:
        if self._server is not None:
            return
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "OpenDL/1.0"

            def log_message(self, *_args):
                pass

            def _reply(self, code: int, payload: dict):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):  # noqa: N802
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", f"Content-Type, {TOKEN_HEADER}")
                self.end_headers()

            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                route = parsed.path.rstrip("/")
                if route == "/ping":
                    self._reply(200, {"ok": True, "app": "opendl", "auth": outer._authorized(self)})
                    return
                if not outer._authorized(self):
                    self._reply(403, {"ok": False, "error": "bad or missing token"})
                    return
                if route == "/add":
                    query = urllib.parse.parse_qs(parsed.query)
                    url = (query.get("url") or [""])[0]
                    if not URL_RE.match(url):
                        self._reply(400, {"ok": False, "error": "bad url"})
                        return
                    outer.on_url(url, {
                        "Referer": (query.get("referer") or [""])[0],
                        "Cookie": (query.get("cookie") or [""])[0],
                        "User-Agent": (query.get("user_agent") or [""])[0],
                    })
                    self._reply(200, {"ok": True})
                    return
                self._reply(404, {"ok": False, "error": "not found"})

            def do_POST(self):  # noqa: N802
                route = self.path.rstrip("/")
                if route not in ("/add", "/save"):
                    self._reply(404, {"ok": False, "error": "not found"})
                    return
                if not outer._authorized(self):
                    self._reply(403, {"ok": False, "error": "bad or missing token"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(min(length, 256 * 1024)) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    payload = {}

                if route == "/save":
                    # Extension asks the desktop app to write a file (settings backup).
                    name = os.path.basename(str(payload.get("filename") or "settings.json"))
                    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
                    content = payload.get("content")
                    if not name or not isinstance(content, str):
                        self._reply(400, {"ok": False, "error": "bad filename or content"})
                        return
                    if len(content.encode("utf-8")) > 2 * 1024 * 1024:
                        self._reply(413, {"ok": False, "error": "file too large"})
                        return
                    try:
                        target_dir = outer._target_dir()
                        os.makedirs(target_dir, exist_ok=True)
                        path = os.path.join(target_dir, name)
                        base, ext = os.path.splitext(path)
                        counter = 1
                        while os.path.exists(path):
                            path = f"{base} ({counter}){ext}"
                            counter += 1
                        with open(path, "w", encoding="utf-8") as handle:
                            handle.write(content)
                    except OSError as exc:
                        self._reply(500, {"ok": False, "error": f"write failed: {exc}"})
                        return
                    self._reply(200, {"ok": True, "path": path})
                    return

                url = str(payload.get("url", ""))
                if not URL_RE.match(url):
                    self._reply(400, {"ok": False, "error": "bad url"})
                    return
                outer.on_url(url, {
                    "Referer": str(payload.get("referer", "")),
                    "Cookie": str(payload.get("cookie", "")),
                    "User-Agent": str(payload.get("user_agent", "")),
                })
                self._reply(200, {"ok": True})

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        # port 0 asks the OS to pick one; record the real value for callers.
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

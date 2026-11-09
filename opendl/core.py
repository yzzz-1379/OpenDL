"""OpenDL core — segmented, resumable, multi-connection HTTP/HTTPS downloader.

Design mirrors the parts of IDM that matter for downloading:
  * probe the URL once (size, range support, filename)
  * split the body into N byte ranges, one worker thread each
  * persist the segment map next to the output file so a paused or crashed
    transfer resumes at byte level, not from zero
  * fall back to a single sequential stream when the server has no Range
"""

from __future__ import annotations

import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

CHUNK = 256 * 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
STATE_SUFFIX = ".opendl"
MIN_SEGMENT = 1024 * 1024  # below this, extra connections are pointless


class ProbeError(Exception):
    pass


@dataclass
class ProbeResult:
    url: str
    final_url: str
    size: int
    supports_ranges: bool
    filename: str
    content_type: str = ""
    is_hls: bool = False
    is_dash: bool = False


def _repair_mojibake(text: str) -> str:
    """Undo UTF-8 bytes that were decoded as latin-1 / cp1252.

    '网盘部署' read through cp1252 becomes 'ç½‘ç›˜éƒ¨ç½²'. Trying cp1252 first
    then latin-1 covers both ways that happens; a name that survives neither
    is returned untouched, so genuine non-ASCII names are not damaged.
    """
    if not text or all(ord(ch) < 128 for ch in text):
        return text
    for encoding in ("cp1252", "latin-1"):
        try:
            candidate = text.encode(encoding).decode("utf-8")
            if candidate:
                return candidate
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text


def _sanitize(name: str) -> str:
    name = _repair_mojibake(urllib.parse.unquote(name))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "download.bin"


def filename_from_response(url: str, headers) -> str:
    disposition = headers.get("Content-Disposition", "") or ""
    if disposition:
        # RFC 5987 form first, then the plain quoted form.
        m = re.search(r"filename\*\s*=\s*([^;]+)", disposition, re.I)
        if m:
            value = m.group(1).strip().strip('"')
            if "''" in value:
                charset, _lang, encoded = value.partition("''")
                try:
                    return _sanitize(urllib.parse.unquote(encoded, encoding=charset or "utf-8"))
                except Exception:
                    pass
        m = re.search(r'filename\s*=\s*"([^"]+)"', disposition, re.I)
        if m:
            return _sanitize(m.group(1))
        m = re.search(r"filename\s*=\s*([^;]+)", disposition, re.I)
        if m:
            return _sanitize(m.group(1).strip().strip('"'))
    path = urllib.parse.urlparse(url).path
    base = os.path.basename(path)
    return _sanitize(base) if base else "download.bin"


def probe(url: str, timeout: float = 25.0, extra_headers: dict | None = None) -> ProbeResult:
    """One cheap request to learn size, range support and filename."""
    base_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if extra_headers:
        base_headers.update({k: v for k, v in extra_headers.items() if v})

    request = urllib.request.Request(url, headers={**base_headers, "Range": "bytes=0-0"})
    try:
        response = urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context())
    except urllib.error.HTTPError as exc:
        if exc.code == 416:  # server refuses the range probe; retry plainly
            request = urllib.request.Request(url, headers=base_headers)
            response = urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context())
        else:
            raise ProbeError(f"HTTP {exc.code} {exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        raise ProbeError(str(exc)) from exc

    with response:
        status = getattr(response, "status", 200)
        headers = response.headers
        content_type = (headers.get("Content-Type", "") or "").lower()
        supports_ranges = status == 206 and "bytes" in (headers.get("Content-Range") or "").lower()
        path = urllib.parse.urlparse(url).path.lower()
        is_hls = "mpegurl" in content_type or path.endswith(".m3u8")
        is_dash = "dash+xml" in content_type or path.endswith(".mpd")

        if supports_ranges:
            content_range = headers.get("Content-Range", "")
            m = re.search(r"/(\d+)\s*$", content_range)
            size = int(m.group(1)) if m else -1
        else:
            size = int(headers.get("Content-Length") or -1)

        filename = filename_from_response(response.geturl() or url, headers)
        return ProbeResult(
            url=url,
            final_url=response.geturl() or url,
            size=size,
            supports_ranges=supports_ranges,
            filename=filename,
            content_type=content_type,
            is_hls=is_hls,
            is_dash=is_dash,
        )


@dataclass
class Segment:
    start: int
    end: int  # inclusive
    done: int = 0

    @property
    def remaining(self) -> int:
        return max(0, (self.end - self.start + 1) - self.done)


@dataclass
class TaskStats:
    downloaded: int = 0
    speed: float = 0.0
    eta: float | None = None
    status: str = "queued"
    error: str = ""


class Download:
    """One file transfer with N range workers and byte-level resume."""

    def __init__(self, url: str, target_dir: str, connections: int = 8,
                 filename: str | None = None, on_change=None, headers: dict | None = None):
        self.url = url
        self.target_dir = target_dir
        self.connections = max(1, min(int(connections), 32))
        self.fixed_filename = filename
        self.on_change = on_change
        self.headers = dict(headers or {})

        self.probe_result: ProbeResult | None = None
        self.path: str | None = None
        self.state_path: str | None = None
        self.segments: list[Segment] = []
        self.size = -1
        self.stats = TaskStats()
        self._hls_done = 0
        self._hls_total = 0
        self._stream_progress: float | None = None

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._monitor: threading.Thread | None = None
        self._started_at = 0.0
        self._last_bytes = 0
        self._last_tick = 0.0
        self._finished = threading.Event()
        self._generation = 0
        self._state_disabled = False

    # ---------------------------------------------------------------- helpers
    def _notify(self):
        if self.on_change:
            try:
                self.on_change(self)
            except Exception:  # noqa: BLE001 - a UI callback must not kill the worker
                pass

    def _merge_headers(self, extra: dict | None = None) -> dict:
        merged = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        merged.update({k: v for k, v in self.headers.items() if v})
        if extra:
            merged.update(extra)
        return merged

    @property
    def total_bytes(self) -> int:
        if self.size > 0:
            return self.size
        return sum(s.end - s.start + 1 for s in self.segments)

    @property
    def progress(self) -> float:
        if self._stream_progress is not None:
            return max(0.0, min(1.0, self._stream_progress))
        if self._hls_total:
            return min(1.0, self._hls_done / self._hls_total)
        total = self.total_bytes
        if total <= 0:
            return 0.0
        return min(1.0, self.stats.downloaded / total)

    def _save_state(self):
        if not self.state_path or self._state_disabled:
            return
        # Streams have no byte map to resume from, so they never write state.
        if self.probe_result and (self.probe_result.is_hls or self.probe_result.is_dash):
            return
        payload = {
            "url": self.url,
            "final_url": self.probe_result.final_url if self.probe_result else self.url,
            "size": self.size,
            "ranges": bool(self.probe_result and self.probe_result.supports_ranges),
            "segments": [{"start": s.start, "end": s.end, "done": s.done} for s in self.segments],
        }
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def _load_state(self) -> bool:
        if not self.state_path or not os.path.exists(self.state_path):
            return False
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return False
        if payload.get("url") != self.url or payload.get("size") != self.size:
            return False
        segments = [Segment(int(s["start"]), int(s["end"]), int(s.get("done", 0)))
                    for s in payload.get("segments", [])]
        if not segments:
            return False
        self.segments = segments
        with self._lock:
            self.stats.downloaded = sum(s.done for s in segments)
        return True

    # ------------------------------------------------------------------ setup
    def prepare(self) -> None:
        """Resolve size/name and build the segment map (idempotent)."""
        if self.probe_result is None:
            self.probe_result = probe(self.url, extra_headers=self.headers)
        if not self.fixed_filename:
            self.fixed_filename = self.probe_result.filename
        if self.probe_result.is_hls and self.fixed_filename.lower().endswith(".m3u8"):
            self.fixed_filename = self.fixed_filename[:-5] + ".ts"
        if self.probe_result.is_dash and self.fixed_filename.lower().endswith(".mpd"):
            # DASH 由 ffmpeg 合成 mp4，名字不该还挂着清单的扩展名
            self.fixed_filename = self.fixed_filename[:-4] + ".mp4"

        os.makedirs(self.target_dir, exist_ok=True)
        base_path = os.path.join(self.target_dir, self.fixed_filename)
        self.path = base_path
        base, ext = os.path.splitext(base_path)
        counter = 1
        # A file plus its .opendl state is a resumable partial: keep that name.
        # A lone existing file is a finished download: never clobber it.
        while os.path.exists(self.path) and not os.path.exists(self.path + STATE_SUFFIX):
            if counter > 1000:
                break
            self.path = f"{base} ({counter}){ext}"
            counter += 1

        self.state_path = self.path + STATE_SUFFIX
        self.size = self.probe_result.size

        # Streams are assembled by their own worker: no byte map, no preallocate.
        if self.probe_result.is_hls or self.probe_result.is_dash:
            return

        # Without Range support a partial byte offset is meaningless: start over.
        if not self.probe_result.supports_ranges or self.size <= 0:
            for stale in (self.path, self.state_path):
                try:
                    if os.path.exists(stale):
                        os.remove(stale)
                except OSError:
                    pass
        elif self._load_state():
            return

        if self.size > 0 and self.probe_result.supports_ranges:
            count = max(1, min(self.connections, self.size // MIN_SEGMENT or 1))
            step = self.size // count
            self.segments = []
            for index in range(count):
                start = index * step
                end = self.size - 1 if index == count - 1 else (start + step - 1)
                self.segments.append(Segment(start, end))
        else:
            self.segments = [Segment(0, max(self.size - 1, 0))]

        # Preallocate so every worker can seek independently.
        if self.size > 0:
            with open(self.path, "wb") as handle:
                handle.truncate(self.size)
        else:
            open(self.path, "wb").close()
        self._save_state()

    def _state_matches(self, candidate: str) -> bool:
        return os.path.exists(candidate + STATE_SUFFIX)

    # ---------------------------------------------------------------- workers
    def _fetch_segment(self, segment: Segment) -> None:
        attempt = 0
        while not self._stop.is_set():
            offset = segment.start + segment.done
            if offset > segment.end:
                return
            try:
                self._stream_range(segment, offset)
                return
            except _Cancelled:
                return
            except Exception as exc:  # noqa: BLE001 - retry then report
                attempt += 1
                if attempt >= 4 or self._stop.is_set():
                    with self._lock:
                        self.stats.error = f"segment {segment.start}: {exc}"
                    return
                time.sleep(min(2 ** attempt, 8))

    def _stream_range(self, segment: Segment, offset: int) -> None:
        request = urllib.request.Request(
            self.probe_result.final_url,
            headers=self._merge_headers({
                "Range": f"bytes={offset}-{segment.end}",
            }),
        )
        response = urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context())
        with response, open(self.path, "r+b") as handle:
            if getattr(response, "status", 200) != 206 and segment.done:
                raise IOError("server ignored the range request")
            handle.seek(offset)
            while not self._stop.is_set():
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                segment.done += len(chunk)
                with self._lock:
                    self.stats.downloaded += len(chunk)
        if self._stop.is_set():
            raise _Cancelled()

    def _stream_single(self) -> None:
        segment = self.segments[0]
        offset = segment.done
        request = urllib.request.Request(
            self.probe_result.final_url,
            headers=self._merge_headers(),
        )
        response = urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context())
        with response, open(self.path, "r+b") as handle:
            handle.seek(offset)
            if offset and response.headers.get("Content-Length"):
                # Cannot trust an offset without Range support.
                pass
            while not self._stop.is_set():
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                segment.done += len(chunk)
                with self._lock:
                    self.stats.downloaded += len(chunk)
        if self._stop.is_set():
            raise _Cancelled()

    def _hls_worker(self) -> None:
        """Pull an HLS playlist segment by segment and concatenate the result."""
        from .hls import HlsError, download_hls

        try:
            def on_progress(done: int, total: int) -> None:
                self._hls_done = done
                self._hls_total = total
                self._notify()

            download_hls(self.probe_result.final_url, self.path, self.headers,
                         self.connections, on_progress, self._stop)
            self.stats.status = "complete"
            with self._lock:
                self.size = os.path.getsize(self.path)
                self.stats.downloaded = self.size
        except HlsError as exc:
            # 自己解析不了（例如 AES-128 加密），交给 ffmpeg 再试一次
            try:
                self._run_ffmpeg()
                self.stats.status = "complete"
                self._stream_progress = 1.0
                with self._lock:
                    self.size = os.path.getsize(self.path)
                    self.stats.downloaded = self.size
            except Exception:  # noqa: BLE001 - 保留原始 HLS 错误更有信息量
                self.stats.status = "error"
                self.stats.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self.stats.status = "error"
            self.stats.error = str(exc)
        finally:
            self._finished.set()
            self._notify()

    def _run_ffmpeg(self) -> None:
        """Pull the stream with ffmpeg; raises on failure."""
        from .ffmpeg import download_stream, find_ffmpeg, find_ffprobe, probe_duration

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("未找到 ffmpeg：请安装后重试（或设置 OPEN_DL_FFMPEG）")
        duration = probe_duration(find_ffprobe(ffmpeg), self.probe_result.final_url, self.headers)

        def on_progress(fraction: float) -> None:
            self._stream_progress = fraction
            with self._lock:
                self.stats.downloaded = int(duration * fraction) if duration else int(fraction * 100)
            self._notify()

        download_stream(ffmpeg, self.probe_result.final_url, self.path, self.headers,
                        on_progress, self._stop, duration)

    def _ffmpeg_worker(self) -> None:
        try:
            self._run_ffmpeg()
            self.stats.status = "complete"
            self._stream_progress = 1.0
            with self._lock:
                self.size = os.path.getsize(self.path)
                self.stats.downloaded = self.size
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self.stats.status = "error"
            self.stats.error = str(exc) or "ffmpeg 下载失败"
        finally:
            self._finished.set()
            self._notify()

    def _monitor_loop(self, generation: int) -> None:
        self._last_tick = time.time()
        self._last_bytes = self.stats.downloaded
        while not self._finished.is_set() and generation == self._generation:
            time.sleep(0.5)
            now = time.time()
            elapsed = max(now - self._last_tick, 1e-6)
            current = self.stats.downloaded
            speed = (current - self._last_bytes) / elapsed
            with self._lock:
                self.stats.speed = speed
                remaining = self.total_bytes - current
                self.stats.eta = (remaining / speed) if speed > 1024 else None
            self._last_bytes, self._last_tick = current, now
            self._save_state()
            self._notify()

    # ------------------------------------------------------------------- api
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        self._finished.clear()
        try:
            self.prepare()
        except ProbeError as exc:
            self.stats.status = "error"
            self.stats.error = str(exc)
            self._finished.set()
            self._notify()
            return
        except OSError as exc:
            self.stats.status = "error"
            self.stats.error = f"无法创建目标文件：{exc}"
            self._finished.set()
            self._notify()
            return
        self.stats.status = "downloading"
        self._started_at = time.time()

        if self.probe_result.is_dash:
            # DASH 的音视频是两条轨，自己拼不划算，交给 ffmpeg。
            self._threads = [threading.Thread(target=self._ffmpeg_worker, daemon=True)]
            self._generation += 1
            self._monitor = threading.Thread(
                target=self._monitor_loop, args=(self._generation,), daemon=True
            )
            self._monitor.start()
            self._threads[0].start()
            self._notify()
            return

        if self.probe_result.is_hls:
            self._threads = [threading.Thread(target=self._hls_worker, daemon=True)]
            self._generation += 1
            self._monitor = threading.Thread(
                target=self._monitor_loop, args=(self._generation,), daemon=True
            )
            self._monitor.start()
            self._threads[0].start()
            self._notify()
            return

        range_mode = bool(self.probe_result and self.probe_result.supports_ranges) and self.size > 0
        if range_mode:
            self._threads = [
                threading.Thread(target=self._fetch_segment, args=(s,), daemon=True)
                for s in self.segments
            ]
        else:
            self._threads = [threading.Thread(target=self._stream_single, daemon=True)]

        for thread in self._threads:
            thread.start()
        self._generation += 1
        self._monitor = threading.Thread(target=self._monitor_loop, args=(self._generation,), daemon=True)
        self._monitor.start()
        threading.Thread(target=self._supervise, daemon=True).start()
        self._notify()

    def _supervise(self) -> None:
        for thread in self._threads:
            thread.join()
        self._threads = []
        with self._lock:
            done = self.stats.downloaded
            total = self.total_bytes
            failed = bool(self.stats.error)
            complete = total > 0 and done >= total
        if self._stop.is_set() and not complete:
            self.stats.status = "paused"
        elif failed and not complete:
            self.stats.status = "error"
        else:
            self.stats.status = "complete"
            # Stop the monitor from writing state back after we delete it.
            self._state_disabled = True
            if total <= 0:
                self.stats.downloaded = os.path.getsize(self.path)
            try:
                if self.state_path and os.path.exists(self.state_path):
                    os.remove(self.state_path)
            except OSError:
                pass
        self._finished.set()
        # Never rewrite the state after a successful run removed it.
        if self.stats.status != "complete":
            self._save_state()
        self._notify()

    def pause(self) -> None:
        self._stop.set()
        self.stats.status = "paused"
        self._notify()

    def wait(self, timeout: float | None = None) -> bool:
        return self._finished.wait(timeout)

    def reset(self) -> None:
        """Drop the partial file and its state."""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2)
        for path in (self.path, self.state_path):
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


class _Cancelled(Exception):
    pass

"""HLS (m3u8) support: resolve the playlist, pull every segment, concatenate.

No ffmpeg needed for the common case: MPEG-TS segments are byte-concatenable.
Encrypted (AES-128) playlists are refused explicitly rather than producing a
broken file — that path needs a decryptor, which is a separate feature.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class HlsError(Exception):
    pass


def _request(url: str, headers: dict | None):
    merged = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        merged.update({k: v for k, v in headers.items() if v})
    return urllib.request.Request(url, headers=merged)


def fetch_text(url: str, headers: dict | None = None, timeout: float = 30.0) -> str:
    import ssl

    with urllib.request.urlopen(_request(url, headers), timeout=timeout,
                               context=ssl.create_default_context()) as response:
        return response.read().decode("utf-8", "replace")


def _absolute(base: str, ref: str) -> str:
    return urllib.parse.urljoin(base, ref)


def pick_variant(playlist: str, base_url: str) -> str | None:
    """For a master playlist, return the highest-bandwidth variant URL."""
    if "#EXT-X-STREAM-INF" not in playlist:
        return None
    best = None
    lines = playlist.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        bandwidth = 0
        match = re.search(r"BANDWIDTH=(\d+)", line)
        if match:
            bandwidth = int(match.group(1))
        for follow in lines[index + 1:]:
            if follow and not follow.startswith("#"):
                if best is None or bandwidth > best[0]:
                    best = (bandwidth, _absolute(base_url, follow.strip()))
                break
    return best[1] if best else None


def parse_media(playlist: str, base_url: str) -> dict:
    """Extract segment URLs, init segment and encryption info."""
    segments: list[str] = []
    init_url = None
    encrypted = False

    for raw in playlist.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MAP"):
            match = re.search(r'URI="([^"]+)"', line)
            if match:
                init_url = _absolute(base_url, match.group(1))
            continue
        if line.startswith("#EXT-X-KEY"):
            method = re.search(r"METHOD=([A-Z0-9-]+)", line)
            if method and method.group(1) not in ("NONE",):
                encrypted = True
            continue
        if line.startswith("#"):
            continue
        segments.append(_absolute(base_url, line))

    return {"segments": segments, "init": init_url, "encrypted": encrypted}


def _download_one(url: str, dest: str, headers: dict | None, stop: threading.Event) -> int:
    import ssl

    written = 0
    with urllib.request.urlopen(_request(url, headers), timeout=60,
                               context=ssl.create_default_context()) as response, open(dest, "wb") as handle:
        while not stop.is_set():
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            written += len(chunk)
    return written


def download_hls(url: str, out_path: str, headers: dict | None, connections: int,
                 on_progress, stop: threading.Event) -> str:
    """Download an HLS stream and concatenate it into out_path.

    on_progress(done_segments, total_segments) is called as work proceeds.
    Returns the written path.
    """
    playlist = fetch_text(url, headers)
    variant = pick_variant(playlist, url)
    if variant:
        url = variant
        playlist = fetch_text(url, headers)

    media = parse_media(playlist, url)
    if media["encrypted"]:
        raise HlsError("该流使用 AES-128 加密，需要解密支持（暂未实现）")
    segments = media["segments"]
    if not segments:
        raise HlsError("清单里没有找到分片")

    total = len(segments)
    workdir = tempfile.mkdtemp(prefix="opendl-hls-")
    done = 0
    lock = threading.Lock()
    errors: list[str] = []

    def worker(indices: list[int]):
        nonlocal done
        for index in indices:
            if stop.is_set():
                return
            dest = os.path.join(workdir, f"{index:06d}.part")
            try:
                _download_one(segments[index], dest, headers, stop)
                with lock:
                    done += 1
                    on_progress(done, total)
            except (urllib.error.URLError, OSError) as exc:
                with lock:
                    errors.append(f"分片 {index}: {exc}")
                return

    try:
        if media["init"]:
            init_dest = os.path.join(workdir, "000_init.part")
            _download_one(media["init"], init_dest, headers, stop)

        buckets: list[list[int]] = [[] for _ in range(max(1, min(connections, total)))]
        for index in range(total):
            buckets[index % len(buckets)].append(index)

        threads = [threading.Thread(target=worker, args=(bucket,), daemon=True)
                   for bucket in buckets if bucket]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if stop.is_set():
            raise HlsError("已取消")
        if errors:
            raise HlsError(errors[0])

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "wb") as output:
            init_file = os.path.join(workdir, "000_init.part")
            if os.path.exists(init_file):
                with open(init_file, "rb") as handle:
                    shutil.copyfileobj(handle, output)
            for index in range(total):
                part = os.path.join(workdir, f"{index:06d}.part")
                if not os.path.exists(part):
                    raise HlsError(f"缺少分片文件 {index}")
                with open(part, "rb") as handle:
                    shutil.copyfileobj(handle, output)
        return out_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

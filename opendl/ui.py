"""Tkinter front end: task list, toolbar, add-dialog, live refresh."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import tkinter as tk
import urllib.parse
from tkinter import filedialog, messagebox, ttk

from .paths import config_file, resource_path

from .core import Download
from .integrations import BrowserAPI, ClipboardMonitor

APP_TITLE = "OpenDL — 多线程下载器"


def system_downloads_dir() -> str:
    """The folder Windows itself calls "Downloads" (it may be redirected to D:).

    `~/Downloads` is not equivalent: any user who moved the folder gets a path
    that quietly exists but is not where Explorer and browsers put files.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            # FOLDERID_Downloads {374DE290-123F-4565-9164-39C4925E467B}
            folder_id = _GUID(
                0x374DE290, 0x123F, 0x4565,
                (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B),
            )
            path = ctypes.c_wchar_p()
            result = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(folder_id), 0, None, ctypes.byref(path)
            )
            if result == 0 and path.value:
                value = path.value
                ctypes.windll.ole32.CoTaskMemFree(path)
                return value
        except Exception:  # noqa: BLE001 - fall back to the conventional path
            pass
    return os.path.join(os.path.expanduser("~"), "Downloads")


DEFAULT_DIR = system_downloads_dir()
SETTINGS_PATH = config_file("settings.json")


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def display_name(url: str) -> str:
    """A readable label for a URL: no query string, percent-decoded."""
    path = urllib.parse.urlparse(url).path
    base = urllib.parse.unquote(path.rsplit("/", 1)[-1])
    return base or url


def human_size(num: float) -> str:
    if num is None or num < 0:
        return "未知"
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while num >= 1024 and index < len(units) - 1:
        num /= 1024.0
        index += 1
    return f"{num:.1f} {units[index]}" if index else f"{int(num)} B"


def human_speed(num: float) -> str:
    return f"{human_size(num)}/s" if num > 0 else "—"


def human_eta(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "—"
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds}s"


class AddDialog(tk.Toplevel):
    def __init__(self, master, on_submit):
        super().__init__(master)
        self.title("添加下载")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.on_submit = on_submit
        self.connections = tk.IntVar(value=8)
        self.directory = tk.StringVar(value=DEFAULT_DIR)

        body = ttk.Frame(self, padding=14)
        body.grid(row=0, column=0, sticky="nsew")
        ttk.Label(body, text="下载地址").grid(row=0, column=0, sticky="w")
        self.url_entry = ttk.Entry(body, width=62)
        self.url_entry.grid(row=1, column=0, columnspan=3, sticky="we", pady=(2, 10))

        ttk.Label(body, text="保存到").grid(row=2, column=0, sticky="w")
        ttk.Entry(body, textvariable=self.directory, width=48).grid(row=3, column=0, columnspan=2, sticky="we", pady=(2, 10))
        ttk.Button(body, text="浏览…", command=self._browse).grid(row=3, column=2, padx=(6, 0), pady=(2, 10))

        ttk.Label(body, text="连接数").grid(row=4, column=0, sticky="w")
        ttk.Spinbox(body, from_=1, to=32, textvariable=self.connections, width=6).grid(
            row=5, column=0, sticky="w", pady=(2, 12)
        )
        ttk.Label(body, text="（1–32，服务器支持分段时生效）", foreground="#666").grid(
            row=5, column=1, columnspan=2, sticky="w"
        )

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="开始下载", command=self._submit).pack(side="right")

        self.url_entry.focus_set()
        self.bind("<Return>", lambda _e: self._submit())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _browse(self):
        chosen = filedialog.askdirectory(initialdir=self.directory.get() or DEFAULT_DIR)
        if chosen:
            self.directory.set(chosen)

    def _submit(self):
        url = self.url_entry.get().strip()
        if not url.lower().startswith(("http://", "https://")):
            messagebox.showwarning("地址无效", "请输入以 http:// 或 https:// 开头的地址。", parent=self)
            return
        self.on_submit(url, self.directory.get().strip() or DEFAULT_DIR, self.connections.get())
        self.destroy()


def apply_window_icon(window) -> None:
    """Set the window/taskbar icon from icon.ico.

    Tk falls back to its own feather icon unless an icon is supplied, and
    default= makes every later dialog inherit it too.
    """
    icon = resource_path("icon.ico")
    if not os.path.exists(icon):
        return
    try:
        window.iconbitmap(default=icon)
    except Exception:  # noqa: BLE001 - a missing icon must never block startup
        pass


class App(tk.Tk):
    def __init__(self, pending_url: str | None = None):
        super().__init__()
        self.title(APP_TITLE)
        apply_window_icon(self)
        self.geometry("1000x560")
        self.minsize(860, 460)
        self.tasks: list[Download] = []
        self.rows: dict[int, str] = {}
        self.connections = 8
        self.clipboard_on = tk.BooleanVar(value=True)
        self.settings = load_settings()
        self.download_dir = self.settings.get("download_dir") or DEFAULT_DIR

        self._build_style()
        self._build_toolbar()
        self._build_table()
        self._build_statusbar()

        self.api = BrowserAPI(self._enqueue_from_browser, export_dir=lambda: self.download_dir)
        try:
            self.api.start()
            self.api_state = f"浏览器接口 :{self.api.port} 已监听 · 配对码 {self.api.token[:8]}…"
        except OSError as exc:
            self.api_state = f"浏览器接口未启动（{exc}）"

        self.clip = ClipboardMonitor(lambda: self.clipboard_get(), self._enqueue_from_clipboard)
        if pending_url:
            self.after(400, lambda: self.add_task(pending_url, self.download_dir, self.connections))
        self.after(600, self._tick)

    # ------------------------------------------------------------------ chrome
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=26)
        style.configure("Toolbar.TButton", padding=(10, 4))

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(10, 8))
        bar.pack(side="top", fill="x")
        ttk.Button(bar, text="添加任务", style="Toolbar.TButton", command=self._open_add).pack(side="left")
        for text, handler in (("暂停", self._pause), ("继续", self._resume), ("删除", self._remove)):
            ttk.Button(bar, text=text, style="Toolbar.TButton", command=handler).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="打开目录", style="Toolbar.TButton", command=self._open_folder).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="下载目录…", style="Toolbar.TButton", command=self._choose_dir).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(bar, text="监听剪贴板", variable=self.clipboard_on).pack(side="right")
        ttk.Label(bar, text="连接数").pack(side="right", padx=(0, 6))
        self.conn_var = tk.IntVar(value=self.connections)
        ttk.Spinbox(bar, from_=1, to=32, width=4, textvariable=self.conn_var,
                    command=self._apply_connections).pack(side="right", padx=(0, 12))

    def _build_table(self):
        frame = ttk.Frame(self, padding=(10, 0))
        frame.pack(fill="both", expand=True)
        columns = ("name", "size", "progress", "speed", "eta", "status")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        widths = {"name": 360, "size": 100, "progress": 190, "speed": 110, "eta": 90, "status": 230}
        titles = {"name": "文件名", "size": "大小", "progress": "进度", "speed": "速度", "eta": "剩余", "status": "状态"}
        for column in columns:
            self.tree.heading(column, text=titles[column])
            self.tree.column(column, width=widths[column], anchor="w" if column == "name" else "center")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self._open_file())
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _build_statusbar(self):
        self.status = tk.StringVar(value="就绪")
        bar = ttk.Frame(self, padding=(10, 4))
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, textvariable=self.status, foreground="#444").pack(side="left")
        self.api_label = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.api_label, foreground="#0a7").pack(side="right")
        self.token_btn = ttk.Button(bar, text="复制配对码", width=12, command=self._copy_token)
        self.token_btn.pack(side="right", padx=(0, 8))

    def _copy_token(self):
        token = getattr(self.api, "token", "")
        if not token:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(token)
            self.token_btn.configure(text="已复制")
        except tk.TclError:
            self.token_btn.configure(text="复制失败")
        self.after(1500, lambda: self.token_btn.configure(text="复制配对码"))

    # ------------------------------------------------------------------ actions
    def _apply_connections(self):
        try:
            self.connections = max(1, min(int(self.conn_var.get()), 32))
        except (tk.TclError, ValueError):
            self.connections = 8

    def _open_add(self):
        AddDialog(self, self.add_task)

    def add_task(self, url: str, directory: str | None = None, connections: int | None = None,
                 headers: dict | None = None):
        task = Download(url, directory or self.download_dir, connections or self.connections,
                        headers=headers)
        task.stats.status = "准备中"
        self.tasks.append(task)
        self._insert_row(task)
        threading_start = getattr(task, "start", None)
        try:
            threading_start()
        except Exception as exc:  # noqa: BLE001 - probe failures land here
            task.stats.status = "error"
            task.stats.error = str(exc)
        self._refresh_row(task)

    def _enqueue_from_browser(self, url: str, headers: dict | None = None):
        headers = headers or {}
        cleaned = {k: v for k, v in headers.items() if v}
        self.after(0, lambda: self.add_task(url, self.download_dir, self.connections, cleaned))

    def _enqueue_from_clipboard(self, url: str):
        self.after(0, lambda: self.add_task(url, self.download_dir, self.connections))

    def _selected(self) -> Download | None:
        selection = self.tree.selection()
        if not selection:
            return None
        task_id = selection[0]
        for task in self.tasks:
            if self.rows.get(id(task)) == task_id:
                return task
        return None

    def _pause(self):
        task = self._selected()
        if task and task.stats.status in ("downloading", "准备中", "queued"):
            task.pause()

    def _resume(self):
        task = self._selected()
        if task and task.stats.status in ("paused", "error", "失败"):
            task._threads = []
            task.start()

    def _remove(self):
        task = self._selected()
        if not task:
            return
        if task.stats.status not in ("complete",):
            if not messagebox.askyesno("删除任务", "任务未完成，确定删除并丢弃已下载的数据？"):
                return
            task.reset()
        self.tasks.remove(task)
        self.tree.delete(self.rows.pop(id(task), None))

    def _choose_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.download_dir, title="选择默认下载目录")
        if not chosen:
            return
        self.download_dir = chosen
        self.settings["download_dir"] = chosen
        save_settings(self.settings)
        self.status.set(f"默认下载目录已改为 {chosen}")

    def _open_folder(self):
        task = self._selected()
        directory = os.path.dirname(task.path) if task and task.path else self.download_dir
        self._reveal(directory)

    def _open_file(self):
        task = self._selected()
        if task and task.path and os.path.exists(task.path):
            try:
                os.startfile(task.path)  # noqa: S606 - deliberate user action
            except OSError:
                pass

    @staticmethod
    def _reveal(path: str):
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", path]) if os.path.isfile(path) else subprocess.Popen(["explorer", path])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError:
            pass

    # ------------------------------------------------------------------- table
    def _insert_row(self, task: Download) -> str:
        name = getattr(task, "fixed_filename", None) or display_name(task.url)
        item = self.tree.insert("", "end", values=(name, "…", "0%", "—", "—", "准备中"))
        self.rows[id(task)] = item
        return item

    def _refresh_row(self, task: Download):
        item = self.rows.get(id(task))
        if not item or not self.tree.exists(item):
            return
        name = os.path.basename(task.path) if task.path else (
            getattr(task, "fixed_filename", None) or display_name(task.url)
        )
        size = human_size(task.size) if task.size and task.size > 0 else "未知"
        progress = f"{task.progress * 100:5.1f}%  {human_size(task.stats.downloaded)}"
        status = task.stats.status
        if status == "error":
            reason = (task.stats.error or "").strip().replace("\n", " ")
            status = ("失败：" + reason[:60]) if reason else "失败"
        self.tree.item(item, values=(
            name, size, progress, human_speed(task.stats.speed), human_eta(task.stats.eta), status
        ))

    def _on_select(self, _event=None):
        task = self._selected()
        if not task:
            return
        if task.stats.status == "error" and task.stats.error:
            self.status.set(f"失败原因：{task.stats.error}")
        elif task.stats.error:
            self.status.set(f"提示：{task.stats.error}")
        elif task.path:
            self.status.set(task.path)

    def _tick(self):
        for task in list(self.tasks):
            self._refresh_row(task)
        active = [t for t in self.tasks if t.stats.status == "downloading"]
        if active:
            total_speed = sum(t.stats.speed for t in active)
            self.status.set(f"{len(active)} 个任务进行中 · 合计 {human_speed(total_speed)}")
        else:
            done = sum(1 for t in self.tasks if t.stats.status == "complete")
            self.status.set(f"共 {len(self.tasks)} 个任务，已完成 {done}")
        self.api_label.set(self.api_state)
        if self.clipboard_on.get():
            self.clip.poll()
        self.after(600, self._tick)


def main(pending_url: str | None = None):
    app = App(pending_url=pending_url)
    app.mainloop()


if __name__ == "__main__":
    main()

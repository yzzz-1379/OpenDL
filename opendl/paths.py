"""配置存放位置。

默认放用户目录（%LOCALAPPDATA%\\OpenDL）；如果程序目录里有 portable.txt，
就改成放在程序目录下的 OpenDL-data\\，整个文件夹拷走即完成迁移（绿色版）。
"""

from __future__ import annotations

import os
import sys

MARKER = "portable.txt"
DATA_NAME = "OpenDL-data"


def base_dir() -> str:
    """程序所在目录：打包后是 exe 目录，源码运行时是包的上层目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_portable() -> bool:
    return os.path.isfile(os.path.join(base_dir(), MARKER))


def config_dir() -> str:
    if is_portable():
        target = os.path.join(base_dir(), DATA_NAME)
    else:
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        target = os.path.join(root, "OpenDL")
    try:
        os.makedirs(target, exist_ok=True)
    except OSError:
        pass
    return target


def config_file(name: str) -> str:
    return os.path.join(config_dir(), name)


def resource_path(name: str) -> str:
    """随程序分发的只读资源（图标等）：打包后在临时解包目录里。"""
    if getattr(sys, "frozen", False):
        for base in (getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.abspath(sys.executable))):
            if base:
                candidate = os.path.join(base, name)
                if os.path.exists(candidate):
                    return candidate
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)

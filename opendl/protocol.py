"""Register the opendl:// URL scheme so a web page can start the app."""

from __future__ import annotations

import os
import subprocess
import sys

SCHEME = "opendl"
REG_PATH = rf"Software\Classes\{SCHEME}"


def _command() -> str:
    # 打包成 exe 后，协议要直接指向 exe 自身，而不是 pythonw + launch.py
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    here = os.path.dirname(os.path.abspath(__file__))
    launcher = os.path.join(here, "launch.py")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return f'"{pythonw}" "{launcher}" "%1"'


def register() -> str:
    """Register the scheme for the current user (no admin needed)."""
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:OpenDL Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _command())
    return _command()


def unregister() -> None:
    import winreg

    def drop(path: str) -> None:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass

    drop(REG_PATH + r"\shell\open\command")
    drop(REG_PATH + r"\shell\open")
    drop(REG_PATH + r"\shell")
    drop(REG_PATH)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "register"
    if action == "remove":
        unregister()
        print(f"已移除 {SCHEME}:// 协议")
    else:
        print(f"已注册 {SCHEME}:// -> {register()}")

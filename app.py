"""Standalone application dispatcher.

The GUI auto-elevates on Windows so physical-drive reads are permitted. CLI
subcommands run in place and simply report when elevation is missing, so they
never block on a UAC prompt in non-interactive use.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def _is_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_gui_elevated() -> bool | None:
    """Return True when already elevated, False after launching a child, None on failure."""
    if os.name != "nt" or _is_admin():
        return True
    if getattr(sys, "frozen", False):
        executable = sys.executable
        arguments = subprocess.list2cmdline(sys.argv[1:])
        directory = str(Path(executable).parent)
    else:
        executable = sys.executable
        arguments = subprocess.list2cmdline(["-m", "iPodRawDriveDumper", *sys.argv[1:]])
        directory = str(Path(__file__).resolve().parent.parent)
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, arguments, directory, 1)
    if result <= 32:
        print("Administrator elevation was declined or failed.", file=sys.stderr)
        return None
    return False


def _attach_parent_console() -> None:
    """For a windowed frozen build invoked with CLI args, attach to the caller's console
    so stdout/stderr are visible and redirectable."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        ATTACH_PARENT_PROCESS = 0xFFFFFFFF
        if ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    except Exception:
        pass


def main() -> int:
    # CLI subcommands: run in place. Never trigger a UAC relaunch that would block scripted use.
    if len(sys.argv) > 1:
        _attach_parent_console()
        try:
            from .cli import main as cli_main
        except ImportError:
            from iPodRawDriveDumper.cli import main as cli_main
        return cli_main()
    # GUI: auto-elevate so raw physical reads are permitted.
    elevation = _relaunch_gui_elevated()
    if elevation is None:
        return 4
    if elevation is False:
        return 0
    try:
        from .gui import launch_gui
    except ImportError:
        from iPodRawDriveDumper.gui import launch_gui
    launch_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The one thing that lets daily use skip the terminal: an icon you double-click to open the web page."""
from __future__ import annotations

import shlex
import stat
import sys
from pathlib import Path
from typing import Optional

from .errors import ValidationError

_PROJECT = Path(__file__).resolve().parent.parent


def create(folder: Optional[str] = None, data_dir: Optional[str] = None, port: int = 8765,
           python: Optional[str] = None, system: Optional[str] = None) -> Path:
    """Write the icon's file and return its path. It runs this copy of Freshlist with this Python and your data."""
    target = Path(folder).expanduser() if folder else Path.home() / "Desktop"
    if not target.is_dir():
        raise ValidationError(f"Could not find the folder {target}. Choose one with: shortcut --to <folder>")
    python = python or sys.executable
    data = str(Path(data_dir or ".").resolve())
    system = system or sys.platform
    if system.startswith("win"):
        path = target / "Freshlist.bat"
        path.write_text(f'@echo off\r\ncd /d "{_PROJECT}"\r\n"{python}" -m jobfinder --data-dir "{data}" ui --port {port}\r\n',
                        encoding="utf-8")
        return path
    path = target / ("Freshlist.command" if system == "darwin" else "Freshlist.sh")
    path.write_text(f"#!/bin/sh\ncd {shlex.quote(str(_PROJECT))} || exit 1\n"
                    f"exec {shlex.quote(python)} -m jobfinder --data-dir {shlex.quote(data)} ui --port {port}\n",
                    encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path

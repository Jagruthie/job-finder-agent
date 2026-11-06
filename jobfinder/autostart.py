"""Keeps the web page running in the background on a Mac, so a pinned Safari tab opens it at any time.

It adds a login item for your user only (a launchd agent): Freshlist starts when you log in, and starts again if it
crashes. Turning it off removes the agent completely.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from .errors import ValidationError

LABEL = "local.freshlist.ui"
_PROJECT = Path(__file__).resolve().parent.parent


def agent_path(folder: Optional[str] = None) -> Path:
    return (Path(folder) if folder else Path.home() / "Library" / "LaunchAgents") / f"{LABEL}.plist"


def log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "Freshlist.log"


def agent(data_dir: Optional[str] = None, port: int = 8765, python: Optional[str] = None) -> dict:
    """The agent's settings: run this copy of Freshlist with this Python and your data, never opening a browser."""
    log = str(log_path())
    return {
        "Label": LABEL,
        "ProgramArguments": [python or sys.executable, "-m", "jobfinder", "--data-dir", str(Path(data_dir or ".").resolve()),
                             "ui", "--no-open", "--port", str(port)],
        "WorkingDirectory": str(_PROJECT),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},     # start again after a crash, not after a normal stop
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def _check_mac(system: Optional[str]) -> None:
    if (system or sys.platform) != "darwin":
        raise ValidationError("Starting in the background is only for macOS. Use the desktop icon instead: "
                              "python -m jobfinder shortcut")


def turn_on(data_dir: Optional[str] = None, port: int = 8765, folder: Optional[str] = None,
            run: Callable = subprocess.run, system: Optional[str] = None) -> Path:
    """Write the agent and start it now. Running it again replaces the old one."""
    _check_mac(system)
    path = agent_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)      # an older one, if any
    path.write_bytes(plistlib.dumps(agent(data_dir, port)))
    started = run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
    if started.returncode != 0:
        raise ValidationError(f"macOS did not start Freshlist: {(started.stderr or '').strip() or 'no reason given'}. "
                              f"The details are in {log_path()}.")
    return path


def turn_off(folder: Optional[str] = None, run: Callable = subprocess.run, system: Optional[str] = None) -> bool:
    """Stop the agent and remove it. Returns whether there was one."""
    _check_mac(system)
    path = agent_path(folder)
    run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    if path.exists():
        path.unlink()
        return True
    return False

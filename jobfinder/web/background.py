"""One run at a time in the background, because a run takes minutes and a page should not hang while it works."""
from __future__ import annotations

import threading
from typing import Callable, Optional

from ..errors import FreshlistError
from ..sources import SourceError


class BackgroundRun:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._state = {"status": "idle", "done": 0, "total": 0, "current": "", "error": ""}
        self.result = None

    def start(self, work: Callable) -> bool:
        """Start `work(progress)` unless a run is already going. Returns whether it started."""
        with self._lock:
            if self._state["status"] == "running":
                return False
            self._state = {"status": "running", "done": 0, "total": 0, "current": "", "error": ""}
            self.result = None
            self._thread = threading.Thread(target=self._work, args=(work,), daemon=True)
            self._thread.start()
        return True

    def _work(self, work: Callable) -> None:
        result, error = None, ""
        try:
            result = work(self._progress)
        except (FreshlistError, SourceError) as problem:
            error = str(problem)
        except Exception as problem:   # the page must never stay stuck on "running"
            error = f"Something went wrong during the run ({type(problem).__name__}: {problem})."
        with self._lock:
            self.result = result
            self._state.update(status="failed" if error else "finished", error=error, current="")

    def _progress(self, step: dict) -> None:
        with self._lock:
            self._state.update(done=step["done"], total=step["total"], current=step["company"])

    def clear(self) -> None:
        """Forget a finished result, unless the work is still going."""
        with self._lock:
            if self._state["status"] != "running":
                self._state = {"status": "idle", "done": 0, "total": 0, "current": "", "error": ""}
                self.result = None

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def running(self) -> bool:
        return self.state()["status"] == "running"

    def wait(self, timeout: float = 10) -> None:
        thread = self._thread
        if thread:
            thread.join(timeout)

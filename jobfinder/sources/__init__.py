"""Readers for the public job endpoints of career-page platforms.

Each reader turns one company's public job list into normalized Job objects.
Only documented, unauthenticated GET endpoints are used.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, List

from ..models import Job

USER_AGENT = "jobfinder-agent/0.1 (personal job search; low request volume)"


class SourceError(Exception):
    """A company's job list could not be read."""


def http_get_json(url: str, timeout: int = 20) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise SourceError("not found (check the board name in companies.csv)")
        raise SourceError(f"HTTP {error.code}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise SourceError(str(error))


def fetch_board(platform: str, board: str, company: str,
                get_json: Callable[[str], Any] = http_get_json) -> List[Job]:
    from . import ashby, greenhouse, lever

    readers = {"greenhouse": greenhouse.fetch, "lever": lever.fetch, "ashby": ashby.fetch}
    key = platform.strip().lower()
    if key not in readers:
        raise SourceError(f"unknown platform '{platform}' (use greenhouse, lever or ashby)")
    return readers[key](board.strip(), company.strip() or board, get_json)

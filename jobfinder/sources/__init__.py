"""Readers for the public job endpoints of career-page platforms.

Each reader turns one company's public job list into normalized Job objects.
Only documented, unauthenticated GET endpoints are used.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Any, Callable, List

from ..models import Job

USER_AGENT = "jobfinder-agent/0.1 (personal job search; low request volume)"


class SourceError(Exception):
    """A company's job list could not be read."""


_SYSTEM_CERTS = "/etc/ssl/cert.pem"   # macOS and many Linux systems keep their trusted certificates here
_CERT_HELP = ("could not check the website's security certificate. On a Mac with Python from python.org, "
              "run 'Install Certificates.command' in your Python folder, or run: pip install certifi")


def ssl_context() -> ssl.SSLContext:
    """Certificates are always checked. Python from python.org on a Mac ships with none,
    so fall back to certifi if installed, else to the system's own certificate file."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    context = ssl.create_default_context()
    if not context.get_ca_certs() and os.path.exists(_SYSTEM_CERTS):
        context.load_verify_locations(_SYSTEM_CERTS)
    return context


def http_get_json(url: str, timeout: int = 20) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise SourceError("not found (check the board name in companies.csv)")
        raise SourceError(f"HTTP {error.code}")
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise SourceError(_CERT_HELP)
        raise SourceError(str(error.reason))
    except (TimeoutError, json.JSONDecodeError) as error:
        raise SourceError(str(error))


def fetch_board(platform: str, board: str, company: str,
                get_json: Callable[[str], Any] = http_get_json) -> List[Job]:
    from . import ashby, greenhouse, lever

    readers = {"greenhouse": greenhouse.fetch, "lever": lever.fetch, "ashby": ashby.fetch}
    key = platform.strip().lower()
    if key not in readers:
        raise SourceError(f"unknown platform '{platform}' (use greenhouse, lever or ashby)")
    return readers[key](board.strip(), company.strip() or board, get_json)

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any, Optional

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_BREAKS = re.compile(r"</(p|li|div|h\d)>|<br\s*/?>", re.I)


def strip_html(value: Optional[str]) -> str:
    """Turn (possibly entity-escaped) HTML into plain text."""
    if not value:
        return ""
    text = html.unescape(value)
    text = _BREAKS.sub(" ", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return _WS.sub(" ", text).strip()


def norm(value: Optional[str]) -> str:
    """Lowercase, drop apostrophes, turn everything else non-alphanumeric into spaces."""
    text = (value or "").lower().replace("&", " and ")
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _WS.sub(" ", text).strip()


def has_phrase(text_norm: str, phrase_norm: str) -> bool:
    if not phrase_norm:
        return False
    return f" {phrase_norm} " in f" {text_norm} "


def count_phrase(text_norm: str, phrase_norm: str) -> int:
    if not phrase_norm:
        return 0
    pattern = r"(?<![a-z0-9])" + re.escape(phrase_norm) + r"(?![a-z0-9])"
    return len(re.findall(pattern, text_norm))


def parse_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO string or an epoch (seconds or milliseconds) into an aware UTC datetime."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        text = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"), text)
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None

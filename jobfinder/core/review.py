"""The weekly review and the checkpoint. Only reports and suggests: it never changes your files."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import List, Optional

from .. import storage
from ..config import file_path
from . import watchlist


def _parse(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _checkpoint(cfg: dict, log: dict, today: date) -> dict:
    days, needed = cfg["checkpoint_days"], cfg["checkpoint_min_matches"]
    first = _parse(log.get("first_live_run", ""))
    base = {"days": days, "needed": needed, "first_live_run": log.get("first_live_run", ""), "warning": False}
    if first is None:
        return {**base, "state": "not_started", "found": 0, "message": "No live run yet, so the checkpoint has not started."}
    end = first + timedelta(days=days)
    found = sum(first <= (_parse(m.get("date", "")) or first) < end for m in log.get("matches", []))
    base["found"] = found
    if found >= needed:
        return {**base, "state": "passed", "message": f"Passed: {found} live matches in the first {days} days."}
    if today < end:
        return {**base, "state": "running", "message": f"Day {(today - first).days + 1} of {days}: {found} of "
                                                       f"{needed} live matches so far."}
    return {**base, "state": "warning", "warning": True,
            "message": f"Only {found} live matches in the first {days} days (at least {needed} expected). "
                       "Consider widening your watch-list or your role filter."}


def review(cfg: dict, today: Optional[date] = None) -> dict:
    """{"date", "companies", "quiet", "failed_last_run", "checkpoint"}. A company is flagged quiet after
    quiet_after_days with no match; only live runs count, so the time before your first run is not held against it."""
    today = today or date.today()
    log = storage.read_json(file_path(cfg, "run_log_file"), {}) or {}
    matches: List[dict] = log.get("matches", [])
    recent_from = today - timedelta(days=cfg["review_recent_days"])
    total = Counter(m["company"] for m in matches)
    recent = Counter(m["company"] for m in matches if (_parse(m.get("date", "")) or date.min) >= recent_from)
    first_run = _parse(log.get("first_live_run", ""))
    companies = []
    for row in watchlist.read(cfg):
        last_match = _parse(row["last_match_date"])
        since = [d for d in (last_match or _parse(row["added_date"]), first_run) if d]
        days = (today - max(since)).days if first_run and since else None
        quiet = row["status"] == "active" and days is not None and days >= cfg["quiet_after_days"]
        companies.append({"company": row["company"], "platform": row["platform"], "tier": row["tier"],
                          "status": row["status"], "matches_total": total[row["company"]],
                          "matches_recent": recent[row["company"]], "last_match_date": row["last_match_date"],
                          "days_without_match": days, "flag_quiet": quiet,
                          "suggestion": f"No match in {days} days. Consider swapping it." if quiet else ""})
    runs = log.get("runs", [])
    return {"date": today.isoformat(), "companies": companies, "quiet": [c for c in companies if c["flag_quiet"]],
            "failed_last_run": runs[-1].get("failed", []) if runs else [], "checkpoint": _checkpoint(cfg, log, today)}

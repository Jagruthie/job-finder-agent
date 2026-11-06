"""The optional two-day rotation: Day A to find and apply, Day B to reach out and widen. Returns a checklist as data.

It never fetches anything: opening today's checklist must not read job sites. Running Freshlist is a step on it.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from ..config import APPLIED_STATUSES
from ..errors import ValidationError
from . import runner, tracker


def which_day(day: date) -> str:
    """Counting days without a break, so the rotation stays A, B, A, B across month ends (a date's own number
    would give two odd days in a row on the 31st and the 1st)."""
    return "A" if day.toordinal() % 2 == 0 else "B"


def _step(step_id: str, text: str, done: bool = False) -> dict:
    return {"id": step_id, "text": text, "done": done}


def outreach_pending(cfg: dict, day: date) -> list:
    """Applications from the last few days where outreach is not done."""
    since = day - timedelta(days=cfg["outreach_window_days"])
    pending = []
    for row in tracker.read(cfg):
        try:
            applied = date.fromisoformat(row["applied_date"]) if row["applied_date"] else None
        except ValueError:    # tracker.csv can be edited by hand; one mistyped date must not hide the rest
            continue
        if row["status"] in APPLIED_STATUSES and applied and since <= applied <= day \
                and row["outreach_status"] in ("", "not_started"):
            pending.append(row)
    return pending


def checklist(cfg: dict, day: Optional[date] = None, override: str = "") -> dict:
    """{"date", "day", "chosen_by", "steps", ...}. Day A adds new_matches, ran_today and to_apply; Day B adds
    outreach_pending and followups_due. `override` ("A" or "B") replaces the day worked out from the date."""
    day = day or date.today()
    override = (override or "").strip().upper()
    if override and override not in ("A", "B"):
        raise ValidationError("The day must be A or B.")
    label = override or which_day(day)
    result = {"date": day.isoformat(), "day": label, "chosen_by": "override" if override else "date"}
    if label == "A":
        last = runner.last_run(cfg)
        ran_today = last.get("date") == day.isoformat()
        links = tracker.logged_links(cfg)
        matches = [{**{k: v for k, v in match.items() if k != "details"}, "in_tracker": match["apply_link"].lower() in links}
                   for match in (last.get("matches", []) if ran_today else [])]
        result.update(ran_today=ran_today, new_matches=matches, to_apply=tracker.list_entries(cfg, "to_apply"), steps=[
            _step("run", "Run Freshlist to check your watch-list.", ran_today),
            _step("read", "Read the new matches and open each apply link."),
            _step("tailor", "Tailor your resume for the ones worth applying to."),
            _step("log_changes", "Log each resume change in the tracker, with how you would explain it in an interview."),
            _step("apply", "Apply yourself. Freshlist never applies for you."),
            _step("log_applied", "Mark each one applied in the tracker."),
        ])
    else:
        pending, followups = outreach_pending(cfg, day), tracker.due(cfg, day)
        minutes = cfg["linkedin_scan_minutes"]
        result.update(outreach_pending=pending, followups_due=followups, steps=[
            _step("outreach", f"Reach out for recent applications without outreach ({len(pending)}).", not pending),
            _step("followups", f"Send the follow-ups that are due ({len(followups)}).", not followups),
            _step("linkedin", f"Scan LinkedIn yourself, capped at {minutes} minutes."),
            _step("log_finds", "Log anything you find there in the tracker, with source linkedin."),
        ])
    return result

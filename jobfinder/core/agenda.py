"""Dates across your jobs: apply deadlines, interviews, follow-ups and learn-by dates, as a calendar and a job list.

Reports only. Most job pages do not state a deadline, so these are dates you enter yourself.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import List, Optional, Tuple

from ..config import APPLIED_STATUSES
from . import learning, tracker

FINISHED = ("rejected", "closed", "skipped")
KIND_LABELS = {"deadline": "Apply by", "interview": "Interview", "followup": "Follow up", "learn": "Learn by"}


def _parse(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:    # the files can be edited by hand; one mistyped date must not hide the rest
        return None


def _job(entry: dict) -> str:
    return f"{entry['role']} at {entry['company']}"


def events(cfg: dict) -> List[dict]:
    """Every date still worth seeing, soonest first: {date, kind, label, text, entry_id}. A deadline stops counting
    once you have applied, and a finished job drops out."""
    entries = {row["id"]: row for row in tracker.read(cfg) if row["status"] not in FINISHED}
    found = []

    def add(when: str, kind: str, entry: dict, text: str) -> None:
        if _parse(when):
            found.append({"date": when, "kind": kind, "label": KIND_LABELS[kind], "text": text, "entry_id": entry["id"]})

    for entry in entries.values():
        if entry["status"] == "to_apply":
            add(entry["apply_deadline"], "deadline", entry, _job(entry))
        add(entry["interview_date"], "interview", entry, _job(entry))
        if entry["status"] == "applied":
            add(entry["followup_date"], "followup", entry, _job(entry))
    for item in learning.read(cfg):
        if item["status"] != "done" and item["entry_id"] in entries:
            add(item["learn_by"], "learn", entries[item["entry_id"]], f"{item['topic']} ({entries[item['entry_id']]['company']})")
    return sorted(found, key=lambda event: (event["date"], event["kind"], event["text"]))


def upcoming(cfg: dict, today: Optional[date] = None, days: int = 7) -> dict:
    """{"missed": apply deadlines that have passed while still to apply, "soon": anything in the next `days` days}."""
    today = today or date.today()
    all_events = events(cfg)
    return {"missed": [e for e in all_events if e["kind"] == "deadline" and _parse(e["date"]) < today],
            "soon": [e for e in all_events if today <= _parse(e["date"]) <= today + timedelta(days=days)]}


def month_of(text: str, today: date) -> Tuple[int, int]:
    """(year, month) from text such as 2026-11, or this month if it cannot be read."""
    try:
        year, month = (int(part) for part in (text or "").split("-"))
        date(year, month, 1)
        return year, month
    except ValueError:
        return today.year, today.month


def month_view(cfg: dict, year: int, month: int, today: Optional[date] = None) -> dict:
    """A month as weeks of days, Monday first, each with its events, plus the month's events as a plain list."""
    today = today or date.today()
    in_month = [event for event in events(cfg) if _parse(event["date"]).year == year and _parse(event["date"]).month == month]
    weeks = [[{"date": day.isoformat(), "day": day.day, "in_month": day.month == month, "is_today": day == today,
               "events": [event for event in in_month if event["date"] == day.isoformat()]} for day in week]
             for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)]
    previous = date(year, month, 1) - timedelta(days=1)
    following = date(year, month, 28) + timedelta(days=7)
    return {"label": date(year, month, 1).strftime("%B %Y"), "weeks": weeks, "events": in_month,
            "previous": previous.strftime("%Y-%m"), "next": following.strftime("%Y-%m"),
            "this_month": today.strftime("%Y-%m"), "is_this_month": (year, month) == (today.year, today.month)}


def _target(entry: dict, today: date) -> Optional[Tuple[str, str]]:
    """The date to learn things by for this job, when you have one: the interview, or else the deadline to apply."""
    interview = _parse(entry["interview_date"])
    if interview and interview >= today:
        return entry["interview_date"], "Before the interview"
    deadline = _parse(entry["apply_deadline"])
    if entry["status"] == "to_apply" and deadline and deadline >= today:
        return entry["apply_deadline"], "Before the deadline to apply"
    return None


def jobs_to_prepare(cfg: dict, show_finished: bool = False, today: Optional[date] = None) -> List[dict]:
    """Each tracker entry with its things to learn. Nearest interview or deadline first, then the newest added."""
    today = today or date.today()
    items = learning.read(cfg)
    jobs = []
    for position, entry in enumerate(tracker.read(cfg)):
        if entry["status"] in FINISHED and not show_finished:
            continue
        target = _target(entry, today)
        mine = [{**item, "due": item["learn_by"] if _parse(item["learn_by"]) else (target[0] if target else ""),
                 "due_label": "Learn by" if _parse(item["learn_by"]) else (target[1] if target else "")}
                for item in items if item["entry_id"] == entry["id"]]
        dates = [d for d in (_parse(entry["interview_date"]), _parse(entry["apply_deadline"])) if d and d >= today]
        jobs.append({**entry, "items": sorted(mine, key=lambda item: item["status"] == "done"), "target": target,
                     "left": sum(item["status"] != "done" for item in mine), "next_date": min(dates) if dates else None,
                     "_position": position})
    return sorted(jobs, key=lambda job: (job["next_date"] is None, job["next_date"] or today, -job["_position"]))

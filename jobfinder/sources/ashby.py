"""Ashby public job posting API.

Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
The docs confirm `publishedAt`, `location`, `isRemote` and compensation fields. The names of
the link and description fields were not confirmed, so several likely names are tried.
"""
from __future__ import annotations

from typing import Any, Callable, List

from ..models import Job
from ..textutil import parse_dt, strip_html


def _first(item: dict, *names: str) -> str:
    for name in names:
        value = item.get(name)
        if value:
            return value
    return ""


def _pay(item: dict) -> str:
    comp = item.get("compensation") or {}
    return (comp.get("scrapeableCompensationSalarySummary")
            or comp.get("compensationTierSummary") or "")


def fetch(board: str, company: str, get_json: Callable[[str], Any]) -> List[Job]:
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true")
    jobs: List[Job] = []
    for item in (data or {}).get("jobs", []):
        if item.get("isListed") is False:
            continue  # only reachable by direct link, not a public listing
        description = _first(item, "descriptionPlain") or strip_html(_first(item, "descriptionHtml"))
        location = item.get("location", "") or ""
        if item.get("isRemote") and "remote" not in location.lower():
            location = f"{location} (Remote)".strip()
        jobs.append(Job(
            platform="ashby",
            board=board,
            company=company,
            job_id=str(_first(item, "id", "jobId") or _first(item, "jobUrl", "applyUrl")),
            title=item.get("title", "") or "",
            location=location,
            url=_first(item, "jobUrl", "applyUrl", "url"),
            description=description,
            posted_at=parse_dt(_first(item, "publishedAt", "publishedDate")),
            pay_text=_pay(item),
        ))
    return jobs

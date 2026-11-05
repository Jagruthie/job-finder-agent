"""Greenhouse Job Board API (public, no login for GET).

Docs: https://developers.greenhouse.io/job-board.html
`first_published` is when the job was first posted (checked on a live board, November 2026). `updated_at` changes
whenever a company edits a job, so it is used only if `first_published` is missing.
"""
from __future__ import annotations

from typing import Any, Callable, List

from ..models import Job
from ..textutil import norm, parse_dt, strip_html


def fetch(board: str, company: str, get_json: Callable[[str], Any]) -> List[Job]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    jobs: List[Job] = []
    for item in (data or {}).get("jobs", []):
        location = (item.get("location") or {}).get("name", "") or ""
        offices = [o.get("name", "") for o in item.get("offices") or [] if o.get("name")]
        if norm(location) == "remote" and offices:
            location = f"Remote ({', '.join(offices)})"   # 'Remote' alone hides that the job is for Poland only
        jobs.append(Job(
            platform="greenhouse",
            board=board,
            company=company,
            job_id=str(item.get("id", "")),
            title=item.get("title", "") or "",
            location=location,
            url=item.get("absolute_url", "") or "",
            description=strip_html(item.get("content", "")),
            posted_at=parse_dt(item.get("first_published")) or parse_dt(item.get("updated_at")),
        ))
    return jobs

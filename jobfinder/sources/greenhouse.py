"""Greenhouse Job Board API (public, no login for GET).

Docs: https://developers.greenhouse.io/job-board.html
Note: `updated_at` is when the job was last changed, not when it was first posted,
so an old job that a company edited can look fresh.
"""
from __future__ import annotations

from typing import Any, Callable, List

from ..models import Job
from ..textutil import parse_dt, strip_html


def fetch(board: str, company: str, get_json: Callable[[str], Any]) -> List[Job]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    jobs: List[Job] = []
    for item in (data or {}).get("jobs", []):
        location = (item.get("location") or {}).get("name", "") or ""
        jobs.append(Job(
            platform="greenhouse",
            board=board,
            company=company,
            job_id=str(item.get("id", "")),
            title=item.get("title", "") or "",
            location=location,
            url=item.get("absolute_url", "") or "",
            description=strip_html(item.get("content", "")),
            posted_at=parse_dt(item.get("updated_at")),
        ))
    return jobs

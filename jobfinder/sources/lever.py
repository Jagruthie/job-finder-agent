"""Lever Postings API (public, no login for GET).

Docs: https://github.com/lever/postings-api
The docs page does not list a posted date. Real responses usually carry `createdAt`
(epoch milliseconds); if it is missing the job gets no date and the agent falls back
to the day it first saw the job.
"""
from __future__ import annotations

from typing import Any, Callable, List

from ..models import Job
from ..textutil import parse_dt, strip_html


def _pay(item: dict) -> str:
    salary = item.get("salaryRange") or {}
    low, high = salary.get("min"), salary.get("max")
    if low is None and high is None:
        return strip_html(item.get("salaryDescriptionPlain") or item.get("salaryDescription") or "")
    currency = salary.get("currency", "")
    interval = salary.get("interval", "")
    return f"{currency} {low}-{high} {interval}".strip()


def fetch(board: str, company: str, get_json: Callable[[str], Any]) -> List[Job]:
    data = get_json(f"https://api.lever.co/v0/postings/{board}?mode=json")
    jobs: List[Job] = []
    for item in data or []:
        categories = item.get("categories") or {}
        parts = [item.get("descriptionPlain", ""), item.get("openingPlain", "")]
        for block in item.get("lists") or []:
            parts.append(block.get("text", ""))
            parts.append(strip_html(block.get("content", "")))
        parts.append(item.get("additionalPlain", ""))
        location = categories.get("location", "") or ""
        if item.get("workplaceType"):
            location = f"{location} ({item['workplaceType']})".strip()
        jobs.append(Job(
            platform="lever",
            board=board,
            company=company,
            job_id=str(item.get("id", "")),
            title=item.get("text", "") or "",
            location=location,
            url=item.get("hostedUrl") or item.get("applyUrl") or "",
            description=" ".join(p for p in parts if p),
            posted_at=parse_dt(item.get("createdAt")),
            pay_text=_pay(item),
        ))
    return jobs

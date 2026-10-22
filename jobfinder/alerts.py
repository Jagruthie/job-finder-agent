"""Read job-alert emails you saved as .eml files (for example from Gmail: More > Download message).

This covers job sites the agent cannot read directly. Only LinkedIn alert emails are understood so far.
Everything is read on this computer. Links in these emails carry tracking and one-time login tokens,
so only the clean job address (https://www.linkedin.com/jobs/view/<id>/) is ever kept.
"""
from __future__ import annotations

import email
import re
from email import policy
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Tuple

from .models import Job

_SEPARATOR = re.compile(r"^-{10,}$")
_LINKEDIN_VIEW = re.compile(r"^View job:\s*(\S+)", re.I)
_LINKEDIN_ID = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", re.I)
DATE_BASIS = "alert email date (the job may be older)"


def _plain_text(message) -> str:
    body = message.get_body(preferencelist=("plain",))
    return body.get_content() if body is not None else ""


def parse_linkedin(text: str, sent) -> List[Job]:
    """Each job is a group of lines just above its 'View job:' link: title, company, location,
    then optional extras such as 'Apply with resume & profile'."""
    jobs: List[Job] = []
    group: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        view = _LINKEDIN_VIEW.match(line)
        if view:
            job_id = _LINKEDIN_ID.search(view.group(1))
            if job_id and len(group) >= 2:
                jobs.append(Job(platform="linkedin", board="alerts", company=group[1], job_id=job_id.group(1),
                                title=group[0], location=group[2] if len(group) > 2 else "",
                                url=f"https://www.linkedin.com/jobs/view/{job_id.group(1)}/",
                                posted_at=sent, date_basis=DATE_BASIS))
            group = []
        elif not line or _SEPARATOR.match(line) or "://" in line:
            group = []
        else:
            group.append(line)
    return jobs


def read_alerts(folder: str) -> Tuple[List[Job], List[str]]:
    """Jobs from every .eml file in the folder (each job once, newest email wins), and notes about
    files that could not be used."""
    found: Dict[str, Job] = {}
    notes: List[str] = []
    directory = Path(folder)
    for file in sorted(directory.glob("*.eml")) if directory.exists() else []:
        with open(file, encoding="utf-8", errors="ignore") as handle:
            message = email.message_from_file(handle, policy=policy.default)
        sender = str(message.get("From", "")).lower()
        try:
            sent = parsedate_to_datetime(str(message.get("Date")))
        except (TypeError, ValueError):
            sent = None
        if "linkedin.com" not in sender:
            notes.append(f"{file.name}: alerts from {sender or 'this sender'} are not supported yet")
            continue
        jobs = parse_linkedin(_plain_text(message), sent)
        if not jobs:
            notes.append(f"{file.name}: no jobs found (the email layout may have changed)")
        for job in jobs:
            old = found.get(job.key)
            if old is None or (job.posted_at and old.posted_at and job.posted_at > old.posted_at):
                found[job.key] = job
    return list(found.values()), notes

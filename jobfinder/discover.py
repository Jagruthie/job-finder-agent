"""Find companies from job links: paste links (or a saved page of search results) and get back
the companies behind them, checked against their public job lists.

Location first: search the web yourself for jobs in your city (see README), then give the links here.
The agent never runs web searches on its own, because search engines do not allow automated searching.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from .filters import INDIA_PLACES, location_matches
from .models import Job
from .sources import SourceError

# boards.greenhouse.io/acme, job-boards.greenhouse.io/acme, boards-api.greenhouse.io/v1/boards/acme,
# boards.greenhouse.io/embed/job_board?for=acme, jobs.lever.co/acme, jobs.ashbyhq.com/acme
_LINKS = [
    ("greenhouse", re.compile(r"(?:job-)?boards\.greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"(?<![.\w-])(?:job-)?boards\.greenhouse\.io/(?!embed\b)([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"(?<![.\w-])jobs\.lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
]
_NOT_COMPANIES = {"leverdemo", "embed", "api", "v1", "jobs", "job", "careers"}


def extract_boards(text: str) -> List[Tuple[str, str]]:
    """Every (platform, board) found in links inside the text, in order, without repeats."""
    found: List[Tuple[str, str]] = []
    for platform, pattern in _LINKS:
        for match in pattern.finditer(text or ""):
            board = match.group(1).strip(".").lower()
            if board and board not in _NOT_COMPANIES and (platform, board) not in found:
                found.append((platform, board))
    order = {pair: (text.lower().find(pair[1]), i) for i, pair in enumerate(found)}
    return sorted(found, key=order.get)


def check_board(platform: str, board: str, preferred: List[str],
                fetch: Callable[[str, str, str], List[Job]], remote_country: str = "India") -> Dict:
    """Read one company's public job list and count jobs in your cities and in India (or remote)."""
    try:
        jobs = fetch(platform, board, board)
    except SourceError as error:
        return {"platform": platform, "board": board, "ok": False, "error": str(error)}
    return {"platform": platform, "board": board, "ok": True, "jobs": len(jobs),
            "in_your_cities": sum(location_matches(j.location, preferred, remote_country) for j in jobs),
            "in_india": sum(location_matches(j.location, INDIA_PLACES + ["remote"], "India") for j in jobs)}


def known_boards(path: str) -> set:
    file = Path(path)
    if not file.exists():
        return set()
    with open(file, newline="", encoding="utf-8-sig") as handle:
        lines = [line for line in handle if line.strip() and not line.lstrip().startswith("#")]
    return {(row.get("platform", "").strip().lower(), row.get("board", "").strip().lower())
            for row in csv.DictReader(lines)}


def append_companies(path: str, results: List[Dict], example: str = "companies.example.csv") -> None:
    file = Path(path)
    if not file.exists():
        source = Path(example)
        file.write_text(source.read_text(encoding="utf-8") if source.exists()
                        else "name,platform,board,email,x_handle\n", encoding="utf-8")
    text = file.read_text(encoding="utf-8")
    with open(file, "a", newline="", encoding="utf-8") as handle:
        if text and not text.endswith("\n"):
            handle.write("\n")
        writer = csv.writer(handle)
        for result in results:
            writer.writerow([result["board"], result["platform"], result["board"], "", ""])

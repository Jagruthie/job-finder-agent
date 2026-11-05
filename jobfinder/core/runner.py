"""The run: read the watch-list's job pages, keep what matches your profiles, and return only the jobs not shown before."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .. import storage
from ..alerts import read_alerts
from ..config import STARTER_COMPANIES, file_path
from ..demo import demo_data
from ..errors import ValidationError
from ..pipeline import clean_list, read_companies, run_pipeline, write_csv
from ..sources import SourceError, fetch_board, http_get_json
from ..sources.remote import SOURCES as REMOTE_SOURCES, fetch_remote
from ..textutil import norm
from ..vocab import Vocab
from . import watchlist

Progress = Callable[[dict], None]


@dataclass
class RunResult:
    date: str
    new_matches: List[dict] = field(default_factory=list)   # number, job_id, company, role, posted_date, apply_link...
    already_shown: int = 0                                   # matches left out because you saw them on an earlier run
    companies_read: int = 0
    failed: List[dict] = field(default_factory=list)         # company, platform, board, error
    skipped: List[dict] = field(default_factory=list)        # company, careers_url, reason
    warnings: List[str] = field(default_factory=list)        # parts of an optional source that could not be read
    notes: List[str] = field(default_factory=list)           # about saved alert emails, when those are on
    sources: List[str] = field(default_factory=list)         # "watch-list", plus any optional source turned on
    stats: Dict[str, int] = field(default_factory=dict)
    csv_path: str = ""
    saved: bool = True          # False when no company could be read, so nothing was remembered


@dataclass
class DemoResult:
    rows: List[dict]
    stats: Dict[str, int]
    failures: List[str]
    csv_path: str


class Fetched:
    """Job lists read ahead of screening, handed out in the order the pipeline asks for them."""

    def __init__(self):
        self.results = {}

    def read(self, platform: str, board: str, name: str):
        result = self.results[(platform, board, name)]
        if isinstance(result, SourceError):
            raise result
        return result


def prefetch(companies: List[dict], fetch, delay: float, sleep=time.sleep, progress: Optional[Progress] = None) -> Fetched:
    """Read every job list first: each site (Greenhouse, Lever, Ashby, Himalayas...) one request at a time with
    the polite delay between them, and the sites side by side, so many companies do not take many minutes.
    `progress`, if given, is called after each company with {company, platform, ok, jobs, error, done, total}."""
    by_site = {}
    for company in companies:
        by_site.setdefault(company["platform"], []).append(company)
    fetched = Fetched()
    lock = threading.Lock()
    done = [0]

    def read_site(items: List[dict]) -> None:
        for index, company in enumerate(items):
            if index:
                sleep(delay)
            key = (company["platform"], company["board"], company["name"])
            try:
                fetched.results[key] = fetch(*key)
            except SourceError as error:
                fetched.results[key] = error
            except Exception as error:   # a dropped connection or a malformed reply fails one company, not the run
                fetched.results[key] = SourceError(f"{type(error).__name__}: {error}")
            if progress:
                result = fetched.results[key]
                failed = isinstance(result, SourceError)
                with lock:
                    done[0] += 1
                    progress({"company": company["name"], "platform": company["platform"], "ok": not failed,
                              "jobs": 0 if failed else len(result), "error": str(result) if failed else "",
                              "done": done[0], "total": len(companies)})

    with ThreadPoolExecutor(max_workers=max(1, len(by_site))) as pool:
        list(pool.map(read_site, by_site.values()))
    return fetched


def _with_optional_sources(cfg: dict, companies: List[dict], profiles: List[dict], alert_jobs: list) -> dict:
    """Add the optional sources that are turned on. Returns which are in use and the role searches for Himalayas."""
    used = ["watch-list"]
    if cfg["use_starter_companies"] and STARTER_COMPANIES.exists():
        own = {(c["platform"], c["board"].lower()) for c in companies}
        companies += [c for c in read_companies(str(STARTER_COMPANIES))
                      if (c["platform"].lower(), c["board"].lower()) not in own]
        used.append("starter companies")
    if alert_jobs:
        companies.append({"name": "Job-alert emails", "platform": "alerts", "board": cfg["inbox_dir"]})
        used.append("job-alert emails")
    remote: List[str] = []
    if cfg["use_remote_boards"] and any(norm(place) == "remote" for place in cfg["preferred_locations"]):
        remote = [source for source in cfg["remote_sources"] if source in REMOTE_SOURCES]
    for source in remote:
        companies.append({"name": f"{source} (remote jobs)", "platform": source, "board": "remote"})
        used.append(source)
    # Himalayas is also searched for each of your roles, which reaches jobs too far down its feed to be read in full.
    queries = list(dict.fromkeys(t for profile in profiles for t in clean_list(profile.get("titles", []))))
    return {"used": used, "queries": queries if "himalayas" in remote else []}


def _match(number: int, row: dict) -> dict:
    return {"number": number, "job_id": row["Job id"], "track": row["Track"], "company": row["Company"],
            "role": row["Role"], "posted_date": row["Posted"], "apply_link": row["Apply link"],
            "location": row["Location"], "matched_by": row["Matched by"], "match": row["Match"], "details": row}


def _board(job_id: str):
    platform, board, _ = job_id.split(":", 2)
    return platform, board


def run(cfg: dict, profiles: List[dict], vocab: Optional[Vocab] = None, fetch=None, now: Optional[datetime] = None,
        today: Optional[date] = None, progress: Optional[Progress] = None, sleep=time.sleep) -> RunResult:
    """Read every active company, filter with your profiles, and return the matches you have not been shown."""
    vocab = vocab or Vocab.load()
    now = now or datetime.now(timezone.utc)
    today = today or now.astimezone().date()
    companies, skipped = watchlist.for_run(watchlist.read(cfg))
    alert_jobs, notes = read_alerts(str(file_path(cfg, "inbox_dir"))) if cfg["use_alert_emails"] else ([], [])
    if alert_jobs:
        notes.append(f"Read {len(alert_jobs)} jobs from job-alert emails in {cfg['inbox_dir']}/")
    sources = _with_optional_sources(cfg, companies, profiles, alert_jobs)
    if not companies:
        raise ValidationError("Your watch-list has no active companies Freshlist can read. "
                              "Add one with: python -m jobfinder companies add <careers link>")
    warnings: List[str] = []

    def read_live(platform: str, board: str, name: str):
        if platform == "alerts":
            return alert_jobs
        if platform in REMOTE_SOURCES:
            return fetch_remote(platform, cfg, now, http_get_json, time.sleep, str(file_path(cfg, "cache_dir")),
                                queries=sources["queries"], warn=warnings.append)
        return fetch_board(platform, board, name)

    seen_path = file_path(cfg, "seen_file")
    memory = storage.read_json(seen_path, {"shown": {}, "first_seen": {}, "dropped": {}})
    shown = memory.setdefault("shown", {})
    fetched = prefetch(companies, fetch or read_live, cfg["request_delay_seconds"], sleep, progress)
    rows, stats, _ = run_pipeline(companies, profiles, cfg, vocab, memory, fetched.read, now=now)
    failed = [{"company": name, "platform": platform, "board": board, "error": str(result)}
              for (platform, board, name), result in fetched.results.items() if isinstance(result, SourceError)]
    result = RunResult(date=today.isoformat(), companies_read=stats["boards_ok"], failed=failed, skipped=skipped,
                       warnings=warnings, notes=notes, sources=sources["used"], stats=dict(stats),
                       already_shown=sum(row["Job id"] in shown for row in rows))
    if not stats["boards_ok"]:    # a dropped connection is not a day with no jobs: remember nothing
        result.saved = False
        return result

    # Every same-day run adds to the day's numbered list, so a number you saw earlier today still means the same job.
    last_path = file_path(cfg, "last_run_file")
    last = storage.read_json(last_path, {})
    earlier = last.get("matches", []) if last.get("date") == today.isoformat() else []
    next_number = max((m["number"] for m in earlier), default=0) + 1
    new_rows = [row for row in rows if row["Job id"] not in shown]
    result.new_matches = [_match(next_number + index, row) for index, row in enumerate(new_rows)]
    all_today = earlier + result.new_matches
    csv_path = Path(file_path(cfg, "output_dir")) / f"jobs-{today.isoformat()}.csv"
    write_csv([m["details"] for m in all_today], str(csv_path))
    result.csv_path = str(csv_path)
    storage.write_json(last_path, {"date": today.isoformat(), "matches": all_today})
    watchlist.record_matches(cfg, {_board(row["Job id"]) for row in rows}, today)

    log_path = file_path(cfg, "run_log_file")
    log = storage.read_json(log_path, {"first_live_run": "", "runs": [], "matches": []})
    log["first_live_run"] = log.get("first_live_run") or today.isoformat()
    log["runs"].append({"date": today.isoformat(), "companies_read": stats["boards_ok"], "failed": failed,
                        "new_matches": len(new_rows)})
    log["matches"].extend({"date": today.isoformat(), "job_id": m["job_id"], "company": m["company"],
                           "role": m["role"]} for m in result.new_matches)
    storage.write_json(log_path, log)

    # Last, so a crash before this point shows a job again rather than losing it.
    shown.update({row["Job id"]: today.isoformat() for row in new_rows})
    storage.write_json(seen_path, memory)
    return result


def last_run(cfg: dict) -> dict:
    """The numbered matches saved by the last run: {"date", "matches"}, empty before the first run."""
    return storage.read_json(file_path(cfg, "last_run_file"), {}) or {}


def demo(cfg: dict, profiles: List[dict], vocab: Optional[Vocab] = None, now: Optional[datetime] = None) -> DemoResult:
    """The whole filter on built-in FAKE jobs. Nothing is fetched and nothing is remembered."""
    vocab = vocab or Vocab.load()
    now = now or datetime.now(timezone.utc)
    companies, jobs = demo_data(now)
    rows, stats, failures = run_pipeline(companies, profiles, cfg, vocab, {"first_seen": {}, "dropped": {}},
                                         lambda platform, board, name: jobs[board], now=now)
    csv_path = Path(file_path(cfg, "output_dir")) / f"demo-{now.astimezone().date().isoformat()}.csv"
    write_csv(rows, str(csv_path))
    return DemoResult(rows=rows, stats=dict(stats), failures=failures, csv_path=str(csv_path))

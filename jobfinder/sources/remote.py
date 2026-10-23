"""Remote-job sources searched on every run: Himalayas and Remote OK.

Only jobs open to people in your country (settings: remote_country, default India) are kept.
Their terms ask for credit and their own links, so each job keeps the source's link unchanged and the
CSV's Platform column names the source. Both refresh about once a day, so one download per day is
cached in cache/ and reused by later runs that day.
  Himalayas: https://himalayas.app/api (free public API; keep applicationLink unchanged)
  Remote OK: https://remoteok.com/api (free; link back to Remote OK and mention it as the source)
Remotive was left out: its free feed returns only 10 jobs and its full API is paid.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from ..filters import INDIA_PLACES
from ..models import Job
from ..textutil import norm, parse_dt, strip_html

SOURCES = ("himalayas", "remoteok")
MAX_HIMALAYAS_PAGES = 60          # 20 jobs a page, newest first; stops earlier once jobs are too old
MAX_SEARCH_PAGES = 10             # per role searched, same rule


def _cached(cache_dir: Optional[str], name: str, now: datetime, download: Callable[[], Any]) -> Any:
    if not cache_dir:
        return download()
    file = Path(cache_dir) / f"{name}-{now.date().isoformat()}.json"
    if file.exists():
        return json.loads(file.read_text(encoding="utf-8"))
    data = download()
    file.parent.mkdir(parents=True, exist_ok=True)
    for old in file.parent.glob(f"{name}-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"):
        old.unlink()                              # earlier days of this same feed only, not other searches
    file.write_text(json.dumps(data), encoding="utf-8")
    return data


def _pay(low, high, currency, period) -> str:
    if not low and not high:
        return ""
    amount = f"{low}-{high}" if low and high else f"{low or high}"
    return f"{currency or ''} {amount} per {period or 'year'}".strip()


def fetch_himalayas(get_json: Callable[[str], Any], country: str, max_age_days: int, now: datetime,
                    sleep: Callable[[float], None], cache_dir: Optional[str] = None,
                    queries: List[str] = (), full: bool = True) -> List[Job]:
    """Remote jobs open to your country: the whole recent feed (full), and a search for each of your roles
    (queries), which also reaches matching jobs too far down the feed to be read in full."""
    where = f"country={quote(country)}&" if country else "worldwide=true&"
    place = norm(country).replace(" ", "-") or "worldwide"

    def download(query: str = "", pages: int = MAX_HIMALAYAS_PAGES) -> Callable[[], list]:
        def run() -> list:
            items: list = []
            search = f"q={quote(query)}&" if query else ""
            for page in range(1, pages + 1):
                if page > 1:
                    sleep(1.0)
                batch = (get_json(f"https://himalayas.app/jobs/api/search?{search}{where}sort=recent&page={page}")
                         or {}).get("jobs", [])
                items.extend(batch)
                dates = [parse_dt(item.get("pubDate")) for item in batch]
                if not batch or all(d and now - d > timedelta(days=max_age_days) for d in dates):
                    break
            return items
        return run

    feeds = [_cached(cache_dir, f"himalayas-{place}", now, download())] if full else []
    for query in queries:
        slug = norm(query).replace(" ", "-")
        feeds.append(_cached(cache_dir, f"himalayas-{place}-search-{slug}", now, download(query, MAX_SEARCH_PAGES)))

    jobs: Dict[str, Job] = {}
    for item in (item for feed in feeds for item in feed or []):
        expiry = parse_dt(item.get("expiryDate"))
        if expiry and expiry < now:
            continue                                  # their terms: remove jobs once they expire
        restrictions = item.get("locationRestrictions") or []
        open_to = f"open to {country}" if country and restrictions else "worldwide"
        guid = item.get("guid") or item.get("applicationLink") or ""
        jobs[guid] = Job(platform="himalayas", board="remote", company=item.get("companyName", "") or "",
                         job_id=guid, title=item.get("title", "") or "", location=f"Remote ({open_to})",
                         url=item.get("applicationLink") or guid,
                         description=strip_html(item.get("description") or item.get("excerpt") or ""),
                         posted_at=parse_dt(item.get("pubDate")),
                         pay_text=_pay(item.get("minSalary"), item.get("maxSalary"), item.get("currency"),
                                       item.get("salaryPeriod")))
    return list(jobs.values())


def _remoteok_open_to(location: str, country: str) -> Optional[str]:
    """Remote OK's location is free text and often empty, so keep only clear cases."""
    text = norm(location)
    if re.search(r"\b(worldwide|anywhere|global)\b", text):
        return "worldwide"
    places = INDIA_PLACES + ["asia", "apac"] if norm(country) == "india" else [norm(country), "worldwide"]
    if country and any(re.search(rf"\b{re.escape(place)}\b", text) for place in places if place):
        return f"open to {country}"
    return None


def fetch_remoteok(get_json: Callable[[str], Any], country: str, now: datetime,
                   cache_dir: Optional[str] = None) -> List[Job]:
    items = _cached(cache_dir, "remoteok", now, lambda: get_json("https://remoteok.com/api"))  # one global feed
    jobs: List[Job] = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("position"):
            continue                                  # the first item is Remote OK's legal notice
        open_to = _remoteok_open_to(item.get("location") or "", country)
        if not open_to:
            continue
        jobs.append(Job(platform="remoteok", board="remote", company=item.get("company", "") or "",
                        job_id=str(item.get("id", "")), title=item.get("position", "") or "",
                        location=f"Remote ({open_to})", url=item.get("url") or item.get("apply_url") or "",
                        description=strip_html(item.get("description") or ""),
                        posted_at=parse_dt(item.get("date")),
                        pay_text=_pay(item.get("salary_min"), item.get("salary_max"), "USD", "year")))
    return jobs


def fetch_remote(source: str, settings: dict, now: datetime, get_json: Callable[[str], Any],
                 sleep: Callable[[float], None], cache_dir: Optional[str] = "cache",
                 queries: List[str] = (), full: bool = True) -> List[Job]:
    country = settings.get("remote_country", "India")
    if source == "himalayas":
        return fetch_himalayas(get_json, country, settings["freshness_days"], now, sleep, cache_dir, queries, full)
    if source == "remoteok":
        return fetch_remoteok(get_json, country, now, cache_dir)
    raise ValueError(f"unknown remote source '{source}'")

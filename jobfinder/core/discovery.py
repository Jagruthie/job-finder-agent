"""Turning job links into watch-list companies, and the optional Y Combinator discovery (off by default)."""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ..discover import YC_HIRING, check_board, extract_boards, find_board, yc_candidates
from ..errors import ConfigError
from ..filters import INDIA_PLACES
from ..sources import fetch_board, http_get_json
from ..textutil import norm
from . import watchlist

Progress = Callable[[dict], None]


def boards_in(items: List[str]) -> List[Tuple[str, str]]:
    """Every Greenhouse, Lever or Ashby board named in links, or in files such as a saved web page."""
    text = []
    for item in items:
        file = Path(item).expanduser()
        text.append(file.read_text(encoding="utf-8", errors="ignore") if file.is_file() else item)
    return extract_boards("\n".join(text))


def known_boards(cfg: dict) -> set:
    return {(row["platform"], watchlist.board_of(row)) for row in watchlist.read(cfg)}


def check_boards(cfg: dict, boards: List[Tuple[str, str]], sleep=time.sleep,
                 progress: Optional[Progress] = None) -> List[dict]:
    """Read each board once and count its jobs in your cities and your country."""
    results = []
    for index, (platform, board) in enumerate(boards):
        if index:
            sleep(cfg["request_delay_seconds"])
        results.append(check_board(platform, board, cfg["preferred_locations"], fetch_board, cfg["remote_country"]))
        if progress:
            progress(results[-1])
    return results


def check_file(cfg: dict, prepared: List[dict], fetch=fetch_board, sleep=time.sleep,
               progress: Optional[Progress] = None) -> List[dict]:
    """Check companies from watchlist.prepare. Each comes back with jobs, in_your_cities, a state and `keep`, which
    is True only for a company with jobs in your cities whose page came from the file. A page found from only a name
    could belong to another company with that name, so you decide about those yourself."""
    known = watchlist.read(cfg)
    results: List[dict] = []
    contacted = False

    def read(platform: str, board: str) -> dict:
        return check_board(platform, board, cfg["preferred_locations"], fetch, cfg["remote_country"])

    for index, item in enumerate(prepared):
        result = {**item, "jobs": 0, "in_your_cities": 0, "guessed": False, "keep": False}
        if item["state"] in ("check", "search"):
            if contacted:
                sleep(cfg["request_delay_seconds"])
            contacted = True
            try:
                if item["state"] == "check":
                    found = read(item["platform"], watchlist.board_of(item))
                else:
                    found = find_board({"name": item["company"], "website": item["website"],
                                        "slug": norm(item["company"]).replace(" ", "-")}, read, sleep)
            except Exception as error:   # a dropped connection or a malformed reply fails one company, not the check
                found = {"ok": False, "error": f"{type(error).__name__}: {error}"}
            _settle(result, found, cfg, known, results)
        results.append(result)
        if progress:
            progress({"company": item.get("company") or item.get("careers_url", ""), "done": index + 1,
                      "total": len(prepared)})
    return results


def _settle(result: dict, found: dict, cfg: dict, known: List[dict], earlier: List[dict]) -> None:
    searched = result["state"] == "search"
    if not found["ok"]:
        result.update(state="not_found" if searched else "failed",
                      note="No Greenhouse, Lever or Ashby page found for this name." if searched else found["error"])
        return
    if searched:
        row = watchlist.new_row(watchlist.careers_url_for(found["platform"], found["board"]), result["company"],
                                result["tier"], result["status"], result["notes"], date.today())
        result.update(row)
        result["guessed"] = True
        chosen = [item for item in earlier if item.get("careers_url")]
        duplicate = watchlist.duplicate_of(row, known + chosen)
        if duplicate:
            result.update(state="listed", note=f"Already on your list as '{duplicate['company']}'.")
            return
    result.update(jobs=found["jobs"], in_your_cities=found["in_your_cities"])
    result["state"] = "good" if found["in_your_cities"] else ("elsewhere" if found["jobs"] else "no_jobs")
    result["note"] = "Found from the name only. Open the link to check it is the right company." if searched else ""
    result["keep"] = result["state"] == "good" and not searched


def add_results(cfg: dict, results: List[dict]) -> dict:
    """Add checked boards to the watch-list."""
    return watchlist.add_many(cfg, [(r.get("name") or "", watchlist.careers_url_for(r["platform"], r["board"]))
                                    for r in results])


def find_yc(cfg: dict, remote: bool = False, limit: int = 0, get_json=http_get_json, sleep=time.sleep,
            progress: Optional[Progress] = None) -> dict:
    """Look for job boards of Y Combinator's hiring companies. Off unless use_yc_discovery is on. Returns
    {"hiring", "candidates", "results"}, with companies already on the watch-list left out of the results."""
    if not cfg["use_yc_discovery"]:
        raise ConfigError("YC discovery is off by default. Turn it on with --enable, or set "
                          '"use_yc_discovery": true in settings.json.')
    places = cfg["preferred_locations"] + (INDIA_PLACES if norm(cfg["remote_country"]) == "india" else [])
    places = [p for p in places if norm(p) != "remote"] + (["remote"] if remote else [])
    companies = get_json(YC_HIRING, timeout=60)
    candidates = yc_candidates(companies, places)
    known = known_boards(cfg)
    results = []
    for index, company in enumerate(candidates[:limit] if limit else candidates):
        if index:
            sleep(0.4)
        result = find_board(company, lambda platform, board: check_board(
            platform, board, cfg["preferred_locations"], fetch_board, cfg["remote_country"]), sleep)
        already = result["ok"] and (result["platform"], result["board"]) in known
        if not already:
            results.append(result)
        if progress:
            progress({**result, "already_listed": already})
    return {"hiring": len(companies), "candidates": len(candidates), "results": results}

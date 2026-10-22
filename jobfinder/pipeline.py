from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from .filters import (NOT_STATED, TOO_SENIOR, WITHIN, experience_bucket, has_excluded_word,
                      location_matches, parse_annual_pay, pay_from_text, shift_mentioned, title_matches)
from .models import Job
from .sources import SourceError
from .textutil import norm, parse_dt
from .vocab import Vocab

DEFAULT_SETTINGS = {
    "freshness_days": 7,
    "max_experience_years": 1,
    "preferred_locations": ["Hyderabad", "Remote"],
    "location_mode": "prefer",          # "prefer" ranks higher, "require" drops other places
    "pay_currency": "INR",              # INR or USD; pay in another currency is never compared
    "min_annual_pay": None,             # full yearly amount, e.g. 300000 for 3 LPA. None means no minimum
    "max_annual_pay": None,             # optional upper end of your range. None means no maximum
    "pay_mode": "prefer",               # "prefer" ranks outside-range jobs lower, "require" drops jobs whose stated pay is outside your range
    "exclude_title_words": ["senior", "sr", "lead", "head", "director", "vp", "principal", "staff"],
    "request_delay_seconds": 1.5,
}

COLUMNS = [
    "Rank", "Track", "Company", "Role", "Apply link", "Posted", "Date basis",
    "Experience", "Experience bucket", "Fit %", "Matched skills", "Missing skills",
    "Location", "Location match", "Shift 10-6 mentioned", "Pay", "Pay check",
    "Company email", "Founder X handle", "Platform",
]

FetchFn = Callable[[str, str, str], List[Job]]


def clean_text(value: str) -> str:
    """Remove stray keystrokes (control characters such as Escape) and square brackets."""
    return "".join(ch for ch in str(value) if ch.isprintable() and ch not in "[]").strip()


def clean_locations(values) -> List[str]:
    """Clean a saved city list: strip stray characters, split on commas, drop empties."""
    cleaned: List[str] = []
    for value in values or []:
        cleaned.extend(part.strip() for part in clean_text(value).split(",") if part.strip())
    return cleaned


# Words people type at a prompt when they mean "leave it as it is". Never a real role or skill.
NON_ANSWERS = {"keep", "enter", "yes", "y", "no", "n", "none", "ok", "okay", "same", "skip"}


def is_non_answer(value: str) -> bool:
    return norm(value) in NON_ANSWERS or not norm(value)


def clean_list(values) -> List[str]:
    """Clean a saved role or skill list: strip stray keystrokes, drop empties and non-answers."""
    return [text for text in (clean_text(v) for v in values or []) if not is_non_answer(text)]


def profile_files(directory: str) -> List[Path]:
    """The profiles to use in a folder: every .json except example.json and *.backup.json copies."""
    folder = Path(directory)
    if not folder.exists():
        return []
    return sorted(f for f in folder.glob("*.json") if f.stem != "example" and not is_backup(f))


def is_backup(path) -> bool:
    return Path(path).name.lower().endswith(".backup.json")


class ProfileError(Exception):
    """A profile cannot be used as it is."""


def load_state(path: str) -> dict:
    file = Path(path)
    if file.exists():
        try:
            return json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"first_seen": {}, "dropped": {}}


def save_state(path: str, state: dict) -> None:
    Path(path).write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def prepare_profile(profile: dict, vocab: Vocab) -> dict:
    phrases: List[str] = []
    for title in clean_list(profile.get("titles", [])):
        phrases.extend(vocab.title_phrases(title))
    return {**profile, "_phrases": sorted(set(phrases)), "_skills": set(clean_list(profile.get("skills", [])))}


def run_pipeline(companies: List[dict], profiles: List[dict], settings: dict, vocab: Vocab,
                 state: dict, fetch: FetchFn, now: datetime = None,
                 sleep: Callable[[float], None] = lambda s: None) -> Tuple[List[dict], Counter, List[str]]:
    now = now or datetime.now(timezone.utc)
    cfg = {**DEFAULT_SETTINGS, **settings}
    prepared = [prepare_profile(p, vocab) for p in profiles]
    for profile in prepared:
        if not profile["_phrases"]:
            raise ProfileError(
                f"Stopped: profile '{profile.get('name', '?')}' has no usable roles, so no jobs were screened. "
                "(Empty lists and words such as 'keep' do not count as roles.) "
                "Run 'python -m jobfinder setup' to set its roles, or add them to the 'titles' list in its file.")
    first_seen: Dict[str, str] = state.setdefault("first_seen", {})
    dropped: Dict[str, str] = state.setdefault("dropped", {})
    stats: Counter = Counter()
    failures: List[str] = []
    rows: List[dict] = []
    max_age = timedelta(days=cfg["freshness_days"])

    for index, company in enumerate(companies):
        if index:
            sleep(cfg["request_delay_seconds"])
        try:
            jobs = fetch(company["platform"], company["board"], company["name"])
        except SourceError as error:
            stats["boards_failed"] += 1
            failures.append(f"{company['name']} ({company['platform']}/{company['board']}): {error}")
            continue
        stats["boards_ok"] += 1

        for job in jobs:
            stats["jobs_seen"] += 1
            if job.key in dropped:
                stats["already_dropped"] += 1
                continue

            posted, basis = job.posted_at, "platform date"
            if posted is None:
                basis = "first seen (real age unknown)"
                posted = parse_dt(first_seen.get(job.key)) or now
                first_seen.setdefault(job.key, now.isoformat())
            if now - posted > max_age:
                dropped[job.key] = now.date().isoformat()
                first_seen.pop(job.key, None)
                stats["stale_dropped"] += 1
                continue

            candidates = [p for p in prepared if title_matches(job.title, p["_phrases"])]
            if not candidates:
                stats["title_mismatch"] += 1
                continue
            if has_excluded_word(job.title, cfg["exclude_title_words"]):
                stats["senior_title"] += 1
                continue

            text = f"{job.title}. {job.description}"
            bucket, exp_text = experience_bucket(text, cfg["max_experience_years"])
            if bucket == TOO_SENIOR:
                stats["too_senior"] += 1
                continue

            loc_ok = location_matches(job.location, cfg["preferred_locations"])
            if cfg["location_mode"] == "require" and not loc_ok:
                stats["location_dropped"] += 1
                continue

            pay_text = job.pay_text or pay_from_text(job.description) or ""
            annual = parse_annual_pay(pay_text, cfg["pay_currency"])
            min_pay, max_pay = cfg.get("min_annual_pay"), cfg.get("max_annual_pay")
            if min_pay is None and max_pay is None:
                pay_check, pay_tier = "No pay range set", 1
            elif annual is None:
                pay_check, pay_tier = "Not stated or not comparable", 1
            elif min_pay is not None and annual[1] < min_pay:
                pay_check, pay_tier = "Below your range", 2
            elif max_pay is not None and annual[0] > max_pay:
                pay_check, pay_tier = "Above your range", 2
            else:
                pay_check, pay_tier = "Within your range", 0
            if cfg["pay_mode"] == "require" and pay_tier == 2:
                stats["pay_dropped"] += 1
                continue

            found = vocab.extract_skills(text)
            best = None
            for profile in candidates:
                matched = [s for s in found if s in profile["_skills"]]
                missing = [s for s in found if s not in profile["_skills"]]
                pct = round(100 * len(matched) / len(found)) if found else None
                if best is None or (pct if pct is not None else -1) > (best[1] if best[1] is not None else -1):
                    best = (profile["name"], pct, matched, missing)
            track, pct, matched, missing = best

            rows.append({
                "Track": track,
                "Company": job.company,
                "Role": job.title,
                "Apply link": job.url,
                "Posted": posted.date().isoformat(),
                "Date basis": basis,
                "Experience": exp_text or "Not stated",
                "Experience bucket": "Within your range" if bucket == WITHIN else "Not stated (review)",
                "Fit %": "" if pct is None else pct,
                "Matched skills": ", ".join(matched),
                "Missing skills": ", ".join(missing),
                "Location": job.location,
                "Location match": "yes" if loc_ok else "no",
                "Shift 10-6 mentioned": "yes" if shift_mentioned(text) else "",
                "Pay": pay_text or "Not stated",
                "Pay check": pay_check,
                "Company email": company.get("email", ""),
                "Founder X handle": company.get("x_handle", ""),
                "Platform": job.platform,
                "_sort": (0 if bucket == WITHIN else 1,
                          pay_tier,
                          0 if loc_ok else 1,
                          0 if shift_mentioned(text) else 1,
                          -(pct if pct is not None else -1),
                          -posted.timestamp()),
            })
            stats["kept"] += 1
            if bucket == NOT_STATED:
                stats["kept_experience_not_stated"] += 1

    # Grouped by track (in the order the profiles were given), best first inside each track.
    order = {profile["name"]: index for index, profile in enumerate(prepared)}
    rows.sort(key=lambda row: (order[row["Track"]], row["_sort"]))
    ranks: Counter = Counter()
    for row in rows:
        ranks[row["Track"]] += 1
        row["Rank"] = ranks[row["Track"]]
        del row["_sort"]
    return rows, stats, failures


def write_csv(rows: List[dict], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def read_companies(path: str) -> List[dict]:
    """companies.csv columns: name, platform, board, email, x_handle (last two optional).
    Lines starting with # are ignored."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        lines = [line for line in handle if line.strip() and not line.lstrip().startswith("#")]
    return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(lines)]

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from .filters import (NOT_STATED, TOO_SENIOR, WITHIN, entry_level_title, experience_bucket, has_excluded_word,
                      higher_level_title, location_matches, parse_annual_pay, pay_from_text, shift_mentioned,
                      title_matches)
from .models import Job
from .sources import SourceError
from .textutil import has_phrase, norm, parse_dt
from .vocab import Vocab

DEFAULT_SETTINGS = {
    "freshness_days": 7,
    "max_experience_years": 1,
    "preferred_locations": ["Hyderabad", "Remote"],
    "location_mode": "prefer",          # "prefer" ranks higher, "require" drops other places
    "remote_country": "India",          # "Remote" in your cities means remote in this country (empty: anywhere)
    "remote_sources": ["himalayas", "remoteok"],   # searched when "Remote" is one of your cities; [] turns them off
    "starter_companies": True,          # also read data/companies-starter.csv (companies checked to hire in India)
    # Deal-breakers (per profile, such as Coding): a post naming this many different terms of it is removed,
    # and one naming at least `deal_breaker_flag` of them is kept but flagged and ranked lower.
    "deal_breaker_remove": 4,
    "deal_breaker_flag": 2,
    "pay_currency": "INR",              # INR or USD; pay in another currency is never compared
    "min_annual_pay": None,             # full yearly amount, e.g. 300000 for 3 LPA. None means no minimum
    "max_annual_pay": None,             # optional upper end of your range. None means no maximum
    "pay_mode": "prefer",               # "prefer" ranks outside-range jobs lower, "require" drops jobs whose stated pay is outside your range
    "exclude_title_words": ["senior", "sr", "lead", "head", "director", "vp", "principal", "staff"],
    # Keep a job whose title is not on your list when it asks for enough of your skills.
    "match_by": "titles_or_skills",     # or "titles" to keep only jobs whose title is on your list
    "skill_match_min": 3,               # at least this many of your skills in the job
    "skill_match_share": 50,            # and at least this % of the skills the job mentions
    "request_delay_seconds": 1.5,
}

COLUMNS = [
    "Rank", "Track", "Company", "Role", "Matched by", "Apply link", "Posted", "Date basis",
    "Experience", "Experience bucket", "Priority skills", "Deal-breaker check", "Fit %", "Matched skills",
    "Missing skills",
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


BUCKET_LABELS = {0: "Within your range", 1: "Entry-level title (years not stated)", 2: "Not stated (review)"}


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
    skills = clean_list(profile.get("skills", []))
    # Skills you confirmed that are not in data/skills.json are matched by their own words in the job text.
    own = [(skill, norm(skill)) for skill in skills if skill not in vocab.skills and len(norm(skill)) >= 3]
    avoid = [group for group in clean_list(profile.get("deal_breakers", [])) if group in vocab.deal_breakers]
    avoided_skills = {s for group in avoid for s in vocab.deal_breakers[group].get("skills", [])}
    priority = [s for s in clean_list(profile.get("priority_skills", [])) if s not in avoided_skills]
    # The specific skills this track is about: your priority skills, or else the skills your chosen roles need
    # (from data/skills.json), without broad ones such as Research and without deal-breakers. A job kept for its
    # skills alone must need 2 of these, so a PM track does not pick up developer jobs because you know Python.
    core = set(priority) or {s for title in clean_list(profile.get("titles", []))
                             for s in vocab.titles.get(title, {}).get("skills", [])} & set(skills)
    return {**profile, "_phrases": sorted(set(phrases)), "_skills": set(skills), "_own_skills": own,
            "_core": core - vocab.generic - avoided_skills, "_priority": priority, "_avoid": avoid}


def common_skills(found_per_job: List[List[str]], share: float = 0.2, minimum_jobs: int = 20) -> set:
    """Skills mentioned in more than `share` of the jobs, when there are enough jobs to tell."""
    if len(found_per_job) < minimum_jobs:
        return set()
    counts = Counter(skill for found in found_per_job for skill in set(found))
    return {skill for skill, count in counts.items() if count > share * len(found_per_job)}


def skill_fit(profile: dict, found: List[str], text_norm: str, ignore: frozenset = frozenset()):
    """(skills of yours the job mentions, skills it mentions that you lack, % of its skills you have).
    Skills in `ignore` are left out of all three."""
    found = found + [skill for skill, key in profile["_own_skills"] if has_phrase(text_norm, key)]
    found = [skill for skill in found if skill not in ignore]
    matched = [s for s in found if s in profile["_skills"]]
    missing = [s for s in found if s not in profile["_skills"]]
    return matched, missing, (round(100 * len(matched) / len(found)) if found else None)


def run_pipeline(companies: List[dict], profiles: List[dict], settings: dict, vocab: Vocab,
                 state: dict, fetch: FetchFn, now: datetime = None,
                 sleep: Callable[[float], None] = lambda s: None) -> Tuple[List[dict], Counter, List[str]]:
    now = now or datetime.now(timezone.utc)
    cfg = {**DEFAULT_SETTINGS, **settings}
    prepared = [prepare_profile(p, vocab) for p in profiles]
    by_skills = cfg["match_by"] != "titles"
    for profile in prepared:
        if not profile["_phrases"] and not (by_skills and len(profile["_skills"]) >= cfg["skill_match_min"]):
            raise ProfileError(
                f"Stopped: profile '{profile.get('name', '?')}' has no usable roles"
                + (f" and fewer than {cfg['skill_match_min']} skills" if by_skills else "")
                + ", so no jobs were screened. (Empty lists and words such as 'keep' do not count.) "
                "Run 'python -m jobfinder setup' to set its roles and skills, or edit its file.")
    first_seen: Dict[str, str] = state.setdefault("first_seen", {})
    dropped: Dict[str, str] = state.setdefault("dropped", {})
    stats: Counter = Counter()
    failures: List[str] = []
    rows: List[dict] = []
    max_age = timedelta(days=cfg["freshness_days"])

    # Pass 1: read every company and keep the fresh jobs.
    fresh = []
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

            posted, basis = job.posted_at, job.date_basis or "platform date"
            if posted is None:
                basis = "first seen (real age unknown)"
                posted = parse_dt(first_seen.get(job.key)) or now
                first_seen.setdefault(job.key, now.isoformat())
            if now - posted > max_age:
                dropped[job.key] = now.date().isoformat()
                first_seen.pop(job.key, None)
                stats["stale_dropped"] += 1
                continue
            text = f"{job.title}. {job.description}"
            fresh.append((company, job, posted, basis, text, vocab.extract_skills(text)))

    # Skills found in a large share of this run's jobs are boilerplate (hiring notices, "communication"),
    # so they do not count when keeping a job for its skills alone.
    common = common_skills([found for *_, found in fresh])

    # Pass 2: screen each fresh job.
    for company, job, posted, basis, text, found in fresh:
        text_norm = norm(text)
        fits = {p["name"]: skill_fit(p, found, text_norm) for p in prepared}
        candidates = [p for p in prepared if title_matches(job.title, p["_phrases"])]
        matched_by = "role title"
        if not candidates and by_skills:
            # Skills in the company's own name (Git at GitLab) say nothing about the job either.
            ignore = common | vocab.skills_in_name(job.company) | vocab.generic
            specific = {}
            for profile in prepared:
                matched, _, pct = skill_fit(profile, found, text_norm, ignore)
                on_track = not profile["_core"] or len(profile["_core"] & set(matched)) >= 2
                if len(matched) >= cfg["skill_match_min"] and (pct or 0) >= cfg["skill_match_share"] and on_track:
                    candidates.append(profile)
                    specific[profile["name"]] = matched
            matched_by = "skills"
        if not candidates:
            stats["title_mismatch"] += 1
            continue
        # Deal-breakers: a track that refuses some work (such as Coding) drops jobs that depend on it.
        breaker_terms: Dict[str, List[str]] = {}
        for profile in list(candidates):
            for group in profile["_avoid"]:
                terms = breaker_terms.setdefault(group, vocab.deal_breaker_terms(group, text))
                if vocab.deal_breaker_title(group, job.title) or len(terms) >= cfg["deal_breaker_remove"]:
                    candidates.remove(profile)
                    break
        if not candidates:
            stats["deal_breaker"] += 1
            continue
        role_phrases = [phrase for profile in candidates for phrase in profile["_phrases"]]
        if has_excluded_word(job.title, cfg["exclude_title_words"], role_phrases) or higher_level_title(job.title):
            stats["senior_title"] += 1
            continue

        bucket, exp_text = experience_bucket(text, cfg["max_experience_years"])
        if bucket == TOO_SENIOR:
            stats["too_senior"] += 1
            continue
        # 0: years stated and within range, 1: years not stated but the title says entry level, 2: not stated
        level = 0 if bucket == WITHIN else (1 if entry_level_title(job.title) else 2)

        loc_ok = location_matches(job.location, cfg["preferred_locations"], cfg["remote_country"])
        # A job kept only for its skills must be in your cities: that wider net needs a tighter place.
        if (cfg["location_mode"] == "require" or matched_by == "skills") and not loc_ok:
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

        # The track whose own direction this job fits best wins (its priority skills, or its roles' skills),
        # then the higher Fit %.
        def track_score(profile):
            matched, _, pct = fits[profile["name"]]
            return (len(set(matched) & profile["_core"]), pct if pct is not None else -1)
        chosen = max(candidates, key=track_score)
        track = chosen["name"]
        matched, missing, pct = fits[track]
        priority_hit = [s for s in matched if s in chosen["_priority"]]
        flags = [f"mentions {group.lower()}: {', '.join(breaker_terms[group])}" for group in chosen["_avoid"]
                 if len(breaker_terms.get(group, [])) >= cfg["deal_breaker_flag"]]
        if matched_by == "skills":
            matched_by = f"skills: {', '.join(specific[track])}"
            stats["kept_by_skills"] += 1

        rows.append({
            "Track": track,
            "Company": job.company,
            "Role": job.title,
            "Matched by": matched_by,
            "Apply link": job.url,
            "Posted": posted.date().isoformat(),
            "Date basis": basis,
            "Experience": exp_text or "Not stated",
            "Experience bucket": BUCKET_LABELS[level],
            "Priority skills": ", ".join(priority_hit),
            "Deal-breaker check": "; ".join(flags),
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
            "_sort": (level,
                      0 if matched_by == "role title" else 1,
                      1 if flags else 0,
                      -len(priority_hit),
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

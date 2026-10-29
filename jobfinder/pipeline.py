from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from .filters import (INDIA_PLACES, NOT_STATED, TOO_SENIOR, WITHIN, entry_level_title, experience_bucket,
                      has_excluded_word, higher_level_title, in_country, location_matches, parse_annual_pay,
                      pay_from_text, remote_elsewhere, remote_in, SENIOR_ROLE_PHRASES, shift_mentioned)
from .match import Corpus, JobTerms, Query, score
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
    # A job whose title is not one of your roles is kept when it scores at least this % of the day's best match
    # for the track and names at least this many of your skills.
    "match_share": 45,
    "match_min_skills": 3,
    "request_delay_seconds": 1.5,
}

COLUMNS = [
    "Rank", "Track", "Company", "Role", "Matched by", "Apply link", "Posted", "Date basis",
    "Experience", "Experience bucket", "Priority skills", "Deal-breaker check", "Match", "Matched skills",
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
    """A profile ready to screen with: the roles and skills you saved (from your resume, as you confirmed them)."""
    titles = clean_list(profile.get("titles", []))
    skills = clean_list(profile.get("skills", []))
    avoid = [group for group in clean_list(profile.get("deal_breakers", [])) if group in vocab.deal_breakers]
    avoided_skills = {s for group in avoid for s in vocab.deal_breakers[group].get("skills", [])}
    priority = [s for s in clean_list(profile.get("priority_skills", [])) if s not in avoided_skills]
    skills += [s for s in priority if s not in skills]     # a priority skill is one of your skills, listed there or not
    phrases = sorted({phrase for title in titles for phrase in vocab.synonyms(title)})
    return {**profile, "_titles": titles, "_skill_list": skills, "_skills": set(skills), "_phrases": phrases,
            "_priority": priority, "_avoid": avoid, "_query": Query(titles, skills, priority, vocab)}


def pay_check(job: Job, cfg: dict) -> Tuple[str, str, int]:
    """(the stated pay, how it compares with yours, 0 within / 1 unknown / 2 outside your range)."""
    pay_text = job.pay_text or pay_from_text(job.description) or ""
    annual = parse_annual_pay(pay_text, cfg["pay_currency"])
    min_pay, max_pay = cfg.get("min_annual_pay"), cfg.get("max_annual_pay")
    if min_pay is None and max_pay is None:
        return pay_text, "No pay range set", 1
    if annual is None:
        return pay_text, "Not stated or not comparable", 1
    if min_pay is not None and annual[1] < min_pay:
        return pay_text, "Below your range", 2
    if max_pay is not None and annual[0] > max_pay:
        return pay_text, "Above your range", 2
    return pay_text, "Within your range", 0


def run_pipeline(companies: List[dict], profiles: List[dict], settings: dict, vocab: Vocab,
                 state: dict, fetch: FetchFn, now: datetime = None,
                 sleep: Callable[[float], None] = lambda s: None) -> Tuple[List[dict], Counter, List[str]]:
    now = now or datetime.now(timezone.utc)
    cfg = {**DEFAULT_SETTINGS, **settings}
    prepared = [prepare_profile(p, vocab) for p in profiles]
    for profile in prepared:
        if not profile["_titles"] and not profile["_skill_list"]:
            raise ProfileError(
                f"Stopped: profile '{profile.get('name', '?')}' has no roles and no skills, so there is nothing to "
                "match jobs against. (Empty lists and words such as 'keep' do not count.) "
                "Run 'python -m jobfinder setup' and give your resume, or edit its file.")
    first_seen: Dict[str, str] = state.setdefault("first_seen", {})
    dropped: Dict[str, str] = state.setdefault("dropped", {})
    stats: Counter = Counter()
    failures: List[str] = []
    max_age = timedelta(days=cfg["freshness_days"])

    # Pass 1: read every company and keep the fresh jobs.
    fresh = []
    first_of: Dict[tuple, str] = {}
    openings: Counter = Counter()
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
            # A removed job is remembered with its date, so it never returns as new, yet comes back if you
            # later allow older jobs. Older memory files kept only the day of removal: an undated job saved
            # that way stays removed, and a dated one is checked again against its own date.
            remembered = dropped.get(job.key)
            posted, basis = job.posted_at, job.date_basis or "platform date"
            if posted is None:
                basis = "first seen (real age unknown)"
                if remembered is not None and len(remembered) <= 10:
                    stats["already_dropped"] += 1
                    continue
                posted = parse_dt(remembered) or parse_dt(first_seen.get(job.key)) or now
                first_seen.setdefault(job.key, posted.isoformat())
            if now - posted > max_age:
                dropped[job.key] = posted.isoformat()
                first_seen.pop(job.key, None)
                stats["already_dropped" if remembered is not None else "stale_dropped"] += 1
                continue
            dropped.pop(job.key, None)
            # The same opening posted several times (same company, title, place and text) is one row.
            same = (norm(job.company), norm(job.title), norm(job.location), norm(job.description))
            if same in first_of:
                openings[first_of[same]] += 1
                stats["duplicates_merged"] += 1
                continue
            first_of[same] = job.key
            openings[job.key] = 1
            fresh.append((company, job, posted, basis, JobTerms(job.title, job.description)))

    # How rare each word is is learned from all of today's fresh jobs, so boilerplate weighs almost nothing.
    corpus = Corpus([terms for *_, terms in fresh])

    # Pass 2: the hard rules (years, place, pay, deal-breakers), then how well each job fits each track.
    role_phrases = [phrase for profile in prepared for phrase in profile["_phrases"]]
    # With 3 or more years, a "Senior" title is often within reach; the years the post asks for decide.
    excluded = [word for word in cfg["exclude_title_words"]
                if not (cfg["max_experience_years"] >= 3 and norm(word) in ("senior", "sr"))]
    if cfg["max_experience_years"] < 3:
        excluded += SENIOR_ROLE_PHRASES
    scored = []
    for company, job, posted, basis, terms in fresh:
        text = f"{job.title}. {job.description}"
        if (has_excluded_word(job.title, excluded, role_phrases)
                or higher_level_title(job.title, cfg["max_experience_years"])):
            stats["senior_title"] += 1
            continue
        bucket, exp_text = experience_bucket(text, cfg["max_experience_years"])
        if bucket == TOO_SENIOR:
            stats["too_senior"] += 1
            continue
        loc_ok = location_matches(job.location, cfg["preferred_locations"], cfg["remote_country"])
        # "Keep jobs in other cities" means other cities of your country (remote_country), not abroad.
        if cfg["location_mode"] == "require" and not loc_ok or not in_country(job.location, cfg["remote_country"]):
            stats["location_dropped"] += 1
            continue
        if (has_phrase(norm(job.location), "remote") and remote_in(norm(job.location), cfg["remote_country"])
                and not any(has_phrase(norm(job.location), place) for place in INDIA_PLACES)
                and remote_elsewhere(job.title, job.description, cfg["remote_country"])):
            stats["location_dropped"] += 1     # 'Remote', but the post says US time zones or the title says Poland
            continue
        pay_text, pay_label, pay_tier = pay_check(job, cfg)
        if cfg["pay_mode"] == "require" and pay_tier == 2:
            stats["pay_dropped"] += 1
            continue
        fits, breakers = {}, {}
        for profile in prepared:
            # Deal-breakers: a track that refuses some work (such as Coding) drops jobs that depend on it.
            refused = False
            for group in profile["_avoid"]:
                found = breakers.setdefault(group, vocab.deal_breaker_terms(group, text))
                refused = refused or vocab.deal_breaker_title(group, job.title) or len(found) >= cfg["deal_breaker_remove"]
            if not refused:
                fits[profile["name"]] = score(profile["_query"], terms, corpus)
        if not fits:
            stats["deal_breaker"] += 1
            continue
        scored.append((company, job, posted, basis, text, bucket, exp_text, loc_ok, pay_text, pay_label, pay_tier,
                       fits, breakers))

    # A strong match: its title names one of your roles, or it scores close to today's best match for the track
    # and names enough of your skills. When the track has priority skills, a match by skills alone must name at
    # least one of them: a day with no real SEO jobs then keeps none, instead of the least bad of the rest.
    best = {p["name"]: max((s[11][p["name"]][0] for s in scored if p["name"] in s[11]), default=0.0) for p in prepared}
    core = {p["name"]: set(p["_priority"]) for p in prepared}

    def strong(name, fit):
        value, titles_named, skills_named = fit
        return value > 0 and (bool(titles_named) or (
            value >= cfg["match_share"] / 100 * best[name] and len(skills_named) >= cfg["match_min_skills"]
            and (not core[name] or bool(core[name] & set(skills_named)))))

    rows: List[dict] = []
    for company, job, posted, basis, text, bucket, exp_text, loc_ok, pay_text, pay_label, pay_tier, fits, breakers \
            in scored:
        tracks = [name for name, fit in fits.items() if strong(name, fit)]
        if not tracks:
            stats["weak_match"] += 1
            continue
        # The track this job fits best, compared with each track's own best match today.
        track = max(tracks, key=lambda name: fits[name][0] / best[name])
        chosen = next(p for p in prepared if p["name"] == track)
        value, titles_named, skills_named = fits[track]
        flags = [f"mentions {group.lower()}: {', '.join(breakers[group])}" for group in chosen["_avoid"]
                 if len(breakers.get(group, [])) >= cfg["deal_breaker_flag"]]
        level = 0 if bucket == WITHIN else (1 if entry_level_title(job.title) else 2)
        missing = [s for s in vocab.extract_skills(text) if s not in chosen["_skills"]][:6]
        rows.append({
            "Track": track,
            "Company": job.company,
            "Role": job.title + (f" ({openings[job.key]} openings)" if openings[job.key] > 1 else ""),
            "Matched by": f"your role: {', '.join(titles_named)}" if titles_named else "your skills",
            "Apply link": job.url,
            "Posted": posted.date().isoformat(),
            "Date basis": basis,
            "Experience": exp_text or "Not stated",
            "Experience bucket": BUCKET_LABELS[level],
            "Priority skills": ", ".join(s for s in skills_named if s in chosen["_priority"]),
            "Deal-breaker check": "; ".join(flags),
            "Match": round(100 * value / best[track]),
            "Matched skills": ", ".join(skills_named),
            "Missing skills": ", ".join(missing),
            "Location": job.location,
            "Location match": "yes" if loc_ok else "no",
            "Shift 10-6 mentioned": "yes" if shift_mentioned(text) else "",
            "Pay": pay_text or "Not stated",
            "Pay check": pay_label,
            "Company email": company.get("email", ""),
            "Founder X handle": company.get("x_handle", ""),
            "Platform": job.platform,
            # Best match first. A job in another city, with pay outside your range, or flagged by a deal-breaker
            # ranks a quarter lower for each, so a strong match elsewhere still beats a weak one in your city.
            "_sort": (-value * 0.75 ** ((0 if loc_ok else 1) + (1 if pay_tier == 2 else 0) + (1 if flags else 0)),
                      -posted.timestamp()),
        })
        stats["kept"] += 1
        stats["kept_by_skills" if not titles_named else "kept_by_title"] += 1
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

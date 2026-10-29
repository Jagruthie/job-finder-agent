"""Interactive terminal setup: asks a few questions, reads your resume, and writes settings.json
and one profile per resume (the skills and roles found in it, as you confirm them).

Press Enter to keep the value shown in [brackets]. Type 'none' to clear an optional value.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, List, Optional

from .filters import parse_pay_preference
from .pipeline import (DEFAULT_SETTINGS, clean_list, clean_locations, clean_text, is_backup, is_non_answer,
                       profile_files)
from .resume import read_resume_text
from .vocab import Vocab

Ask = Callable[[str], str]
Say = Callable[[str], None]

TOTAL_STEPS = 6
_TO_MODE = {"keep": "prefer", "prefer": "prefer", "remove": "require", "require": "require"}
_FROM_MODE = {"prefer": "keep", "require": "remove"}


def _split(text: str) -> List[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def describe_pay(currency: str, low: Optional[float], high: Optional[float]) -> str:
    def show(value: float) -> str:
        return f"{value / 100000:g} LPA" if currency == "INR" else f"${value / 1000:g}k"

    if low is None and high is None:
        return "none"
    if low is not None and high is not None:
        return f"{show(low)} to {show(high)} a year"
    if low is not None:
        return f"at least {show(low)} a year"
    return f"up to {show(high)} a year"


def _ask_list(ask: Ask, step: int, label: str, current: List[str]) -> List[str]:
    answer = ask(f"{step}/{TOTAL_STEPS}  {label} [{', '.join(current)}]: ").strip()
    return _split(answer) if answer else list(current)


def _ask_mode(ask: Ask, say: Say, step: int, label: str, current: str) -> str:
    while True:
        answer = ask(f"{step}/{TOTAL_STEPS}  {label} (keep or remove) [{_FROM_MODE[current]}]: ").strip().lower()
        if not answer:
            return current
        if answer in _TO_MODE:
            return _TO_MODE[answer]
        say("  Please type keep or remove.")


# "1", "0-1", "0 to 1", "1 year", "7 days". A range counts as its top number.
_WHOLE_OR_RANGE = re.compile(r"(\d+)\s*(?:(?:-|–|to)\s*(\d+))?\s*(?:years?|yrs?|days?)?", re.I)


def _ask_int(ask: Ask, say: Say, step: int, label: str, current: int, minimum: int = 0) -> int:
    while True:
        answer = ask(f"{step}/{TOTAL_STEPS}  {label} [{current}]: ").strip()
        if not answer:
            return current
        match = _WHOLE_OR_RANGE.fullmatch(answer)
        if match:
            value = int(match.group(2) or match.group(1))
            if value >= minimum:
                if match.group(2):
                    say(f"  Understood: up to {value}.")
                return value
        say(f"  Please type a whole number, {minimum} or higher, or a range such as 0-1.")


def _ask_pay(ask: Ask, say: Say, step: int, current: dict):
    """Returns (currency, minimum, maximum). Typed the way people say it."""
    shown = describe_pay(current["pay_currency"], current["min_annual_pay"], current["max_annual_pay"])
    say(f"{step}/{TOTAL_STEPS}  Pay you want. Type it your own way, for example:")
    say("        8 LPA   |   more than 8 lakhs   |   6-12 LPA   |   up to 10 LPA   |   $80k   |   none")
    while True:
        answer = ask(f"        Pay [{shown}]: ").strip()
        if not answer:
            return current["pay_currency"], current["min_annual_pay"], current["max_annual_pay"]
        if answer.lower() in {"none", "no", "-", "any"}:
            return current["pay_currency"], None, None
        try:
            currency, low, high = parse_pay_preference(answer, current["pay_currency"])
        except ValueError as error:
            say(f"  {error}. Try something like: 8 LPA, 6-12 LPA, or none.")
            continue
        say(f"  Understood: {describe_pay(currency, low, high)} ({currency}).")
        return currency, low, high


def run_setup(settings_path: str, profiles_dir: str, vocab: Vocab,
              ask: Ask = input, say: Say = print, read_resume: Callable[[str], str] = read_resume_text) -> dict:
    raw_ask = ask
    ask = lambda prompt: clean_text(raw_ask(prompt))   # ignore stray keystrokes such as Escape or [ ]
    path = Path(settings_path)
    current = dict(DEFAULT_SETTINGS)
    if path.exists():
        current.update({k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items()
                        if not k.startswith("_")})
    current["preferred_locations"] = clean_locations(current["preferred_locations"]) or list(
        DEFAULT_SETTINGS["preferred_locations"])

    say("Setup: 6 quick questions, then your resume. Press Enter to keep the value in [brackets].\n")
    settings = dict(current)
    settings["preferred_locations"] = _ask_list(
        ask, 1, "Cities you prefer, comma separated", current["preferred_locations"])
    settings["location_mode"] = _ask_mode(
        ask, say, 2, f"Jobs in other cities{' of ' + current['remote_country'] if current['remote_country'] else ''}: "
        "keep them (ranked lower) or remove them?", current["location_mode"])
    settings["max_experience_years"] = _ask_int(
        ask, say, 3, "Most years of experience a job may ask for", current["max_experience_years"])
    settings["freshness_days"] = _ask_int(
        ask, say, 4, "Only keep jobs posted within this many days", current["freshness_days"], minimum=1)
    currency, low, high = _ask_pay(ask, say, 5, current)
    settings["pay_currency"], settings["min_annual_pay"], settings["max_annual_pay"] = currency, low, high
    if low is not None or high is not None:
        settings["pay_mode"] = _ask_mode(
            ask, say, 6, "Jobs whose stated pay is outside that range: keep them (ranked lower) or remove them?",
            current["pay_mode"])
        say("  Jobs that do not state pay are always kept.")
    else:
        say(f"{TOTAL_STEPS}/{TOTAL_STEPS}  (skipped, since you set no pay range)")

    out = {"_note": "Edit by hand or run: python -m jobfinder setup"}
    out.update({k: settings[k] for k in DEFAULT_SETTINGS})
    for key in ("min_annual_pay", "max_annual_pay"):
        if out[key] is not None:
            out[key] = int(round(out[key]))
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    say(f"\nSaved {path}.")

    from_resume = _ask_resumes(ask, say, Path(profiles_dir), vocab, read_resume)
    files = profile_files(profiles_dir)   # skips example.json and *.backup.json copies
    files += [file for file in from_resume if file not in files]
    if not files:
        say("\nNo profiles yet. Run setup again and give the path to your resume to make one.")
        return out
    say("\nSkills and roles to look for, per profile. Each profile is one track; give another resume for another "
        "kind of job. Every day, each job is compared with these: its title with your roles, its text with your "
        "skills (other spellings, such as PySpark for Spark, count too).")
    for file in files:
        profile = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {"name": file.stem}
        saved_skills, saved_titles = profile.get("skills", []), profile.get("titles", [])
        skills, titles = clean_list(saved_skills), clean_list(saved_titles)
        name = profile.get("name", file.stem)
        say(f"\nProfile '{name}'")
        if file in from_resume:
            skills, roles = from_resume[file]
            hint = "Enter accepts these"
            say("  Skills found in your resume:")
        else:
            roles, hint = titles, "Enter keeps these"
        new_skills = _ask_profile_list(ask, say, "Skills", skills, hint, "SQL, Airflow, Recruitment", required=False)
        if file in from_resume:
            say("  Roles found in your resume:" if roles else
                "  No job titles found in your resume. Type the roles you want, or press Enter to match by your "
                "skills only.")
            if titles and titles != roles:
                say(f"  Roles saved now: {', '.join(titles)}")
        new_titles = _fix_typos(ask, say, vocab, _ask_profile_list(
            ask, say, "Roles", roles, hint if roles else "Enter leaves them empty", "Data Engineer, Recruiter",
            required=not new_skills))
        saved_breakers, saved_priority = profile.get("deal_breakers", []), profile.get("priority_skills", [])
        suggested_breakers = clean_list(saved_breakers)
        if "deal_breakers" not in profile and "Coding" in vocab.deal_breakers and new_titles and not any(
                vocab.deal_breaker_title("Coding", title) for title in new_titles):
            suggested_breakers = ["Coding"]    # never asked before, and none of the roles is a coding job
        new_breakers = _ask_deal_breakers(ask, say, vocab, suggested_breakers)
        avoided = {s for group in new_breakers for s in vocab.deal_breakers[group].get("skills", [])}
        priority_default = [s for s in clean_list(saved_priority) if s not in avoided]
        if "priority_skills" not in profile:
            # Never asked before: suggest your skills that your roles are about (SEO, Ahrefs for an SEO role).
            priority_default = [s for s in vocab.role_skills(new_titles) if s in new_skills and s not in avoided]
        say("  Priority skills (optional): what this track's jobs should mainly be about. They count more in the "
            "match, and a job that matches by skills alone must name at least one of them.")
        new_priority = _ask_profile_list(ask, say, "Priority skills", priority_default, "Enter accepts these",
                                         "Airflow, Data Modeling", required=False)
        added = [s for s in new_priority if s not in new_skills]
        if added:
            say(f"  Added to this track's skills too: {', '.join(added)}")
            new_skills = new_skills + added
        if (file.exists() and new_skills == saved_skills and new_titles == saved_titles
                and new_breakers == saved_breakers and new_priority == saved_priority):
            continue
        profile["skills"], profile["titles"] = new_skills, new_titles
        profile["deal_breakers"], profile["priority_skills"] = new_breakers, new_priority
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        say(f"  Saved {file}.")
    return out


def _ask_resumes(ask: Ask, say: Say, profiles_dir: Path, vocab: Vocab, read_resume: Callable[[str], str]) -> dict:
    """Ask for resume files, one per profile, and read the skills and role titles in each.
    Returns {profile file: (skills found, roles named)}. Resumes are read on this computer and never saved or uploaded."""
    say("\nYour resume is read on this computer only. Nothing is uploaded and the file is not copied.")
    found = {}
    read = {}   # resume file -> profile name, so the same resume is not made into two profiles by mistake
    while True:
        prompt = "another resume" if found else "your resume"
        answer = ask(f"  Path to {prompt} (.docx, .pdf, .txt or .md), or press Enter to "
                     f"{'continue' if found else 'skip'}: ").strip()
        if not answer:
            return found
        resume = Path(answer.strip("'\"").replace("\\ ", " ")).expanduser()   # drag-and-drop adds quotes or "\ "
        if not resume.is_file():
            say(f"  Could not find a file at {resume}. Check the path, or drag the file into this window.")
            continue
        if resume.resolve() in read:
            say(f"  You already gave this resume (profile '{read[resume.resolve()]}'). One resume makes one profile; "
                "to search two kinds of roles from it, setup offers a second track after you pick its roles.")
            continue
        try:
            text = read_resume(str(resume))
        except SystemExit as error:   # unsupported type or a missing reader library
            say(f"  {error}")
            continue
        known, extra = vocab.extract_skills(text), vocab.resume_phrases(text)
        skills = known + extra
        say(f"  Found {len(skills)} skills in {resume.name}"
            + (f", including {len(extra)} from your own skill lists that the built-in list does not know"
               f" ({', '.join(extra[:5])}{', ...' if len(extra) > 5 else ''}). Delete any that are not skills."
               if extra else ".")
            + ("" if skills else " You can type your skills in the next step."))
        name = _ask_profile_name(ask, say, resume.stem)
        file = profiles_dir / f"{name}.json"
        if file.exists():
            say(f"  Profile '{name}' already exists. You will confirm the skills from this resume before it changes.")
        found[file] = (skills, vocab.resume_titles(text))
        read[resume.resolve()] = name


def _fix_typos(ask: Ask, say: Say, vocab: Vocab, roles: List[str]) -> List[str]:
    """Offer the built-in spelling for a role that looks mistyped ('Data Enginner'), since a role is matched
    by its exact words and a misspelled one finds nothing."""
    fixed = []
    for role in roles:
        close = vocab.closest_title(role)
        while close:
            answer = ask(f"  Did you mean '{close}' instead of '{role}'? (yes or no) [yes]: ").strip().lower()
            if answer in ("", "y", "yes"):
                role = close
            elif answer not in ("n", "no"):
                say("  Please type yes or no.")
                continue
            break
        if role not in fixed:
            fixed.append(role)
    return fixed


def _ask_deal_breakers(ask: Ask, say: Say, vocab: Vocab, current: List[str]) -> List[str]:
    """Work this track's jobs must not depend on, such as Coding. Enter keeps what is shown; 'none' clears it."""
    known = {group.lower(): group for group in vocab.deal_breakers}
    if not known:
        return []
    shown = ", ".join(current) or "none"
    while True:
        answer = ask(f"  Deal-breakers: work these jobs must not depend on ({', '.join(known.values())}) [{shown}] "
                     "(Enter keeps this, 'none' clears it, or type one): ").strip()
        if not answer:
            return list(current)
        if answer.lower() in ("none", "no", "-"):
            return []
        picked = [part.strip().lower() for part in answer.split(",") if part.strip()]
        if picked and all(part in known for part in picked):
            chosen = [known[part] for part in picked]
            say(f"  Jobs that clearly depend on {', '.join(c.lower() for c in chosen)} will be removed; "
                "ones that only mention it a little are kept but flagged.")
            return chosen
        say(f"  Please type one of: {', '.join(known.values())}, or 'none', or press Enter to keep [{shown}].")


def _ask_profile_name(ask: Ask, say: Say, stem: str) -> str:
    default = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:20] or "me"
    while True:
        name = ask(f"  Short name for this profile, for example pm or seo [{default}]: ").strip().lower() or default
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,29}", name) and name != "example" and not is_backup(f"{name}.json"):
            return name
        say("  Use letters, numbers, - or _ only (not 'example'), for example: seo")


def _ask_profile_list(ask: Ask, say: Say, label: str, default: List[str], hint: str, example: str,
                      required: bool = True) -> List[str]:
    """Ask for a comma separated list of skills or roles. Enter on an empty line keeps the default.
    Words like 'keep' or 'yes' are refused, so they are never saved as a role or skill."""
    noun = {"Skills": "skill", "Roles": "role", "Priority skills": "skill"}[label]
    while True:
        answer = ask(f"  {label} [{', '.join(default)}] ({hint}, or type a new comma separated list): ").strip()
        if not answer:
            values = list(default)
        else:
            values = _split(answer)
            refused = [v for v in values if is_non_answer(v)]
            if refused:
                say(f"  '{refused[0]}' is not a {noun} name. To keep what is shown in [brackets], press Enter "
                    f"on an empty line. Otherwise type {noun} names, for example: {example}")
                continue
            if not values and default:
                say(f"  That had no {noun} names, so the old list was kept.")
                values = list(default)
        if values or not required:
            return values
        say(f"  This profile needs at least one {noun}. Type one, for example: {example}")

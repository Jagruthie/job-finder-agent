"""Interactive terminal setup: asks a few questions, reads your resume, and writes settings.json
and one profile per resume (the skills you confirm, and the roles that need those skills).

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
        ask, say, 2, "Jobs in other cities: keep them (ranked lower) or remove them?", current["location_mode"])
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
    say("\nSkills and roles to look for, per profile. Known roles: " + ", ".join(sorted(vocab.titles)))
    for file in files:
        profile = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {"name": file.stem}
        saved_skills, saved_titles = profile.get("skills", []), profile.get("titles", [])
        skills, titles = clean_list(saved_skills), clean_list(saved_titles)
        say(f"\nProfile '{profile.get('name', file.stem)}'")
        if file in from_resume:
            skills, hint = from_resume[file], "Enter accepts these"
            say("  Skills found in your resume:")
        else:
            hint = "Enter keeps these"
        new_skills = _ask_profile_list(ask, say, "Skills", skills, hint, "SEO, Keyword Research", required=False)
        unknown = [s for s in new_skills if s not in vocab.skills]
        if unknown:
            say("  Not in data/skills.json, so they are saved but not counted when matching jobs: "
                + ", ".join(unknown))
        # Roles come from the confirmed skills: each role listed needs at least 2 of them.
        matches = vocab.role_matches(new_skills)
        if matches:
            say("  Roles that need your skills:")
            for title, matched, wanted in matches:
                say(f"    {title}: {len(matched)} of {len(wanted)} skills it needs ({', '.join(matched)})")
            default, hint = [title for title, _, _ in matches], "Enter accepts these"
        else:
            say("  None of the built-in roles needs 2 or more of these skills.")
            default, hint = titles, "Enter keeps these"
        if titles and titles != default:
            say(f"  Roles saved now: {', '.join(titles)}")
        new_titles = _ask_profile_list(ask, say, "Roles", default, hint, "SEO Executive")
        if file.exists() and new_skills == saved_skills and new_titles == saved_titles:
            continue
        unknown = [t for t in new_titles if t not in vocab.titles]
        if unknown:
            say("  Not in the built-in list (they will match job titles containing those exact words): "
                + ", ".join(unknown))
        profile["skills"], profile["titles"] = new_skills, new_titles
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        say(f"  Saved {file}.")
    return out


def _ask_resumes(ask: Ask, say: Say, profiles_dir: Path, vocab: Vocab, read_resume: Callable[[str], str]) -> dict:
    """Ask for resume files, one per profile, and read the skills in each.
    Returns {profile file: skills found}. Resumes are read on this computer and never saved or uploaded."""
    say("\nYour resume is read on this computer only. Nothing is uploaded and the file is not copied.")
    found = {}
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
        try:
            skills = vocab.extract_skills(read_resume(str(resume)))
        except SystemExit as error:   # unsupported type or a missing reader library
            say(f"  {error}")
            continue
        say(f"  Found {len(skills)} known skills in {resume.name}."
            + ("" if skills else " You can type your skills in the next step."))
        name = _ask_profile_name(ask, say, resume.stem)
        file = profiles_dir / f"{name}.json"
        if file.exists():
            say(f"  Profile '{name}' already exists. You will confirm the skills from this resume before it changes.")
        found[file] = skills


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
    noun = {"Skills": "skill", "Roles": "role"}[label]
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

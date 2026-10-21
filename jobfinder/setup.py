"""Interactive terminal setup: asks a few questions and writes settings.json and role titles.

Press Enter to keep the value shown in [brackets]. Type 'none' to clear an optional value.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List, Optional

from .filters import parse_pay_preference
from .pipeline import DEFAULT_SETTINGS, clean_locations, clean_text
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


def _ask_int(ask: Ask, say: Say, step: int, label: str, current: int, minimum: int = 0) -> int:
    while True:
        answer = ask(f"{step}/{TOTAL_STEPS}  {label} [{current}]: ").strip()
        if not answer:
            return current
        try:
            value = int(answer)
            if value >= minimum:
                return value
        except ValueError:
            pass
        say(f"  Please type a whole number, {minimum} or higher.")


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
              ask: Ask = input, say: Say = print) -> dict:
    raw_ask = ask
    ask = lambda prompt: clean_text(raw_ask(prompt))   # ignore stray keystrokes such as Escape or [ ]
    path = Path(settings_path)
    current = dict(DEFAULT_SETTINGS)
    if path.exists():
        current.update({k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items()
                        if not k.startswith("_")})
    current["preferred_locations"] = clean_locations(current["preferred_locations"]) or list(
        DEFAULT_SETTINGS["preferred_locations"])

    say("Setup: 6 quick questions. Press Enter to keep the value in [brackets].\n")
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

    files = sorted(f for f in Path(profiles_dir).glob("*.json") if f.stem != "example") if Path(profiles_dir).exists() else []
    if files:
        say("\nRoles to look for, per profile. Known roles: " + ", ".join(sorted(vocab.titles)))
    for file in files:
        profile = json.loads(file.read_text(encoding="utf-8"))
        titles = profile.get("titles", [])
        say(f"\nProfile '{profile.get('name', file.stem)}'")
        if titles:
            default = titles
        else:
            default = vocab.suggest_titles(profile.get("skills", []))
            say("  This profile has no roles yet" + (", so these are suggested from its skills." if default else "."))
        while True:
            hint = "Enter keeps these" if titles else "Enter accepts these"
            answer = ask(f"  Roles [{', '.join(default)}] ({hint}, or type a new comma separated list): ").strip()
            if not answer:
                new_titles = list(default)
            else:
                new_titles = _split(answer)
                if not new_titles and titles:
                    say("  That had no role names, so the old roles were kept.")
                    new_titles = list(titles)
            if new_titles:
                break
            say("  This profile needs at least one role. Type one, for example: SEO Executive")
        if new_titles == titles:
            continue
        unknown = [t for t in new_titles if t not in vocab.titles]
        if unknown:
            say("  Not in the built-in list (they will match job titles containing those exact words): "
                + ", ".join(unknown))
        profile["titles"] = new_titles
        file.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        say(f"  Saved {file}.")
    say("\nTo change skills, edit the 'skills' list in the profile file.")
    return out

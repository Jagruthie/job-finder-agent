from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from .demo import demo_data
from .pipeline import (DEFAULT_SETTINGS, ProfileError, clean_locations, load_state, read_companies, run_pipeline,
                       save_state, write_csv)
from .resume import read_resume_text
from .setup import run_setup
from .sources import fetch_board
from .vocab import Vocab


def load_settings(path: str) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    file = Path(path)
    if file.exists():
        settings.update({k: v for k, v in json.loads(file.read_text(encoding="utf-8")).items()
                         if not k.startswith("_")})
    else:
        print(f"(no {file}; using built-in defaults)")
    settings["preferred_locations"] = clean_locations(settings.get("preferred_locations")) or list(
        DEFAULT_SETTINGS["preferred_locations"])
    return settings


def load_profiles(paths: List[str]) -> List[dict]:
    profiles = []
    for path in paths:
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
        profile.setdefault("name", Path(path).stem)
        profiles.append(profile)
    return profiles


def print_profiles(profiles: List[dict]) -> None:
    for profile in profiles:
        titles = profile.get("titles", [])
        print(f"Profile '{profile.get('name', '?')}': {len(profile.get('skills', []))} skills, "
              f"looking for roles: {', '.join(titles) if titles else '(none)'}")


def print_summary(stats, failures, settings) -> None:
    print(f"\nCompanies read: {stats['boards_ok']} ok, {stats['boards_failed']} failed")
    for failure in failures:
        print(f"  - {failure}")
    print(f"Jobs seen: {stats['jobs_seen']}")
    print(f"  removed as stale (older than {settings['freshness_days']} days): {stats['stale_dropped']}")
    print(f"  skipped, already removed earlier: {stats['already_dropped']}")
    print(f"  not a role you want (title): {stats['title_mismatch']}")
    print(f"  too senior (title or years): {stats['senior_title'] + stats['too_senior']}")
    print(f"  dropped for location: {stats['location_dropped']}")
    print(f"  dropped for pay outside your range: {stats['pay_dropped']}")
    print(f"Kept: {stats['kept']} (of which experience not stated: {stats['kept_experience_not_stated']})")


def print_preview(rows: List[dict]) -> None:
    print("\nResults, best first:")
    for row in rows:
        fit = f"{row['Fit %']}%" if row["Fit %"] != "" else "n/a"
        print(f"\n{row['Rank']}. {row['Role']} at {row['Company']}  [{row['Track']}, fit {fit}]")
        print(f"   {row['Location']} | posted {row['Posted']} ({row['Date basis']}) | experience: {row['Experience']}"
              f" ({row['Experience bucket']})")
        print(f"   matched: {row['Matched skills'] or '-'}")
        print(f"   missing: {row['Missing skills'] or '-'}")
        print(f"   pay: {row['Pay']} ({row['Pay check']}) | email: {row['Company email'] or '-'} | X: {row['Founder X handle'] or '-'}")
        print(f"   apply: {row['Apply link']}")


def cmd_setup(args) -> int:
    run_setup(args.settings, args.profiles_dir, Vocab.load())
    return 0


def cmd_profile(args) -> int:
    vocab = Vocab.load()
    text = read_resume_text(args.resume)
    skills = vocab.extract_skills(text)
    titles = vocab.suggest_titles(skills)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{args.name}.json"
    target.write_text(json.dumps({
        "_note": "SUGGESTIONS ONLY. Delete skills that are wrong, add ones the agent missed (use names from data/skills.json), and fix the titles.",
        "name": args.name,
        "skills": skills,
        "titles": titles,
    }, indent=2), encoding="utf-8")
    print(f"Found {len(skills)} skills and suggested {len(titles)} titles.")
    print(f"Skills: {', '.join(skills) or '(none)'}")
    print(f"Suggested titles: {', '.join(titles) or '(none)'}")
    print(f"\nNow open {target} and edit it. This is the confirm step.")
    return 0


def cmd_demo(args) -> int:
    """Run the full pipeline on built-in FAKE jobs. Nothing is fetched and no memory is saved."""
    vocab = Vocab.load()
    settings = load_settings(args.settings)
    profiles = load_profiles(args.profiles)
    now = datetime.now(timezone.utc)
    companies, jobs = demo_data(now)
    state = {"first_seen": {}, "dropped": {}}
    print_profiles(profiles)
    try:
        rows, stats, failures = run_pipeline(companies, profiles, settings, vocab, state,
                                             lambda platform, board, name: jobs[board], now=now)
    except ProfileError as error:
        print(f"\n{error}")
        return 1
    out_path = Path(args.out_dir) / f"demo-{now.date().isoformat()}.csv"
    write_csv(rows, str(out_path))
    print("DEMO MODE: these jobs are fake. Nothing was fetched from the internet.")
    print_summary(stats, failures, settings)
    print_preview(rows)
    print(f"\nWrote {out_path}")
    return 0


def cmd_run(args) -> int:
    vocab = Vocab.load()
    settings = load_settings(args.settings)
    profiles = load_profiles(args.profiles)
    companies = read_companies(args.companies)
    if not companies:
        print("No companies found. Add rows to companies.csv.")
        return 1

    state = load_state(args.state)
    now = datetime.now(timezone.utc)
    print_profiles(profiles)
    try:
        rows, stats, failures = run_pipeline(companies, profiles, settings, vocab, state,
                                             fetch_board, now=now, sleep=time.sleep)
    except ProfileError as error:
        print(f"\n{error}")
        return 1
    save_state(args.state, state)

    out_path = Path(args.out_dir) / f"jobs-{now.date().isoformat()}.csv"
    write_csv(rows, str(out_path))
    print_summary(stats, failures, settings)
    print(f"\nWrote {out_path}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="jobfinder", description="Find fresh, skill-matched jobs on company career pages.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_profile = sub.add_parser("profile", help="read a resume and write a profile of suggested skills and titles")
    p_profile.add_argument("resume")
    p_profile.add_argument("--name", required=True, help="short name for this profile, e.g. pm or seo")
    p_profile.add_argument("--out-dir", default="profiles")
    p_profile.set_defaults(func=cmd_profile)

    p_setup = sub.add_parser("setup", help="answer a few questions to set roles, location, experience and pay")
    p_setup.add_argument("--settings", default="settings.json")
    p_setup.add_argument("--profiles-dir", default="profiles")
    p_setup.set_defaults(func=cmd_setup)

    p_demo = sub.add_parser("demo", help="try the whole flow on built-in fake jobs (no internet)")
    p_demo.add_argument("--profiles", nargs="+", required=True)
    p_demo.add_argument("--settings", default="settings.json")
    p_demo.add_argument("--out-dir", default="output")
    p_demo.set_defaults(func=cmd_demo)

    p_run = sub.add_parser("run", help="fetch jobs, filter them, and write a CSV")
    p_run.add_argument("--profiles", nargs="+", required=True)
    p_run.add_argument("--companies", default="companies.csv")
    p_run.add_argument("--settings", default="settings.json")
    p_run.add_argument("--state", default="state.json")
    p_run.add_argument("--out-dir", default="output")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

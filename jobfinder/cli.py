from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from .alerts import read_alerts
from .demo import demo_data
from .discover import (YC_HIRING, append_companies, check_board, extract_boards, find_board, known_boards,
                       yc_candidates)
from .filters import INDIA_PLACES
from .pipeline import (DEFAULT_SETTINGS, ProfileError, clean_list, clean_locations, is_backup, load_state,
                       profile_files, read_companies, run_pipeline, save_state, write_csv)
from .resume import read_resume_text
from .setup import run_setup
from .sources import SourceError, fetch_board, http_get_json
from .sources.remote import SOURCES as REMOTE_SOURCES, fetch_remote
from .textutil import norm
from .vocab import Vocab


STARTER_COMPANIES = Path(__file__).resolve().parent.parent / "data" / "companies-starter.csv"


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
        if is_backup(path):
            print(f"(skipping {path}: it is a backup copy, not a profile)")
            continue
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
        profile.setdefault("name", Path(path).stem)
        profiles.append(profile)
    return profiles


def profile_paths(args) -> List[str]:
    """The profiles named with --profiles, or else every profile in the profiles folder (one per track)."""
    if args.profiles:
        return args.profiles
    paths = [str(f) for f in profile_files(args.profiles_dir)]
    if paths:
        print(f"Using every profile in {args.profiles_dir}/: {', '.join(Path(p).name for p in paths)}")
    else:
        print(f"No profiles found in {args.profiles_dir}/. Run 'python -m jobfinder setup' and give your resume.")
    return paths


def print_profiles(profiles: List[dict]) -> None:
    for profile in profiles:
        titles = clean_list(profile.get("titles", []))
        print(f"Profile '{profile.get('name', '?')}': {len(profile.get('skills', []))} skills, "
              f"looking for roles: {', '.join(titles) if titles else '(none)'}")


def print_summary(stats, failures, settings, rows=(), profiles=()) -> None:
    print(f"\nCompanies read: {stats['boards_ok']} ok, {stats['boards_failed']} failed")
    for failure in failures:
        print(f"  - {failure}")
    print(f"Jobs seen: {stats['jobs_seen']}")
    print(f"  removed as stale (older than {settings['freshness_days']} days): {stats['stale_dropped']}")
    print(f"  skipped, already removed earlier: {stats['already_dropped']}")
    print(f"  not your roles and not enough of your skills: {stats['title_mismatch']}")
    print(f"  too senior (title or years): {stats['senior_title'] + stats['too_senior']}")
    print(f"  removed by your deal-breakers (for example coding-heavy): {stats['deal_breaker']}")
    print(f"  dropped for location: {stats['location_dropped']}")
    print(f"  dropped for pay outside your range: {stats['pay_dropped']}")
    print(f"Kept: {stats['kept']} (of which experience not stated: {stats['kept_experience_not_stated']}; "
          f"kept for your skills although the title is not on your list: {stats['kept_by_skills']})")
    if len(profiles) > 1:
        print("  per track: " + ", ".join(f"{p['name']} {sum(r['Track'] == p['name'] for r in rows)}"
                                         for p in profiles))


def print_preview(rows: List[dict], profiles=()) -> None:
    print("\nResults per track, best first:")
    for profile in profiles:
        if not any(row["Track"] == profile["name"] for row in rows):
            print(f"\n=== Track '{profile['name']}': no jobs kept ===")
    track = None
    for row in rows:
        if row["Track"] != track:
            track = row["Track"]
            count = sum(r["Track"] == track for r in rows)
            print(f"\n=== Track '{track}': {count} job{'s' if count != 1 else ''} ===")
        fit = f"{row['Fit %']}%" if row["Fit %"] != "" else "n/a"
        print(f"\n{row['Rank']}. {row['Role']} at {row['Company']}  [{row['Track']}, fit {fit}]")
        print(f"   {row['Location']} | posted {row['Posted']} ({row['Date basis']}) | experience: {row['Experience']}"
              f" ({row['Experience bucket']})")
        print(f"   matched: {row['Matched skills'] or '-'}")
        print(f"   missing: {row['Missing skills'] or '-'}")
        print(f"   pay: {row['Pay']} ({row['Pay check']}) | email: {row['Company email'] or '-'} | X: {row['Founder X handle'] or '-'}")
        print(f"   apply: {row['Apply link']}")


def cmd_add(args) -> int:
    """Turn job links (or a saved page of search results) into companies to watch."""
    text = []
    for item in args.links:
        file = Path(item).expanduser()
        text.append(file.read_text(encoding="utf-8", errors="ignore") if file.is_file() else item)
    boards = extract_boards("\n".join(text))
    if not boards:
        print("No Greenhouse, Lever or Ashby job links found. Links look like jobs.lever.co/acme, "
              "boards.greenhouse.io/acme or jobs.ashbyhq.com/acme.")
        return 1
    known = known_boards(args.companies)
    new = [pair for pair in boards if pair not in known]
    if len(new) < len(boards):
        print(f"Already in {args.companies}: {', '.join(b for p, b in boards if (p, b) in known)}")
    settings = load_settings(args.settings)
    preferred = settings["preferred_locations"]
    print(f"Checking {len(new)} companies' public job lists ({', '.join(preferred)} = your cities)...")
    results = []
    for index, (platform, board) in enumerate(new):
        if index:
            time.sleep(settings["request_delay_seconds"])
        result = check_board(platform, board, preferred, fetch_board, settings["remote_country"])
        results.append(result)
        if result["ok"]:
            print(f"  {board} ({platform}): {result['jobs']} jobs, {result['in_your_cities']} in your cities, "
                  f"{result['in_india']} in India or remote")
        else:
            print(f"  {board} ({platform}): could not read ({result['error']})")
    wanted = [r for r in results if r["ok"] and (r["in_your_cities"] or (args.all and r["jobs"]))]
    if not wanted:
        print("\nNone of these has open jobs in your cities, so nothing was added. "
              "Use --all to add companies with jobs anywhere.")
        return 0
    print(f"\nTo add: {', '.join(r['board'] for r in wanted)}")
    answer = "yes" if args.yes else input(f"Add these {len(wanted)} to {args.companies}? (yes or no) [yes]: ")
    if answer.strip().lower() not in ("", "y", "yes"):
        print("Nothing was added.")
        return 0
    append_companies(args.companies, wanted)
    print(f"Added {len(wanted)} companies to {args.companies}. Open it to fix a company name if you like.")
    return 0


def _confirm_and_add(args, results: List[dict]) -> int:
    """Add the companies with jobs in your country (or remote in it), after asking."""
    wanted = [r for r in results if r["ok"] and (r["in_india"] or (args.all and r["jobs"]))]
    if not wanted:
        print("\nNone of these has open jobs in India or remote-India, so nothing was added.")
        return 0
    print(f"\nTo add ({len(wanted)}): {', '.join(r.get('name') or r['board'] for r in wanted)}")
    answer = "yes" if args.yes else input(f"Add these {len(wanted)} to {args.companies}? (yes or no) [yes]: ")
    if answer.strip().lower() not in ("", "y", "yes"):
        print("Nothing was added.")
        return 0
    append_companies(args.companies, wanted)
    print(f"Added {len(wanted)} companies to {args.companies}. Every run now reads them and applies your filters.")
    return 0


def cmd_discover(args) -> int:
    """Find companies to watch from Y Combinator's list of hiring companies."""
    settings = load_settings(args.settings)
    places = settings["preferred_locations"] + (INDIA_PLACES if norm(settings["remote_country"]) == "india" else [])
    places = [p for p in places if norm(p) != "remote"] + (["remote"] if args.remote else [])
    print("Reading Y Combinator's list of hiring companies (unofficial daily copy, read on this computer only)...")
    try:
        companies = http_get_json(YC_HIRING, timeout=60)
    except SourceError as error:
        print(f"Could not read the list: {error}")
        return 1
    candidates = yc_candidates(companies, places)
    known = known_boards(args.companies)
    print(f"{len(companies)} YC companies are hiring; {len(candidates)} list "
          f"{'your places or remote' if args.remote else 'your places'}. Looking for their job boards...")
    if args.limit:
        candidates = candidates[:args.limit]
    results = []
    for index, company in enumerate(candidates):
        if index:
            time.sleep(0.4)
        result = find_board(company, lambda platform, board: check_board(
            platform, board, settings["preferred_locations"], fetch_board, settings["remote_country"]), time.sleep)
        if result["ok"] and (result["platform"], result["board"]) in known:
            print(f"  {result['name']}: already on your list")
            continue
        results.append(result)
        if result["ok"]:
            print(f"  {result['name']} ({result['platform']}/{result['board']}): {result['jobs']} jobs, "
                  f"{result['in_your_cities']} in your cities, {result['in_india']} in India or remote")
        else:
            print(f"  {result['name']}: {result['error']}")
    found = sum(r["ok"] for r in results)
    print(f"\nFound job boards for {found} of {len(results)} companies. "
          "The rest post jobs somewhere this agent cannot read.")
    return _confirm_and_add(args, results)


def cmd_setup(args) -> int:
    run_setup(args.settings, args.profiles_dir, Vocab.load())
    return 0


def cmd_profile(args) -> int:
    vocab = Vocab.load()
    text = read_resume_text(args.resume)
    skills = vocab.extract_skills(text) + vocab.resume_phrases(text)
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
    print(f"Suggested roles (from those skills): {', '.join(titles) or '(none)'}")
    print("\nNow run: python -m jobfinder setup")
    print("It shows these skills for you to confirm, then the roles that need those skills. This is the confirm step.")
    return 0


def cmd_demo(args) -> int:
    """Run the full pipeline on built-in FAKE jobs. Nothing is fetched and no memory is saved."""
    vocab = Vocab.load()
    settings = load_settings(args.settings)
    profiles = load_profiles(profile_paths(args))
    if not profiles:
        return 1
    now = datetime.now(timezone.utc)
    companies, jobs = demo_data(now)
    state = {"first_seen": {}, "dropped": {}}
    print_profiles(profiles)
    try:
        rows, stats, failures = run_pipeline(companies, profiles, settings, vocab, state,
                                             lambda platform, board, name: jobs[board], now=now)
    except ProfileError as error:
        print(f"\n{error}")
        print("Kept: 0 (the demo stopped before screening any jobs)")
        return 1
    out_path = Path(args.out_dir) / f"demo-{now.date().isoformat()}.csv"
    write_csv(rows, str(out_path))
    print("DEMO MODE: these jobs are fake. Nothing was fetched from the internet.")
    print_summary(stats, failures, settings, rows, profiles)
    print_preview(rows, profiles)
    print(f"\nWrote {out_path}")
    return 0


def cmd_run(args) -> int:
    vocab = Vocab.load()
    settings = load_settings(args.settings)
    profiles = load_profiles(profile_paths(args))
    if not profiles:
        return 1
    companies = read_companies(args.companies) if Path(args.companies).exists() else []
    if settings["starter_companies"] and STARTER_COMPANIES.exists():
        own = {(c["platform"].lower(), c["board"].lower()) for c in companies}
        companies += [c for c in read_companies(str(STARTER_COMPANIES))
                      if (c["platform"].lower(), c["board"].lower()) not in own]
    alert_jobs, notes = read_alerts(args.inbox)
    for note in notes:
        print(f"(inbox) {note}")
    if alert_jobs:
        print(f"Read {len(alert_jobs)} jobs from job-alert emails in {args.inbox}/")
        companies.append({"name": "Job-alert emails", "platform": "alerts", "board": args.inbox})
    wants_remote = any(norm(place) == "remote" for place in settings["preferred_locations"])
    remote = [s for s in settings["remote_sources"] if s in REMOTE_SOURCES] if wants_remote else []
    for source in remote:
        companies.append({"name": f"{source} (remote jobs)", "platform": source, "board": "remote"})
    if remote:
        print(f"Searching remote jobs open to {settings['remote_country'] or 'anyone'} on: {', '.join(remote)}")
    if not companies:
        print("No companies found and no job-alert emails. Add rows to companies.csv "
              f"or save alert emails (.eml) in {args.inbox}/.")
        return 1

    state = load_state(args.state)
    now = datetime.now(timezone.utc)

    def fetch(platform: str, board: str, name: str):
        if platform == "alerts":
            return alert_jobs
        if platform in REMOTE_SOURCES:
            return fetch_remote(platform, settings, now, http_get_json, time.sleep, args.cache)
        return fetch_board(platform, board, name)

    print_profiles(profiles)
    try:
        rows, stats, failures = run_pipeline(companies, profiles, settings, vocab, state,
                                             fetch, now=now, sleep=time.sleep)
    except ProfileError as error:
        print(f"\n{error}")
        return 1
    save_state(args.state, state)

    out_path = Path(args.out_dir) / f"jobs-{now.date().isoformat()}.csv"
    write_csv(rows, str(out_path))
    print_summary(stats, failures, settings, rows, profiles)
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

    p_add = sub.add_parser("add", help="add companies from job links or a saved page of search results")
    p_add.add_argument("links", nargs="+", help="job or careers links, or a file (for example a saved web page)")
    p_add.add_argument("--companies", default="companies.csv")
    p_add.add_argument("--settings", default="settings.json")
    p_add.add_argument("--all", action="store_true", help="also add companies with no jobs in your cities")
    p_add.add_argument("--yes", action="store_true", help="add without asking")
    p_add.set_defaults(func=cmd_add)

    p_disc = sub.add_parser("discover", help="find companies to watch from Y Combinator's hiring companies")
    p_disc.add_argument("source", choices=["yc"])
    p_disc.add_argument("--remote", action="store_true", help="also YC companies that hire remotely (slower)")
    p_disc.add_argument("--limit", type=int, default=0, help="check at most this many companies")
    p_disc.add_argument("--companies", default="companies.csv")
    p_disc.add_argument("--settings", default="settings.json")
    p_disc.add_argument("--all", action="store_true", help="also add companies with no jobs in India")
    p_disc.add_argument("--yes", action="store_true", help="add without asking")
    p_disc.set_defaults(func=cmd_discover)

    p_setup = sub.add_parser("setup", help="answer a few questions to set roles, location, experience and pay")
    p_setup.add_argument("--settings", default="settings.json")
    p_setup.add_argument("--profiles-dir", default="profiles")
    p_setup.set_defaults(func=cmd_setup)

    p_demo = sub.add_parser("demo", help="try the whole flow on built-in fake jobs (no internet)")
    p_demo.add_argument("--profiles", nargs="*", help="profile files; default: every profile in --profiles-dir")
    p_demo.add_argument("--profiles-dir", default="profiles")
    p_demo.add_argument("--settings", default="settings.json")
    p_demo.add_argument("--out-dir", default="output")
    p_demo.set_defaults(func=cmd_demo)

    p_run = sub.add_parser("run", help="fetch jobs, filter them, and write a CSV")
    p_run.add_argument("--profiles", nargs="*", help="profile files; default: every profile in --profiles-dir")
    p_run.add_argument("--profiles-dir", default="profiles")
    p_run.add_argument("--companies", default="companies.csv")
    p_run.add_argument("--settings", default="settings.json")
    p_run.add_argument("--state", default="state.json")
    p_run.add_argument("--inbox", default="inbox", help="folder of saved job-alert emails (.eml)")
    p_run.add_argument("--cache", default="cache", help="folder for today's copy of the remote-job feeds")
    p_run.add_argument("--out-dir", default="output")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

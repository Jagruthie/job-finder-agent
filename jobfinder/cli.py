"""The terminal layer: reads your command, calls the core, prints what comes back. No job-search logic lives here."""
from __future__ import annotations

import argparse
import json
import socket
import urllib.request
import webbrowser
from pathlib import Path
from typing import List

from .config import COMPANY_STATUSES, OUTREACH_STATUSES, STARTER_COMPANIES, TIERS, TRACKER_SOURCES, TRACKER_STATUSES, load_config  # noqa: F401
from .core import discovery, profiles as core_profiles, review as core_review, runner, today as core_today, tracker, watchlist
from .core.runner import prefetch  # noqa: F401
from .errors import FreshlistError, ProfileError
from .pipeline import clean_list
from .setup import run_setup
from . import autostart
from .shortcut import create as create_shortcut
from .sources import SourceError
from .vocab import Vocab


def _config(args) -> dict:
    cfg = load_config(getattr(args, "settings", None), getattr(args, "data_dir", None))
    if not cfg["settings_found"]:
        print(f"(no {cfg['settings_path']}; using built-in defaults)")
    for attribute, key in (("companies", "companies_file"), ("profiles_dir", "profiles_dir"), ("state", "seen_file"),
                           ("inbox", "inbox_dir"), ("cache", "cache_dir"), ("out_dir", "output_dir")):
        if getattr(args, attribute, None):
            cfg[key] = getattr(args, attribute)
    return cfg


def load_profiles(paths: List[str]) -> List[dict]:
    loaded, notes = core_profiles.load(paths)
    for note in notes:
        print(f"({note})")
    return loaded


def profile_paths(args, cfg: dict) -> List[str]:
    """The profiles named with --profiles, or else every profile in the profiles folder (one per track)."""
    if args.profiles:
        return args.profiles
    paths = core_profiles.find_paths(cfg)
    if paths:
        print(f"Using every profile in {cfg['profiles_dir']}/: {', '.join(Path(p).name for p in paths)}")
    else:
        print(f"No profiles found in {cfg['profiles_dir']}/. Run 'python -m jobfinder setup' and give your resume.")
    return paths


def _cell(value) -> str:
    return "" if value is None else str(value)


def print_table(rows: List[dict], columns: List[str]) -> None:
    if not rows:
        return
    widths = {c: min(40, max(len(c), *(len(_cell(row.get(c))) for row in rows))) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    for row in rows:
        print("  ".join(_cell(row.get(c))[:widths[c]].ljust(widths[c]) for c in columns))


def print_profiles(profiles: List[dict]) -> None:
    for profile in profiles:
        titles = clean_list(profile.get("titles", []))
        print(f"Profile '{profile.get('name', '?')}': {len(profile.get('skills', []))} skills, "
              f"looking for roles: {', '.join(titles) if titles else '(none)'}")


def _stat(stats: dict, key: str) -> int:
    return stats.get(key, 0)


def print_summary(stats, failures, settings, rows=(), profiles=()) -> None:
    print(f"\nCompanies read: {_stat(stats, 'boards_ok')} ok, {_stat(stats, 'boards_failed')} failed")
    for failure in failures:
        print(f"  - {failure}")
    print(f"Jobs seen: {_stat(stats, 'jobs_seen')}")
    if _stat(stats, "duplicates_merged"):
        print(f"  the same opening posted more than once, shown once: {stats['duplicates_merged']}")
    print(f"  removed as stale (older than {settings['freshness_days']} days): {_stat(stats, 'stale_dropped')}")
    print(f"  skipped, already removed earlier: {_stat(stats, 'already_dropped')}")
    print(f"  not a strong enough match for your roles and skills: {_stat(stats, 'weak_match')}")
    print(f"  too senior (title or years): {_stat(stats, 'senior_title') + _stat(stats, 'too_senior')}")
    print(f"  removed by your deal-breakers (for example coding-heavy): {_stat(stats, 'deal_breaker')}")
    print(f"  dropped for location: {_stat(stats, 'location_dropped')}")
    print(f"  dropped for pay outside your range: {_stat(stats, 'pay_dropped')}")
    print(f"Kept: {_stat(stats, 'kept')} (of which experience not stated: {_stat(stats, 'kept_experience_not_stated')}; "
          f"matched by your roles: {_stat(stats, 'kept_by_title')}; by your skills only: {_stat(stats, 'kept_by_skills')})")
    if len(profiles) > 1:
        print("  per track: " + ", ".join(f"{p['name']} {sum(r['Track'] == p['name'] for r in rows)}"
                                         for p in profiles))
    for profile in profiles:
        if not any(r["Track"] == profile["name"] for r in rows) and _stat(stats, "jobs_seen"):
            print(f"  No strong matches for track '{profile['name']}' today; nothing weaker was added to fill it.")


def print_preview(rows: List[dict], profiles=()) -> None:
    print("\nResults per track, best first (match: 100 is today's best for the track):")
    for profile in profiles:
        if not any(row["Track"] == profile["name"] for row in rows):
            print(f"\n=== Track '{profile['name']}': no jobs kept ===")
    track = None
    for row in rows:
        if row["Track"] != track:
            track = row["Track"]
            count = sum(r["Track"] == track for r in rows)
            print(f"\n=== Track '{track}': {count} job{'s' if count != 1 else ''} ===")
        print(f"\n{row['Rank']}. {row['Role']} at {row['Company']}  [{row['Track']}, match {row['Match']}]")
        print(f"   {row['Location']} | posted {row['Posted']} ({row['Date basis']}) | experience: {row['Experience']}"
              f" ({row['Experience bucket']})")
        print(f"   why: {row['Matched by']}; skills: {row['Matched skills'] or '-'}")
        print(f"   missing: {row['Missing skills'] or '-'}")
        print(f"   pay: {row['Pay']} ({row['Pay check']}) | email: {row['Company email'] or '-'} | X: {row['Founder X handle'] or '-'}")
        print(f"   apply: {row['Apply link']}")


def print_matches(matches: List[dict]) -> None:
    for match in matches:
        print(f"\n{match['number']}. {match['role']} at {match['company']}  [{match['track']}]")
        print(f"   {match['location'] or 'location not stated'} | posted {match['posted_date']} | {match['matched_by']}")
        print(f"   apply: {match['apply_link']}")


def _confirm_and_add(args, cfg, wanted: List[dict], how: str) -> int:
    if not wanted:
        print(f"\nNone of these has open jobs {how}, so nothing was added. Use --all to add companies with jobs anywhere.")
        return 0
    print(f"\nTo add ({len(wanted)}): {', '.join(r.get('name') or r['board'] for r in wanted)}")
    answer = "yes" if args.yes else input(f"Add these {len(wanted)} to your watch-list? (yes or no) [yes]: ")
    if answer.strip().lower() not in ("", "y", "yes"):
        print("Nothing was added.")
        return 0
    added = discovery.add_results(cfg, wanted)
    print(f"Added {len(added['added'])} companies to your watch-list. Open it to fix a company name if you like.")
    return 0


def cmd_add(args) -> int:
    """Turn job links (or a saved page of search results) into companies to watch."""
    cfg = _config(args)
    boards = discovery.boards_in(args.links)
    if not boards:
        print("No Greenhouse, Lever or Ashby job links found. Links look like jobs.lever.co/acme, "
              "boards.greenhouse.io/acme or jobs.ashbyhq.com/acme. For any other page use: companies add <link>")
        return 1
    known = discovery.known_boards(cfg)
    new = [pair for pair in boards if pair not in known]
    if len(new) < len(boards):
        print(f"Already in your watch-list: {', '.join(b for p, b in boards if (p, b) in known)}")
    print(f"Checking {len(new)} companies' public job lists ({', '.join(cfg['preferred_locations'])} = your cities)...")

    def show(result):
        if result["ok"]:
            print(f"  {result['board']} ({result['platform']}): {result['jobs']} jobs, {result['in_your_cities']} in "
                  f"your cities, {result['in_india']} in India or remote")
        else:
            print(f"  {result['board']} ({result['platform']}): could not read ({result['error']})")
    results = discovery.check_boards(cfg, new, progress=show)
    return _confirm_and_add(args, cfg, [r for r in results if r["ok"] and (r["in_your_cities"] or (args.all and r["jobs"]))],
                            "in your cities")


def cmd_discover(args) -> int:
    """Optional, off by default: find companies from Y Combinator's list of hiring companies."""
    cfg = _config(args)
    cfg["use_yc_discovery"] = cfg["use_yc_discovery"] or args.enable

    def show(result):
        if result.get("already_listed"):
            print(f"  {result['name']}: already on your list")
        elif result["ok"]:
            print(f"  {result['name']} ({result['platform']}/{result['board']}): {result['jobs']} jobs, "
                  f"{result['in_your_cities']} in your cities, {result['in_india']} in India or remote")
        else:
            print(f"  {result['name']}: {result['error']}")
    if cfg["use_yc_discovery"]:
        print("Reading Y Combinator's list of hiring companies (unofficial daily copy, read on this computer only)...")
    found = discovery.find_yc(cfg, args.remote, args.limit, progress=show)
    results = found["results"]
    print(f"{found['hiring']} YC companies are hiring; {found['candidates']} list your places.")
    print(f"Found job boards for {sum(r['ok'] for r in results)} of {len(results)} companies. "
          "The rest post jobs somewhere Freshlist cannot read.")
    return _confirm_and_add(args, cfg, [r for r in results if r["ok"] and (r["in_india"] or (args.all and r["jobs"]))],
                            "in India or remote-India")


def cmd_setup(args) -> int:
    cfg = load_config(args.settings, args.data_dir)
    run_setup(cfg["settings_path"], args.profiles_dir or str(Path(cfg["data_dir"]) / cfg["profiles_dir"]), Vocab.load())
    print("\nNext, choose one way to open the page each day:\n"
          "  On a Mac, keep it running and pin it in Safari: python -m jobfinder autostart on\n"
          "  Or make a desktop icon to double-click: python -m jobfinder shortcut")
    return 0


def cmd_profile(args) -> int:
    profile = core_profiles.from_resume(args.resume, args.name, args.out_dir)
    print(f"Found {len(profile['skills'])} skills and {len(profile['titles'])} roles in your resume.")
    print(f"Skills: {', '.join(profile['skills']) or '(none)'}")
    print(f"Roles: {', '.join(profile['titles']) or '(none)'}")
    print("\nNow run: python -m jobfinder setup")
    print("It shows these skills and roles for you to confirm. This is the confirm step.")
    return 0


def cmd_demo(args) -> int:
    """The filter on built-in FAKE jobs. Nothing is fetched and nothing is remembered."""
    cfg = _config(args)
    profiles = load_profiles(profile_paths(args, cfg))
    if not profiles:
        return 1
    print_profiles(profiles)
    try:
        result = runner.demo(cfg, profiles)
    except ProfileError as error:
        print(f"\n{error}")
        print("Kept: 0 (the demo stopped before screening any jobs)")
        return 1
    print("DEMO MODE: these jobs are fake. Nothing was fetched from the internet.")
    print_summary(result.stats, result.failures, cfg, result.rows, profiles)
    print_preview(result.rows, profiles)
    print(f"\nWrote {result.csv_path}")
    return 0


def cmd_run(args) -> int:
    cfg = _config(args)
    cfg["use_starter_companies"] = cfg["use_starter_companies"] or args.starter
    cfg["use_remote_boards"] = cfg["use_remote_boards"] or args.remote_boards
    cfg["use_alert_emails"] = cfg["use_alert_emails"] or args.alert_emails
    profiles = load_profiles(profile_paths(args, cfg))
    if not profiles:
        return 1
    print_profiles(profiles)
    print("\nReading job pages, one request at a time per site...")

    def show(step):
        state = f"{step['jobs']} jobs" if step["ok"] else f"could not read ({step['error']})"
        print(f"  [{step['done']}/{step['total']}] {step['company']}: {state}")
    result = runner.run(cfg, profiles, Vocab.load(), progress=show)
    for note in result.notes:
        print(f"(inbox) {note}")
    print(f"\nSources: {', '.join(result.sources)}")
    print(f"Companies read: {result.companies_read}; failed: {len(result.failed)}; "
          f"cannot be read (not Greenhouse, Lever or Ashby): {len(result.skipped)}")
    for failure in result.failed:
        print(f"  - {failure['company']} ({failure['platform']}/{failure['board']}): {failure['error']}")
    for skipped in result.skipped:
        print(f"  - {skipped['company']}: {skipped['reason']}")
    for warning in result.warnings:
        print(f"  - {warning}")
    if not result.saved:
        print("\nNo company could be read, so nothing was remembered. Check your connection and run again.")
        return 1
    stats = result.stats
    print(f"\nJobs seen: {_stat(stats, 'jobs_seen')}. Removed: older than {cfg['freshness_days']} days "
          f"{_stat(stats, 'stale_dropped') + _stat(stats, 'already_dropped')}, too senior "
          f"{_stat(stats, 'senior_title') + _stat(stats, 'too_senior')}, location {_stat(stats, 'location_dropped')}, "
          f"pay {_stat(stats, 'pay_dropped')}, deal-breakers {_stat(stats, 'deal_breaker')}, "
          f"not a strong match {_stat(stats, 'weak_match')}.")
    print(f"New matches: {len(result.new_matches)} (already shown on earlier runs, left out: {result.already_shown})")
    print_matches(result.new_matches)
    if result.new_matches:
        print("\nLog one with: python -m jobfinder tracker add --from-run <number>")
    print(f"\nWrote {result.csv_path}")
    return 0


def _print_companies(rows: List[dict]) -> None:
    if not rows:
        print("Your watch-list is empty. Add a company with: python -m jobfinder companies add <careers link>")
    print_table(rows, ["company", "platform", "tier", "status", "added_date", "last_match_date", "careers_url"])


def cmd_companies(args) -> int:
    cfg = _config(args)
    action = args.action
    if action == "add":
        row = watchlist.add(cfg, args.url, args.name, args.tier, notes=args.notes)
        print(f"Added {row['company']} ({row['platform']}, {row['tier']}).")
        if row["platform"] == "other":
            print("This is not a Greenhouse, Lever or Ashby page, so it is listed but cannot be read on a run.")
    elif action == "list":
        _print_companies(watchlist.list_companies(cfg, args.tier, args.status))
    elif action == "set-tier":
        row = watchlist.set_tier(cfg, args.company, args.value)
        print(f"{row['company']} is now tier {row['tier']}.")
    elif action == "set-status":
        row = watchlist.set_status(cfg, args.company, args.value)
        print(f"{row['company']} is now {row['status']}.")
    elif action == "import":
        result = watchlist.import_csv(cfg, args.file)
        print(f"Added {len(result['added'])} companies; skipped {len(result['skipped'])}.")
        for item in result["skipped"]:
            print(f"  - {item['company']}: {item['reason']}")
    elif action == "migrate":
        result = watchlist.migrate(cfg)
        print(f"Converted {result['migrated']} companies to the watch-list columns.")
        print(f"The old file is kept as {result['backup']}.")
    return 0


_TRACKER_FIELDS = ["company", "role", "apply_link", "posted_date", "source", "resume_version", "changes_made",
                   "how_i_would_explain_it", "founder_x_handle", "company_email", "applied_date", "outreach_status",
                   "followup_date", "status", "notes", "apply_deadline", "interview_date"]


def _tracker_fields(args) -> dict:
    return {name: getattr(args, name) for name in _TRACKER_FIELDS if getattr(args, name) is not None}


def _print_entries(rows: List[dict]) -> None:
    print_table(rows, ["id", "company", "role", "status", "source", "resume_version", "applied_date",
                       "followup_date", "outreach_status"])


def cmd_tracker(args) -> int:
    cfg = _config(args)
    action = args.action
    if action == "add":
        fields = _tracker_fields(args)
        entry = (tracker.add_from_run(cfg, args.from_run, **fields) if args.from_run is not None
                 else tracker.add(cfg, **fields))
        print(f"Added {entry['id']}: {entry['role']} at {entry['company']} ({entry['status']}).")
    elif action == "update":
        entry = tracker.update(cfg, args.id, **_tracker_fields(args))
        print(f"Updated {entry['id']}: {entry['role']} at {entry['company']} ({entry['status']}"
              + (f", follow up on {entry['followup_date']}" if entry["followup_date"] else "") + ").")
    elif action == "list":
        rows = tracker.list_entries(cfg, args.status)
        if not rows:
            print("No tracker entries" + (f" with status {args.status}." if args.status else " yet."))
        _print_entries(rows)
        for problem in tracker.problems(cfg):
            print(f"\nWarning, {problem['id']}: {problem['problem']}")
    elif action == "due":
        rows = tracker.due(cfg)
        if not rows:
            print("No follow-ups due.")
        print_table(rows, ["id", "company", "role", "applied_date", "followup_date", "days_overdue"])
    elif action == "export":
        print(f"Wrote {tracker.export_xlsx(cfg, args.out, args.status)}")
    return 0


def cmd_today(args) -> int:
    cfg = _config(args)
    plan = core_today.checklist(cfg, override=args.day or "")
    print(f"{plan['date']}: Day {plan['day']}" + (" (your choice)" if plan["chosen_by"] == "override" else "") + "\n")
    for step in plan["steps"]:
        print(f"[{'x' if step['done'] else ' '}] {step['text']}")
    if plan["day"] == "A":
        if plan["new_matches"]:
            print("\nToday's new matches:")
            print_table(plan["new_matches"], ["number", "company", "role", "posted_date", "in_tracker", "apply_link"])
        if plan["to_apply"]:
            print("\nIn the tracker, not applied yet:")
            _print_entries(plan["to_apply"])
    else:
        if plan["outreach_pending"]:
            print("\nApplications waiting for outreach:")
            print_table(plan["outreach_pending"], ["id", "company", "role", "applied_date", "founder_x_handle", "company_email"])
        if plan["followups_due"]:
            print("\nFollow-ups due:")
            print_table(plan["followups_due"], ["id", "company", "role", "followup_date", "days_overdue"])
    return 0


def cmd_review(args) -> int:
    cfg = _config(args)
    report = core_review.review(cfg)
    print(f"Review for {report['date']}. This only reports: nothing was changed.\n")
    print_table(report["companies"], ["company", "tier", "status", "matches_recent", "matches_total",
                                      "last_match_date", "days_without_match", "flag_quiet"])
    if report["quiet"]:
        print("\nQuiet companies:")
        for company in report["quiet"]:
            print(f"  - {company['company']}: {company['suggestion']}")
    if report["failed_last_run"]:
        print("\nCould not be read on the last run:")
        for failure in report["failed_last_run"]:
            print(f"  - {failure['company']}: {failure['error']}")
    checkpoint = report["checkpoint"]
    print(f"\n{'WARNING: ' if checkpoint['warning'] else ''}Checkpoint: {checkpoint['message']}")
    return 0


def _port_is_busy(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _freshlist_is_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/jobs/status", timeout=2) as reply:
            return "status" in json.load(reply)
    except (OSError, ValueError):
        return False


def cmd_shortcut(args) -> int:
    path = create_shortcut(args.to, args.data_dir, args.port)
    print(f"Made {path}. From now on, double-click it to open Freshlist. You don't need to type anything.")
    return 0


def cmd_autostart(args) -> int:
    if args.state == "off":
        removed = autostart.turn_off()
        print("Freshlist no longer starts in the background." if removed else "Freshlist was not set to start in the background.")
        return 0
    autostart.turn_on(args.data_dir, args.port)
    print(f"Freshlist now runs in the background and starts again each time you log in.\n"
          f"Open http://localhost:{args.port} in Safari, then right-click its tab and choose Pin Tab.\n"
          f"If macOS asks whether Python may use your Desktop folder, choose Allow. "
          f"Problems are written to {autostart.log_path()}.\n"
          "To stop it: python -m jobfinder autostart off")
    return 0


def cmd_ui(args) -> int:
    """Open the local web page. It listens only on this computer."""
    try:
        from werkzeug.serving import make_server
        from .web import create_app
    except ImportError:
        print("The web page needs Flask. Install it with: pip install -r requirements.txt")
        return 1
    if _port_is_busy(args.port):     # checked first, because make_server ends the program when the port is taken
        if _freshlist_is_running(args.port):
            print(f"Freshlist is already open at http://127.0.0.1:{args.port}.")
            if not args.no_open:
                webbrowser.open(f"http://127.0.0.1:{args.port}")
            return 0
        print(f"Port {args.port} is already in use. Try another: python -m jobfinder ui --port {args.port + 1}")
        return 1
    server = make_server("127.0.0.1", args.port, create_app(args.data_dir, args.settings), threaded=True)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Freshlist is open at {url} (only on this computer). Keep this window open while you use it; "
          "close it or press Ctrl+C to stop.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def _add_tracker_flags(parser) -> None:
    for name in _TRACKER_FIELDS:
        choices = {"source": TRACKER_SOURCES, "status": TRACKER_STATUSES, "outreach_status": OUTREACH_STATUSES}.get(name)
        parser.add_argument(f"--{name.replace('_', '-')}", dest=name, choices=choices)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobfinder", description="Freshlist: watch a fixed list of company career pages, see only the new jobs "
                                      "that match you, and track every application. Nothing is ever sent for you.")
    parser.add_argument("--data-dir", help="folder holding your files (default: this folder)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="answer a few questions to set your filters and read your resume")
    p_setup.add_argument("--settings")
    p_setup.add_argument("--profiles-dir")
    p_setup.set_defaults(func=cmd_setup)

    p_companies = sub.add_parser("companies", help="manage your watch-list of company career pages")
    actions = p_companies.add_subparsers(dest="action", required=True)
    p = actions.add_parser("add", help="add a company; the platform is detected from the link")
    p.add_argument("url")
    p.add_argument("--name", default="")
    p.add_argument("--tier", default="watch", choices=TIERS)
    p.add_argument("--notes", default="")
    p = actions.add_parser("list", help="list your companies")
    p.add_argument("--tier", default="", choices=("",) + TIERS)
    p.add_argument("--status", default="", choices=("",) + COMPANY_STATUSES)
    p = actions.add_parser("set-tier", help="set a company's tier")
    p.add_argument("company")
    p.add_argument("value", choices=TIERS)
    p = actions.add_parser("set-status", help="set a company's status; only active ones are checked on a run")
    p.add_argument("company")
    p.add_argument("value", choices=COMPANY_STATUSES)
    p = actions.add_parser("import", help="add many companies from a plain CSV")
    p.add_argument("file")
    actions.add_parser("migrate", help="convert an older companies.csv to the new columns, keeping a backup")
    for action in actions.choices.values():
        action.add_argument("--companies")
        action.add_argument("--settings")
    p_companies.set_defaults(func=cmd_companies)

    p_run = sub.add_parser("run", help="check your watch-list and show only new matching jobs")
    p_run.add_argument("--profiles", nargs="*", help="profile files; default: every profile in the profiles folder")
    p_run.add_argument("--profiles-dir")
    p_run.add_argument("--companies")
    p_run.add_argument("--settings")
    p_run.add_argument("--state", help="file that remembers jobs already shown")
    p_run.add_argument("--inbox", help="folder of saved job-alert emails (.eml)")
    p_run.add_argument("--cache", help="folder for today's copy of the remote-job feeds")
    p_run.add_argument("--out-dir")
    p_run.add_argument("--starter", action="store_true", help="optional: also read the built-in starter companies")
    p_run.add_argument("--remote-boards", action="store_true", help="optional: also read the Himalayas and Remote OK feeds")
    p_run.add_argument("--alert-emails", action="store_true", help="optional: also read saved LinkedIn alert emails")
    p_run.set_defaults(func=cmd_run)

    p_tracker = sub.add_parser("tracker", help="log and follow up every application, from any source")
    actions = p_tracker.add_subparsers(dest="action", required=True)
    p = actions.add_parser("add", help="add an entry, or a match from the last run with --from-run")
    p.add_argument("--from-run", type=int, help="the match's number in the last run")
    _add_tracker_flags(p)
    p = actions.add_parser("update", help="change an entry; an empty value clears a field")
    p.add_argument("id")
    _add_tracker_flags(p)
    p = actions.add_parser("list", help="list entries")
    p.add_argument("--status", default="", choices=("",) + TRACKER_STATUSES)
    actions.add_parser("due", help="applications whose follow-up date has arrived")
    p = actions.add_parser("export", help="write the tracker to an Excel .xlsx file")
    p.add_argument("--out", help="file to write; default: tracker.xlsx in the output folder")
    p.add_argument("--status", default="", choices=("",) + TRACKER_STATUSES)
    for action in actions.choices.values():
        action.add_argument("--settings")
    p_tracker.set_defaults(func=cmd_tracker)

    p_today = sub.add_parser("today", help="today's checklist for the optional two-day rotation")
    p_today.add_argument("--day", type=str.upper, choices=("A", "B"), help="use this day instead of the date's")
    p_today.add_argument("--settings")
    p_today.set_defaults(func=cmd_today)

    p_review = sub.add_parser("review", help="weekly report: matches per company, quiet companies, the checkpoint")
    p_review.add_argument("--settings")
    p_review.set_defaults(func=cmd_review)

    p_demo = sub.add_parser("demo", help="try the filter on built-in fake jobs (no internet)")
    p_demo.add_argument("--profiles", nargs="*", help="profile files; default: every profile in the profiles folder")
    p_demo.add_argument("--profiles-dir")
    p_demo.add_argument("--settings")
    p_demo.add_argument("--out-dir")
    p_demo.set_defaults(func=cmd_demo)

    p_add = sub.add_parser("add", help="add companies to your watch-list from job links or a saved search page")
    p_add.add_argument("links", nargs="+", help="job or careers links, or a file (for example a saved web page)")
    p_add.add_argument("--companies")
    p_add.add_argument("--settings")
    p_add.add_argument("--all", action="store_true", help="also add companies with no jobs in your cities")
    p_add.add_argument("--yes", action="store_true", help="add without asking")
    p_add.set_defaults(func=cmd_add)

    p_disc = sub.add_parser("discover", help="optional, off by default: find companies from Y Combinator's list")
    p_disc.add_argument("source", choices=["yc"])
    p_disc.add_argument("--enable", action="store_true", help="turn this optional source on for this command")
    p_disc.add_argument("--remote", action="store_true", help="also YC companies that hire remotely (slower)")
    p_disc.add_argument("--limit", type=int, default=0, help="check at most this many companies")
    p_disc.add_argument("--companies")
    p_disc.add_argument("--settings")
    p_disc.add_argument("--all", action="store_true", help="also add companies with no jobs in India")
    p_disc.add_argument("--yes", action="store_true", help="add without asking")
    p_disc.set_defaults(func=cmd_discover)

    p_profile = sub.add_parser("profile", help="read a resume and write a profile of suggested skills and titles")
    p_profile.add_argument("resume")
    p_profile.add_argument("--name", required=True, help="short name for this profile, e.g. pm or seo")
    p_profile.add_argument("--out-dir", default="profiles")
    p_profile.set_defaults(func=cmd_profile)

    p_ui = sub.add_parser("ui", help="open the local web page")
    p_ui.add_argument("--port", type=int, default=8765)
    p_ui.add_argument("--no-open", action="store_true", help="do not open the browser")
    p_ui.add_argument("--settings")
    p_ui.set_defaults(func=cmd_ui)

    p_shortcut = sub.add_parser("shortcut", help="make an icon you double-click to open the web page")
    p_shortcut.add_argument("--to", help="folder to put it in; default: your Desktop")
    p_shortcut.add_argument("--port", type=int, default=8765)
    p_shortcut.set_defaults(func=cmd_shortcut)

    p_auto = sub.add_parser("autostart", help="macOS: keep the web page running in the background, for a pinned Safari tab")
    p_auto.add_argument("state", choices=["on", "off"])
    p_auto.add_argument("--port", type=int, default=8765)
    p_auto.set_defaults(func=cmd_autostart)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FreshlistError as error:
        print(f"\n{error}")
        return 1
    except SourceError as error:
        print(f"\nCould not read the list: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

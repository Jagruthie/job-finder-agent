"""The web layer: a page on your own computer that calls the core. No job-search logic lives here."""
from __future__ import annotations

import secrets
import threading
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from ..config import COMPANY_STATUSES, OUTREACH_STATUSES, TIERS, TRACKER_SOURCES, TRACKER_STATUSES, load_config
from ..core import agenda, discovery, learning, profiles as core_profiles, review as core_review, runner, today as core_today, tracker, watchlist
from ..errors import FreshlistError, NotFoundError, ValidationError
from ..sources import fetch_board
from .background import BackgroundRun

LOCAL_HOSTS = ("127.0.0.1", "localhost")
OPTIONAL_SOURCES = {"starter": "use_starter_companies", "remote_boards": "use_remote_boards",
                    "alert_emails": "use_alert_emails"}
ENTRY_FIELDS = [column for column in tracker.COLUMNS if column != "id"]


def entry_values(form) -> dict:
    return {field: form.get(field, "").strip() for field in ENTRY_FIELDS if field in form}


def show_value(value: str) -> str:
    """A stored value as a person reads it: to_apply becomes to apply."""
    return (value or "").replace("_", " ")


def safe_link(url: str) -> str:
    """The link if it is a normal web address, or empty. companies.csv can be edited by hand, so never trust it."""
    return url if urlparse(url or "").scheme in ("http", "https") else ""


def left_out(stats: dict, cfg: dict) -> list:
    """Why the other jobs were not shown, as (reason, count), leaving out reasons that removed nothing."""
    count = stats.get
    reasons = [(f"Older than {cfg['freshness_days']} days", count("stale_dropped", 0) + count("already_dropped", 0)),
               ("Too senior", count("senior_title", 0) + count("too_senior", 0)),
               ("Location", count("location_dropped", 0)),
               ("Pay outside your range", count("pay_dropped", 0)),
               ("Your deal-breakers", count("deal_breaker", 0)),
               ("Not a strong enough match", count("weak_match", 0))]
    return [reason for reason in reasons if reason[1]]


def read_upload(upload) -> str:
    """The text of an uploaded CSV. Spreadsheets save CSVs in more than one encoding, so two are tried."""
    name = (upload.filename or "").lower()
    if name.endswith((".xlsx", ".xls", ".numbers")):
        raise ValidationError("Please save the spreadsheet as a CSV file first, then upload that.")
    raw = upload.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def create_app(data_dir=None, settings=None, fetch=None) -> Flask:
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    app.jinja_env.filters["safe_link"] = safe_link
    app.jinja_env.filters["show_value"] = show_value
    write_lock = threading.Lock()   # the storage module has no locking, so only one change happens at a time
    background = BackgroundRun()
    app.extensions["freshlist_run"] = background
    file_check = BackgroundRun()
    app.extensions["freshlist_file_check"] = file_check

    def config() -> dict:
        return load_config(settings, data_dir)

    def company_filters(source) -> dict:
        tier, status = source.get("tier", ""), source.get("status", "")
        return {"tier": tier if tier in TIERS else "", "status": status if status in COMPANY_STATUSES else ""}

    def companies_page(filters: dict, entered=None, code: int = 200):
        cfg = config()
        if watchlist.needs_migration(cfg):
            return render_template("migrate.html", active="companies"), code
        everything = watchlist.read(cfg)
        readable, unreadable = watchlist.for_run(everything)
        rows = watchlist.list_companies(cfg, filters["tier"], filters["status"])
        return render_template("companies.html", active="companies", rows=rows, filters=filters, tiers=TIERS,
                               statuses=COMPANY_STATUSES, entered=entered or {}, total=len(everything),
                               readable=len(readable), unreadable=len(unreadable)), code

    @app.before_request
    def only_from_this_computer():
        # Stops other websites you have open from reading or changing your data through this local page.
        if request.host.split(":")[0] not in LOCAL_HOSTS:
            abort(403)
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin and urlparse(origin).netloc != request.host:
            abort(403)
        # A run updates companies.csv when it finishes, so the list stays as it is until then.
        if request.method == "POST" and request.path.startswith("/companies") and background.running():
            flash("A run is checking your companies. Change the list when it has finished.", "warning")
            return redirect(url_for("companies"))

    @app.errorhandler(FreshlistError)
    def show_problem(error):
        return render_template("problem.html", message=str(error)), 400

    @app.errorhandler(413)
    def file_too_large(_):
        return render_template("problem.html", message="That file is too large. Freshlist accepts files up to 2 MB."), 413

    @app.get("/")
    def home():
        return redirect(url_for("jobs"))

    @app.get("/jobs")
    def jobs():
        cfg = config()
        last = runner.last_run(cfg)
        result = background.result
        logged = tracker.logged_links(cfg)
        new_ids = {match["job_id"] for match in result.new_matches} if result else set()
        matches = [{**match, "in_tracker": match["apply_link"].lower() in logged, "is_new": match["job_id"] in new_ids}
                   for match in last.get("matches", [])]
        return render_template("jobs.html", active="jobs", run=background.state(), result=result, matches=matches,
                               last_date=last.get("date", ""), is_today=last.get("date") == date.today().isoformat(),
                               has_profiles=bool(core_profiles.find_paths(cfg)), cfg=cfg,
                               left_out=left_out(result.stats, cfg) if result else [])

    @app.get("/jobs/status")
    def run_status():
        return jsonify(background.state())

    @app.post("/jobs/run")
    def start_run():
        cfg = config()
        if watchlist.needs_migration(cfg):
            flash("Convert your company list first, then check for new jobs.", "warning")
            return redirect(url_for("companies"))
        profiles, _ = core_profiles.load(core_profiles.find_paths(cfg))
        if not profiles:
            flash("You need a profile first. Run this in your terminal: python -m jobfinder setup", "error")
            return redirect(url_for("jobs"))
        for box, setting in OPTIONAL_SOURCES.items():
            cfg[setting] = cfg[setting] or box in request.form
        chosen = cfg["use_starter_companies"] or cfg["use_remote_boards"] or cfg["use_alert_emails"]
        if not chosen and not watchlist.for_run(watchlist.read(cfg))[0]:
            flash("Add a company Freshlist can read first, on the Companies page.", "warning")
            return redirect(url_for("jobs"))
        if not background.start(lambda progress: runner.run(cfg, profiles, fetch=fetch, progress=progress)):
            flash("A run is already going.", "warning")
        return redirect(url_for("jobs"))

    @app.post("/jobs/track")
    def track_match():
        # Found by the job's id, not its number: numbers restart each day, and this page may be older than the last run.
        cfg = config()
        job_id = request.form.get("job_id", "")
        match = next((m for m in runner.last_run(cfg).get("matches", []) if m["job_id"] == job_id), None)
        if match is None:
            flash("That job is no longer in the latest list. Here is the current one.", "warning")
            return redirect(url_for("jobs"))
        try:
            with write_lock:
                entry = tracker.add_from_run(cfg, match["number"])
            topics = [item for item in learning.read(cfg) if item["entry_id"] == entry["id"]]
            flash(f"Added {entry['role']} at {entry['company']} to your tracker, marked to apply. Applying is up to you."
                  + (f" {len(topics)} {'skill' if len(topics) == 1 else 'skills'} it names went on your Learn list." if topics else ""), "success")
        except FreshlistError as error:
            flash(str(error), "error")
        return redirect(url_for("today" if request.form.get("back") == "today" else "jobs"))

    def tracker_filter(source) -> str:
        status = source.get("status", "")
        return status if status in TRACKER_STATUSES else ""

    def entry_page(entry: dict, entry_id: str = "", code: int = 200):
        cfg = config()
        versions = list(cfg["resume_versions"])
        if entry.get("resume_version") and entry["resume_version"] not in versions:
            versions.append(entry["resume_version"])    # keep a value set by hand instead of silently changing it
        return render_template("entry.html", active="tracker", entry=entry, entry_id=entry_id, versions=versions,
                               sources=TRACKER_SOURCES, statuses=TRACKER_STATUSES, outreach=OUTREACH_STATUSES,
                               followup_days=cfg["followup_days"]), code

    def find_entry(cfg: dict, entry_id: str) -> dict:
        entry = next((row for row in tracker.read(cfg) if row["id"] == entry_id), None)
        if entry is None:
            raise NotFoundError(f"No tracker entry with id '{entry_id}'.")
        return entry

    def learning_for(entry_id: str) -> list:
        return [item for item in learning.read(config()) if item["entry_id"] == entry_id]

    def learn_note(entry_id: str, before: int) -> str:
        added = len(learning_for(entry_id)) - before
        return f" {added} new {'thing' if added == 1 else 'things'} to learn went on your Learn list." if added > 0 else ""

    @app.get("/tracker")
    def tracker_list():
        cfg = config()
        status = tracker_filter(request.args)
        everything = tracker.read(cfg)
        return render_template("tracker.html", active="tracker", rows=list(reversed(tracker.list_entries(cfg, status))),
                               status=status, statuses=TRACKER_STATUSES, total=len(everything),
                               to_apply=sum(row["status"] == "to_apply" for row in everything),
                               applied=sum(bool(row["applied_date"]) for row in everything),
                               due=tracker.due(cfg), problems=tracker.problems(cfg))

    @app.get("/tracker/new")
    def new_entry():
        return entry_page({"status": "to_apply", "outreach_status": "not_started"})

    @app.post("/tracker/new")
    def add_entry():
        values = entry_values(request.form)
        try:
            with write_lock:
                entry = tracker.add(config(), **{field: value for field, value in values.items() if value})
        except FreshlistError as error:
            flash(str(error), "error")
            return entry_page(values, code=400)
        flash(f"Added {entry['role']} at {entry['company']}." + learn_note(entry["id"], 0), "success")
        return redirect(url_for("tracker_list"))

    @app.post("/tracker/export")
    def export_tracker():
        try:
            path = tracker.export_xlsx(config(), status=tracker_filter(request.form))
        except FreshlistError as error:
            flash(str(error), "error")
            return redirect(url_for("tracker_list"))
        return send_file(Path(path).resolve(), as_attachment=True, download_name="tracker.xlsx")

    @app.get("/tracker/<entry_id>")
    def edit_entry(entry_id):
        entry = find_entry(config(), entry_id)
        if request.args.get("status") in TRACKER_STATUSES:     # sent here to finish a status change
            entry = {**entry, "status": request.args["status"]}
        return entry_page(entry, entry_id)

    @app.post("/tracker/<entry_id>")
    def save_entry(entry_id):
        cfg = config()
        values = entry_values(request.form)
        # Only what changed goes to the core, because a date you did not touch must still be worked out for you.
        changes = {field: value for field, value in values.items() if value != find_entry(cfg, entry_id)[field]}
        if not changes:
            flash("Nothing changed.", "warning")
            return redirect(url_for("tracker_list"))
        listed = len(learning_for(entry_id))
        try:
            with write_lock:
                entry = tracker.update(cfg, entry_id, **changes)
        except FreshlistError as error:
            flash(str(error), "error")
            return entry_page(values, entry_id, code=400)
        flash(f"Saved {entry['role']} at {entry['company']}." + learn_note(entry_id, listed), "success")
        return redirect(url_for("tracker_list"))

    @app.post("/tracker/<entry_id>/status")
    def change_entry_status(entry_id):
        status = request.form.get("status", "")
        try:
            with write_lock:
                entry = tracker.update(config(), entry_id, status=status)
        except FreshlistError as error:
            flash(str(error), "error")
            return redirect(url_for("edit_entry", entry_id=entry_id, status=status))
        message = f"{entry['company']} is now {show_value(entry['status'])}."
        if entry["status"] == "applied" and entry["followup_date"]:
            message += f" Follow up on {entry['followup_date']}."
        flash(message, "success")
        kept = tracker_filter({"status": request.form.get("filter_status", "")})
        return redirect(url_for("tracker_list", **({"status": kept} if kept else {})))

    def back_to_learn(entry_id: str = ""):
        month, show = request.form.get("month", ""), request.form.get("show", "")
        args = {key: value for key, value in (("month", month), ("show", show)) if value}
        return redirect(url_for("learn", **args, _anchor=f"job-{entry_id}" if entry_id else None))

    @app.get("/learn")
    def learn():
        cfg = config()
        now = date.today()
        year, month = agenda.month_of(request.args.get("month", ""), now)
        show = "all" if request.args.get("show") == "all" else ""
        return render_template("learn.html", active="learn", view=agenda.month_view(cfg, year, month, now), show=show,
                               month=f"{year:04d}-{month:02d}", jobs=agenda.jobs_to_prepare(cfg, bool(show), now),
                               finished=sum(row["status"] in agenda.FINISHED for row in tracker.read(cfg)),
                               missed=agenda.upcoming(cfg, now)["missed"])

    @app.post("/learn/<entry_id>/items")
    def add_learning(entry_id):
        cfg = config()
        find_entry(cfg, entry_id)
        try:
            with write_lock:
                learning.add(cfg, entry_id, request.form.get("topic", ""), request.form.get("learn_by", ""))
        except FreshlistError as error:
            flash(str(error), "error")
        return back_to_learn(entry_id)

    @app.post("/learn/<entry_id>/dates")
    def save_job_dates(entry_id):
        cfg = config()
        current = find_entry(cfg, entry_id)
        changes = {field: request.form.get(field, "").strip() for field in ("apply_deadline", "interview_date")
                   if field in request.form and request.form[field].strip() != current[field]}
        try:
            if changes:
                with write_lock:
                    tracker.update(cfg, entry_id, **changes)
                flash(f"Saved the dates for {current['company']}.", "success")
        except FreshlistError as error:
            flash(str(error), "error")
        return back_to_learn(entry_id)

    @app.post("/learn/items/<item_id>/done")
    def tick_learning(item_id):
        try:
            with write_lock:
                item = learning.set_done(config(), item_id, "done" in request.form)
            return back_to_learn(item["entry_id"])
        except FreshlistError as error:
            flash(str(error), "error")
            return back_to_learn()

    @app.post("/learn/items/<item_id>/date")
    def date_learning(item_id):
        try:
            with write_lock:
                item = learning.set_learn_by(config(), item_id, request.form.get("learn_by", ""))
            return back_to_learn(item["entry_id"])
        except FreshlistError as error:
            flash(str(error), "error")
            return back_to_learn()

    @app.post("/learn/items/<item_id>/remove")
    def remove_learning(item_id):
        try:
            with write_lock:
                item = learning.remove(config(), item_id)
            return back_to_learn(item["entry_id"])
        except FreshlistError as error:
            flash(str(error), "error")
            return back_to_learn()

    @app.get("/today")
    def today():
        wanted = request.args.get("day", "").upper()
        automatic = core_today.which_day(date.today())
        plan = core_today.checklist(config(), override=wanted if wanted in ("A", "B") and wanted != automatic else "")
        return render_template("today.html", active="today", plan=plan, other_day="B" if plan["day"] == "A" else "A",
                               automatic_day=automatic, dates=agenda.upcoming(config()))

    @app.get("/review")
    def review():
        cfg = config()
        if watchlist.needs_migration(cfg):
            return render_template("migrate.html", active="review")
        return render_template("review.html", active="review", report=core_review.review(cfg),
                               recent_days=cfg["review_recent_days"])

    @app.get("/companies")
    def companies():
        return companies_page(company_filters(request.args))

    @app.post("/companies")
    def add_company():
        form = request.form
        try:
            with write_lock:
                row = watchlist.add(config(), form.get("url", ""), form.get("name", "").strip(),
                                    form.get("tier", "watch"), notes=form.get("notes", "").strip())
        except FreshlistError as error:
            flash(str(error), "error")
            return companies_page({"tier": "", "status": ""}, entered=form, code=400)
        if row["platform"] == "other":
            flash(f"Added {row['company']}, but Freshlist can only read Greenhouse, Lever and Ashby pages, "
                  "so a run will skip it.", "warning")
        else:
            flash(f"Added {row['company']} ({row['platform']}).", "success")
        return redirect(url_for("companies"))

    @app.post("/companies/update")
    def update_company():
        form = request.form
        back = url_for("companies", **{key: value for key, value in company_filters({
            "tier": form.get("filter_tier", ""), "status": form.get("filter_status", "")}).items() if value})
        try:
            with write_lock:
                if "tier" in form:
                    row = watchlist.set_tier(config(), form.get("company", ""), form["tier"])
                    flash(f"{row['company']} is now tier {row['tier']}.", "success")
                elif "status" in form:
                    row = watchlist.set_status(config(), form.get("company", ""), form["status"])
                    flash(f"{row['company']} is now {row['status']}.", "success")
        except FreshlistError as error:
            flash(str(error), "error")
        return redirect(back)

    @app.get("/companies/import/status")
    def import_status():
        return jsonify(file_check.state())

    @app.get("/companies/import")
    def import_companies():
        cfg = config()
        if watchlist.needs_migration(cfg):
            return render_template("migrate.html", active="companies")
        results = file_check.result or []
        counts = {state: sum(row["state"] == state for row in results) for state in
                  ("good", "elsewhere", "no_jobs", "cannot_read", "failed", "not_found", "listed", "bad")}
        order = ["good", "elsewhere", "no_jobs", "cannot_read", "not_found", "failed", "listed", "bad"]
        rows = sorted(enumerate(results), key=lambda pair: order.index(pair[1]["state"]))
        return render_template("import.html", active="companies", check=file_check.state(), rows=rows, counts=counts,
                               places=", ".join(cfg["preferred_locations"]))

    @app.post("/companies/import")
    def start_import():
        cfg = config()
        if watchlist.needs_migration(cfg):
            return redirect(url_for("import_companies"))
        upload, links = request.files.get("file"), request.form.get("links", "").strip()
        try:
            if upload and upload.filename:
                plain = watchlist.parse_plain(read_upload(upload))
            elif links:
                plain = watchlist.parse_links(links)
            else:
                raise ValidationError("Choose a CSV file or paste some links first.")
            prepared = watchlist.prepare(cfg, plain)
        except FreshlistError as error:
            flash(str(error), "error")
            return redirect(url_for("import_companies"))
        if not file_check.start(lambda progress: discovery.check_file(cfg, prepared, fetch or fetch_board,
                                                                      progress=progress)):
            flash("A file is already being checked.", "warning")
        return redirect(url_for("import_companies"))

    @app.post("/companies/import/add")
    def add_imported():
        results = file_check.result or []
        picked = {value for value in request.form.getlist("keep") if value.isdigit()}
        wanted = [row for index, row in enumerate(results) if str(index) in picked and row.get("careers_url")]
        if not wanted:
            flash("Tick at least one company to add.", "warning")
            return redirect(url_for("import_companies"))
        with write_lock:
            done = watchlist.add_rows(config(), wanted)
        file_check.clear()
        flash(f"Added {len(done['added'])} {'company' if len(done['added']) == 1 else 'companies'} to your list."
              + (f" Skipped {len(done['skipped'])} already there." if done["skipped"] else "")
              + " Check for new jobs to filter their jobs against your profile.", "success")
        return redirect(url_for("companies"))

    @app.post("/companies/import/clear")
    def clear_import():
        file_check.clear()
        return redirect(url_for("import_companies"))

    @app.post("/companies/migrate")
    def migrate_companies():
        with write_lock:
            result = watchlist.migrate(config())
        flash(f"Converted {result['migrated']} companies. The old file is kept as {result['backup']}.", "success")
        return redirect(url_for("companies"))

    return app

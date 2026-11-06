"""The web page, on fake data only. Nothing here reaches the internet."""
import contextlib
import csv
import io
import json
import socket
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jobfinder.cli import main as cli_main
from jobfinder.core import agenda, discovery, learning, runner, today as today_module, tracker, watchlist
from jobfinder.errors import ValidationError
from jobfinder import autostart
from jobfinder.shortcut import create as create_shortcut
from jobfinder.config import load_config
from jobfinder.models import Job
from jobfinder.sources import SourceError
from jobfinder.web import create_app, safe_link

LEVER = "https://jobs.lever.co/example-analytics"


class CompaniesPageTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.cfg = load_config(data_dir=self.folder)
        self.client = create_app(self.folder).test_client()

    def write(self, text):
        (self.folder / "companies.csv").write_text(text, encoding="utf-8")

    def page(self, url="/companies"):
        return self.client.get(url).get_data(as_text=True)

    def test_empty_list_invites_you_to_add(self):
        self.assertIn("Add the career pages", self.page())

    def test_home_goes_to_new_jobs(self):
        self.assertTrue(self.client.get("/").headers["Location"].endswith("/jobs"))

    def test_add_saves_and_says_so(self):
        response = self.client.post("/companies", data={"url": LEVER, "tier": "deep"}, follow_redirects=True)
        self.assertIn("Added Example Analytics (lever).", response.get_data(as_text=True))
        self.assertEqual(watchlist.read(self.cfg)[0]["tier"], "deep")

    def test_add_a_page_that_cannot_be_read_warns(self):
        response = self.client.post("/companies", data={"url": "https://careers.sample.example"}, follow_redirects=True)
        self.assertIn("a run will skip it", response.get_data(as_text=True))

    def test_duplicate_is_refused_and_keeps_what_you_typed(self):
        self.client.post("/companies", data={"url": LEVER})
        response = self.client.post("/companies", data={"url": LEVER, "name": "Other name"})
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Already on your watch-list", text)
        self.assertIn('value="Other name"', text)
        self.assertEqual(len(watchlist.read(self.cfg)), 1)

    def test_change_tier_and_status(self):
        self.client.post("/companies", data={"url": LEVER})
        self.client.post("/companies/update", data={"company": "Example Analytics", "tier": "deep"})
        self.client.post("/companies/update", data={"company": "Example Analytics", "status": "quiet"})
        row = watchlist.read(self.cfg)[0]
        self.assertEqual((row["tier"], row["status"]), ("deep", "quiet"))

    def test_a_bad_value_changes_nothing(self):
        self.client.post("/companies", data={"url": LEVER})
        response = self.client.post("/companies/update", data={"company": "Example Analytics", "status": "gone"},
                                    follow_redirects=True)
        self.assertIn("status must be one of", response.get_data(as_text=True))
        self.assertEqual(watchlist.read(self.cfg)[0]["status"], "active")

    def test_change_keeps_your_filter(self):
        self.client.post("/companies", data={"url": LEVER})
        response = self.client.post("/companies/update", data={"company": "Example Analytics", "tier": "deep",
                                                               "filter_status": "active"})
        self.assertTrue(response.headers["Location"].endswith("/companies?status=active"))

    def test_filter_shows_only_what_you_picked(self):
        self.client.post("/companies", data={"url": LEVER, "tier": "deep"}, follow_redirects=True)
        self.client.post("/companies", data={"url": "https://jobs.ashbyhq.com/demo-payments"}, follow_redirects=True)
        text = self.page("/companies?tier=deep")
        self.assertIn("Example Analytics", text)
        self.assertNotIn("Demo Payments", text)

    def test_older_list_offers_to_convert_and_keeps_a_backup(self):
        self.write("name,platform,board\nExample Analytics,lever,example-analytics\n")
        self.assertIn("Convert my list", self.page())
        self.client.post("/companies/migrate")
        self.assertEqual(watchlist.read(self.cfg)[0]["company"], "Example Analytics")
        self.assertEqual(len(list(self.folder.glob("companies.csv.bak-*"))), 1)

    def test_text_from_the_file_cannot_run_as_code(self):
        columns = watchlist.COLUMNS
        with open(self.folder / "companies.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, columns)
            writer.writeheader()
            writer.writerow({**{c: "" for c in columns}, "company": "<script>alert(1)</script>",
                             "careers_url": "javascript:alert(1)", "platform": "other", "tier": "watch",
                             "status": "active"})
        text = self.page()
        self.assertNotIn("<script>alert", text)
        self.assertNotIn('href="javascript', text)

    def test_only_normal_web_links_are_linked(self):
        self.assertEqual(safe_link(LEVER), LEVER)
        self.assertEqual(safe_link("javascript:alert(1)"), "")
        self.assertEqual(safe_link(""), "")

    def test_another_website_cannot_change_your_list(self):
        response = self.client.post("/companies", data={"url": LEVER}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(watchlist.read(self.cfg), [])

    def test_the_page_answers_only_on_this_computer(self):
        self.assertEqual(self.client.get("/companies", headers={"Host": "evil.example"}).status_code, 403)

    def test_a_damaged_settings_file_shows_a_readable_problem(self):
        (self.folder / "settings.json").write_text("[1, 2]", encoding="utf-8")
        response = self.client.get("/companies")
        self.assertEqual(response.status_code, 400)
        self.assertIn("should hold settings", response.get_data(as_text=True))


def fake_job(job_id, title="Associate Product Manager", board="example-analytics"):
    return Job("lever", board, board.title(), job_id, title, "Remote", f"https://jobs.lever.co/{board}/{job_id}",
               f"0-1 years of experience. SQL and user research. Reference {job_id}.",
               datetime.now(timezone.utc) - timedelta(days=1))


class NewJobsPageTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        (self.folder / "settings.json").write_text(json.dumps({"request_delay_seconds": 0}), encoding="utf-8")
        (self.folder / "profiles").mkdir()
        (self.folder / "profiles" / "pm.json").write_text(json.dumps({
            "name": "pm", "skills": ["SQL", "User Research"], "titles": ["Associate Product Manager"]}), encoding="utf-8")
        self.cfg = load_config(data_dir=self.folder)
        watchlist.add(self.cfg, LEVER)
        self.jobs = {"example-analytics": [fake_job("1")]}
        self.gate = None
        self.app = create_app(self.folder, fetch=self.fetch)
        self.client = self.app.test_client()

    def fetch(self, platform, board, name):
        if self.gate:
            self.gate.wait(5)
        found = self.jobs[board]
        if isinstance(found, Exception):
            raise found
        return found

    def run_and_wait(self):
        response = self.client.post("/jobs/run", follow_redirects=True)
        self.app.extensions["freshlist_run"].wait()
        return response

    def page(self):
        return self.client.get("/jobs").get_data(as_text=True)

    def test_before_the_first_run_it_says_so(self):
        self.assertIn("No run yet", self.page())

    def test_a_run_shows_the_new_job_with_its_apply_link(self):
        self.run_and_wait()
        text = self.page()
        self.assertIn("Associate Product Manager", text)
        self.assertIn("https://jobs.lever.co/example-analytics/1", text)
        self.assertIn("1 new", text)

    def test_a_second_run_leaves_out_what_you_have_seen(self):
        self.run_and_wait()
        self.run_and_wait()
        self.assertIn("0 new", self.page())
        self.assertEqual(len(runner.last_run(self.cfg)["matches"]), 1)

    def test_progress_is_reported_while_the_run_works(self):
        self.gate = threading.Event()
        self.client.post("/jobs/run")
        self.assertEqual(self.client.get("/jobs/status").get_json()["status"], "running")
        self.assertIn("disabled", self.page())
        self.gate.set()
        self.app.extensions["freshlist_run"].wait()
        state = self.client.get("/jobs/status").get_json()
        self.assertEqual((state["status"], state["done"], state["total"]), ("finished", 1, 1))

    def test_only_one_run_at_a_time(self):
        self.gate = threading.Event()
        self.client.post("/jobs/run")
        response = self.client.post("/jobs/run", follow_redirects=True)
        self.assertIn("already going", response.get_data(as_text=True))
        self.gate.set()
        self.app.extensions["freshlist_run"].wait()

    def test_the_company_list_cannot_change_during_a_run(self):
        self.gate = threading.Event()
        self.client.post("/jobs/run")
        response = self.client.post("/companies", data={"url": "https://jobs.ashbyhq.com/demo"}, follow_redirects=True)
        self.assertIn("Change the list when it has finished", response.get_data(as_text=True))
        self.assertEqual(len(watchlist.read(self.cfg)), 1)
        self.gate.set()
        self.app.extensions["freshlist_run"].wait()

    def test_a_company_that_fails_is_listed_and_the_others_still_show(self):
        watchlist.add(self.cfg, "https://jobs.lever.co/demo-payments")
        self.jobs["demo-payments"] = SourceError("not found")
        self.run_and_wait()
        text = self.page()
        self.assertIn("not found", text)
        self.assertIn("Associate Product Manager", text)

    def test_when_nothing_could_be_read_it_says_nothing_was_remembered(self):
        self.jobs["example-analytics"] = SourceError("no connection")
        self.run_and_wait()
        self.assertIn("nothing was remembered", self.page())
        self.assertFalse((self.folder / "seen_jobs.json").exists())

    def test_a_crash_shows_a_message_instead_of_running_forever(self):
        app = create_app(self.folder, fetch=lambda p, b, n: (_ for _ in ()).throw(ValueError("bad reply")))
        client = app.test_client()
        client.post("/jobs/run")
        app.extensions["freshlist_run"].wait()
        self.assertNotEqual(client.get("/jobs/status").get_json()["status"], "running")

    def test_no_profile_asks_for_setup(self):
        (self.folder / "profiles" / "pm.json").unlink()
        self.assertIn("python -m jobfinder setup", self.client.post("/jobs/run", follow_redirects=True).get_data(as_text=True))

    def test_older_company_list_goes_to_convert(self):
        (self.folder / "companies.csv").write_text("name,platform,board\nAcme,lever,acme\n", encoding="utf-8")
        self.assertIn("Convert my list", self.client.post("/jobs/run", follow_redirects=True).get_data(as_text=True))

    def test_add_to_tracker_saves_it_and_never_marks_it_applied(self):
        self.run_and_wait()
        job_id = runner.last_run(self.cfg)["matches"][0]["job_id"]
        response = self.client.post("/jobs/track", data={"job_id": job_id}, follow_redirects=True)
        self.assertIn("to your tracker, marked to apply", response.get_data(as_text=True))
        entry = tracker.read(self.cfg)[0]
        self.assertEqual((entry["status"], entry["applied_date"]), ("to_apply", ""))
        self.assertIn("In tracker", self.page())

    def test_adding_the_same_job_twice_is_refused(self):
        self.run_and_wait()
        job_id = runner.last_run(self.cfg)["matches"][0]["job_id"]
        self.client.post("/jobs/track", data={"job_id": job_id})
        response = self.client.post("/jobs/track", data={"job_id": job_id}, follow_redirects=True)
        self.assertIn("already in your tracker", response.get_data(as_text=True))
        self.assertEqual(len(tracker.read(self.cfg)), 1)

    def test_a_job_no_longer_listed_is_not_added(self):
        self.run_and_wait()
        response = self.client.post("/jobs/track", data={"job_id": "lever:gone:1"}, follow_redirects=True)
        self.assertIn("no longer in the latest list", response.get_data(as_text=True))
        self.assertEqual(tracker.read(self.cfg), [])

    def test_a_bad_apply_link_from_a_job_page_is_not_linked(self):
        (self.folder / "last_run.json").write_text(json.dumps({"date": date.today().isoformat(), "matches": [
            {"number": 1, "job_id": "lever:a:1", "track": "pm", "company": "<b>Acme</b>", "role": "APM",
             "posted_date": "", "apply_link": "javascript:alert(1)", "location": "", "matched_by": "", "match": 1}]}),
            encoding="utf-8")
        text = self.page()
        self.assertNotIn('href="javascript', text)
        self.assertNotIn("<b>Acme", text)


class TrackerCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.cfg = load_config(data_dir=self.folder)
        self.client = create_app(self.folder).test_client()

    def add(self, **fields):
        return tracker.add(self.cfg, **{"company": "Sample Robotics", "role": "SEO Executive", "source": "linkedin", **fields})

    def page(self, url="/tracker"):
        return self.client.get(url).get_data(as_text=True)

    def entry(self):
        return tracker.read(self.cfg)[0]

    def form(self, entry, **changes):
        """What the browser sends when you press Save: every field, changed or not."""
        return {**{field: entry[field] for field in tracker.COLUMNS if field != "id"}, **changes}


class TrackerPageTests(TrackerCase):
    def test_empty_tracker_says_how_to_start(self):
        self.assertIn("Nothing in your tracker yet", self.page())

    def test_add_by_hand(self):
        response = self.client.post("/tracker/new", data={"company": "Sample Robotics", "role": "SEO Executive",
                                                          "source": "referral", "status": "to_apply"},
                                    follow_redirects=True)
        self.assertIn("Added SEO Executive at Sample Robotics", response.get_data(as_text=True))
        self.assertEqual(self.entry()["source"], "referral")

    def test_a_missing_field_is_refused_and_keeps_what_you_typed(self):
        response = self.client.post("/tracker/new", data={"company": "Sample Robotics", "notes": "from a friend"})
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn("needs a role", text)
        self.assertIn("from a friend", text)
        self.assertEqual(tracker.read(self.cfg), [])

    def test_marking_applied_sets_the_date_and_the_follow_up(self):
        entry = self.add()
        response = self.client.post(f"/tracker/{entry['id']}/status", data={"status": "applied"}, follow_redirects=True)
        saved = self.entry()
        today = date.today()
        self.assertEqual(saved["applied_date"], today.isoformat())
        self.assertEqual(saved["followup_date"], (today + timedelta(days=7)).isoformat())
        self.assertIn("Follow up on", response.get_data(as_text=True))

    def test_saving_the_full_form_still_works_out_the_follow_up(self):
        entry = self.add()
        self.client.post(f"/tracker/{entry['id']}", data=self.form(entry, status="applied"))
        saved = self.entry()
        self.assertEqual(saved["applied_date"], date.today().isoformat())
        self.assertTrue(saved["followup_date"])

    def test_taking_back_an_application_on_the_full_form_clears_the_dates(self):
        entry = self.add(status="applied")
        self.client.post(f"/tracker/{entry['id']}", data=self.form(entry, status="to_apply"))
        saved = self.entry()
        self.assertEqual((saved["status"], saved["applied_date"], saved["followup_date"]), ("to_apply", "", ""))

    def test_a_resume_change_with_no_explanation_blocks_applied_and_takes_you_to_write_it(self):
        entry = self.add(changes_made="Moved SEO project to the top")
        response = self.client.post(f"/tracker/{entry['id']}/status", data={"status": "applied"}, follow_redirects=True)
        text = response.get_data(as_text=True)
        self.assertIn("cannot be marked applied", text)
        self.assertIn('value="applied" selected', text)
        self.assertEqual(self.entry()["status"], "to_apply")

    def test_the_same_rule_on_the_full_form_keeps_what_you_typed(self):
        entry = self.add()
        response = self.client.post(f"/tracker/{entry['id']}", data=self.form(
            entry, status="applied", changes_made="Reworded my summary"))
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Reworded my summary", text)
        self.assertEqual(self.entry()["changes_made"], "")

    def test_writing_the_explanation_lets_it_through(self):
        entry = self.add(changes_made="Moved SEO project to the top")
        self.client.post(f"/tracker/{entry['id']}", data=self.form(
            entry, status="applied", how_i_would_explain_it="I ran that project"))
        self.assertEqual(self.entry()["status"], "applied")

    def test_saving_with_no_changes_says_so(self):
        entry = self.add()
        response = self.client.post(f"/tracker/{entry['id']}", data=self.form(entry), follow_redirects=True)
        self.assertIn("Nothing changed", response.get_data(as_text=True))

    def test_filter_shows_only_that_status(self):
        self.add()
        self.add(company="Demo Payments", status="applied")
        text = self.page("/tracker?status=applied")
        self.assertIn("Demo Payments", text)
        self.assertNotIn("Sample Robotics", text)

    def test_follow_ups_due_are_shown(self):
        self.add(applied_date="2026-01-01", status="applied")
        self.assertIn("Follow-ups due", self.page())

    def test_a_mistyped_date_in_the_file_does_not_break_the_page(self):
        entry = self.add(status="applied")
        rows = tracker.read(self.cfg)
        rows[0]["followup_date"] = "next week"
        tracker._save(self.cfg, rows)
        self.assertEqual(self.client.get("/tracker").status_code, 200)
        self.assertEqual(self.client.get(f"/tracker/{entry['id']}").status_code, 200)

    def test_an_unknown_entry_shows_a_readable_problem(self):
        self.assertIn("No tracker entry", self.page("/tracker/t-missing"))

    def test_export_downloads_a_real_excel_file(self):
        self.add()
        response = self.client.post("/tracker/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("tracker.xlsx", response.headers["Content-Disposition"])
        self.assertEqual(response.get_data()[:2], b"PK")
        response.close()

    def test_text_and_links_from_the_file_cannot_run_as_code(self):
        self.add(company="<script>alert(1)</script>", apply_link="javascript:alert(1)")
        text = self.page()
        self.assertNotIn("<script>alert", text)
        self.assertNotIn('href="javascript', text)

    def test_another_website_cannot_change_your_tracker(self):
        entry = self.add()
        response = self.client.post(f"/tracker/{entry['id']}/status", data={"status": "applied"},
                                    headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.entry()["status"], "to_apply")


class TodayPageTests(TrackerCase):
    def fake_matches(self, when=None):
        (self.folder / "last_run.json").write_text(json.dumps({"date": (when or date.today()).isoformat(), "matches": [
            {"number": 1, "job_id": "lever:a:1", "track": "pm", "company": "Example Analytics", "role": "Product Analyst",
             "posted_date": "2026-01-10", "apply_link": "https://jobs.lever.co/a/1", "location": "Remote",
             "matched_by": "", "match": 1}]}), encoding="utf-8")

    def test_it_shows_the_day_from_the_date(self):
        expected = "Day " + today_module.which_day(date.today())
        self.assertIn(expected, self.page("/today"))

    def test_you_can_choose_the_other_day(self):
        self.assertIn("Reach out and widen", self.page("/today?day=B"))
        self.assertIn("Find and apply", self.page("/today?day=a"))

    def test_it_only_says_you_chose_the_day_when_that_differs_from_the_date(self):
        automatic = today_module.which_day(date.today())
        other = "B" if automatic == "A" else "A"
        self.assertNotIn("you chose this day", self.page(f"/today?day={automatic}"))
        self.assertIn("you chose this day", self.page(f"/today?day={other}"))

    def test_a_bad_day_falls_back_to_the_date(self):
        self.assertEqual(self.client.get("/today?day=Z").status_code, 200)

    def test_opening_today_never_starts_a_run_or_writes_a_file(self):
        before = sorted(path.name for path in self.folder.iterdir())
        self.page("/today?day=A")
        self.page("/today?day=B")
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), before)

    def test_day_a_shows_todays_matches_and_can_add_one(self):
        self.fake_matches()
        self.assertIn("Product Analyst", self.page("/today?day=A"))
        response = self.client.post("/jobs/track", data={"job_id": "lever:a:1", "back": "today"})
        self.assertTrue(response.headers["Location"].endswith("/today"))
        self.assertIn("In tracker", self.page("/today?day=A"))

    def test_day_a_leaves_out_matches_from_an_earlier_day(self):
        self.fake_matches(date.today() - timedelta(days=1))
        self.assertNotIn("Product Analyst", self.page("/today?day=A"))

    def test_day_a_lists_jobs_still_to_apply(self):
        self.add()
        self.assertIn("SEO Executive", self.page("/today?day=A"))

    def test_day_b_lists_outreach_and_follow_ups(self):
        self.add(status="applied", founder_x_handle="@fake_founder")
        self.add(company="Demo Payments", applied_date="2026-01-01", status="applied", outreach_status="sent")
        text = self.page("/today?day=B")
        self.assertIn("@fake_founder", text)
        self.assertIn("Demo Payments", text)

    def test_a_mistyped_date_does_not_break_day_b(self):
        self.add(status="applied")
        rows = tracker.read(self.cfg)
        rows[0]["applied_date"] = "last week"
        tracker._save(self.cfg, rows)
        self.assertEqual(self.client.get("/today?day=B").status_code, 200)

    def test_a_status_change_only_mentions_follow_up_while_applied(self):
        entry = self.add(status="applied")
        response = self.client.post(f"/tracker/{entry['id']}/status", data={"status": "interview"}, follow_redirects=True)
        self.assertNotIn("Follow up on", response.get_data(as_text=True))


class ReviewPageTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.cfg = load_config(data_dir=self.folder)
        self.client = create_app(self.folder).test_client()

    def page(self):
        return self.client.get("/review").get_data(as_text=True)

    def log(self, first, matches=()):
        (self.folder / "run_log.json").write_text(json.dumps({
            "first_live_run": first.isoformat(), "matches": list(matches),
            "runs": [{"date": first.isoformat(), "failed": [{"company": "Demo Payments", "error": "not found"}]}]}),
            encoding="utf-8")

    def test_before_any_run_it_says_the_checkpoint_has_not_started(self):
        watchlist.add(self.cfg, LEVER)
        self.assertIn("has not started", self.page())

    def test_an_empty_list_invites_you_to_add(self):
        self.assertIn("No companies on your list", self.page())

    def test_it_counts_matches_per_company(self):
        watchlist.add(self.cfg, LEVER)
        today = date.today()
        self.log(today, [{"date": today.isoformat(), "company": "Example Analytics", "job_id": "x", "role": "APM"}])
        self.assertRegex(self.page(), r'class="number">1<')

    def test_a_quiet_company_is_flagged_and_only_suggested(self):
        watchlist.add(self.cfg, LEVER, today=date(2026, 1, 1))
        self.log(date.today() - timedelta(days=20))
        text = self.page()
        self.assertIn("Consider swapping it", text)
        self.assertEqual(watchlist.read(self.cfg)[0]["status"], "active")

    def test_too_few_matches_warns_at_the_checkpoint(self):
        watchlist.add(self.cfg, LEVER)
        self.log(date.today() - timedelta(days=20))
        self.assertIn("Consider widening", self.page())

    def test_a_company_that_failed_last_run_is_listed(self):
        watchlist.add(self.cfg, LEVER)
        self.log(date.today())
        self.assertIn("not found", self.page())

    def test_review_changes_no_files(self):
        watchlist.add(self.cfg, LEVER)
        self.log(date.today())
        before = {path.name: path.read_bytes() for path in self.folder.iterdir()}
        self.page()
        self.assertEqual({path.name: path.read_bytes() for path in self.folder.iterdir()}, before)

    def test_older_company_list_offers_to_convert(self):
        (self.folder / "companies.csv").write_text("name,platform,board\nAcme,lever,acme\n", encoding="utf-8")
        self.assertIn("Convert my list", self.page())


def fake_listed_job(board, place):
    return Job("lever", board, board, "1", "Associate Product Manager", place, f"https://jobs.lever.co/{board}/1", "Text.",
               datetime.now(timezone.utc))


class ParseFileTests(unittest.TestCase):
    def test_a_header_with_other_spellings_is_understood(self):
        rows = watchlist.parse_plain("Company Name,Careers Page\nExample Analytics,https://jobs.lever.co/example\n")
        self.assertEqual((rows[0]["company"], rows[0]["careers_url"]), ("Example Analytics", "https://jobs.lever.co/example"))

    def test_platform_and_board_columns_become_a_link(self):
        rows = watchlist.parse_plain("name,platform,board\nDemo,Ashby,demo-payments\n")
        self.assertEqual(rows[0]["careers_url"], "https://jobs.ashbyhq.com/demo-payments")

    def test_one_column_with_no_header_can_mix_names_and_links(self):
        rows = watchlist.parse_plain("Sample Robotics\nhttps://jobs.lever.co/example\n")
        self.assertEqual((rows[0]["company"], rows[1]["careers_url"]), ("Sample Robotics", "https://jobs.lever.co/example"))

    def test_unknown_columns_are_named_in_the_message(self):
        with self.assertRaisesRegex(ValidationError, "Alpha, Beta"):
            watchlist.parse_plain("Alpha,Beta\n1,2\n")

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(ValidationError):
            watchlist.parse_plain("# only a comment\n")

    def test_a_huge_file_is_refused(self):
        lines = "company\n" + "".join(f"Company {n}\n" for n in range(watchlist.MAX_COMPANIES_IN_FILE + 1))
        with self.assertRaisesRegex(ValidationError, "at most"):
            watchlist.parse_plain(lines)

    def test_pasted_links_are_found_inside_other_text(self):
        rows = watchlist.parse_links("see https://jobs.lever.co/example/123 and boards.greenhouse.io/sample")
        self.assertEqual(len(rows), 2)

    def test_pasted_text_with_no_links_is_refused(self):
        with self.assertRaises(ValidationError):
            watchlist.parse_links("no links here")


class CheckFileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cfg = load_config(data_dir=folder.name)
        self.cfg.update(preferred_locations=["Jaipur"], request_delay_seconds=0)

    def fetch(self, platform, board, name):
        boards = {"in-city": [fake_listed_job(board, "Jaipur")], "abroad": [fake_listed_job(board, "Berlin")], "empty": []}
        if board not in boards:
            raise SourceError("not found")
        return boards[board]

    def check(self, text):
        prepared = watchlist.prepare(self.cfg, watchlist.parse_plain(text))
        return discovery.check_file(self.cfg, prepared, self.fetch, sleep=lambda seconds: None)

    def test_each_company_gets_the_right_state_and_only_good_ones_are_ticked(self):
        results = self.check("company,careers_url\n"
                             "A,https://jobs.lever.co/in-city\nB,https://jobs.lever.co/abroad\n"
                             "C,https://jobs.lever.co/empty\nD,https://jobs.lever.co/gone\n"
                             "E,https://careers.sample.example\n")
        self.assertEqual([r["state"] for r in results], ["good", "elsewhere", "no_jobs", "failed", "cannot_read"])
        self.assertEqual([r["keep"] for r in results], [True, False, False, False, False])

    def test_a_name_alone_is_searched_for_but_never_ticked_for_you(self):
        result = self.check("company\nIn-City\n")[0]
        self.assertEqual((result["state"], result["guessed"], result["keep"]), ("good", True, False))
        self.assertIn("right company", result["note"])

    def test_a_name_with_no_page_says_so(self):
        self.assertEqual(self.check("company\nNobody Here\n")[0]["state"], "not_found")

    def test_a_company_already_listed_is_not_contacted(self):
        watchlist.add(self.cfg, "https://jobs.lever.co/in-city", "A")
        calls = []
        prepared = watchlist.prepare(self.cfg, watchlist.parse_plain("careers_url\nhttps://jobs.lever.co/in-city\n"))
        result = discovery.check_file(self.cfg, prepared, lambda *a: calls.append(a) or [], sleep=lambda seconds: None)[0]
        self.assertEqual((result["state"], calls), ("listed", []))

    def test_a_repeat_inside_the_file_is_caught(self):
        results = self.check("careers_url\nhttps://jobs.lever.co/in-city\nhttps://jobs.lever.co/in-city/123\n")
        self.assertEqual([r["state"] for r in results], ["good", "listed"])

    def test_a_crash_on_one_company_does_not_stop_the_others(self):
        def fetch(platform, board, name):
            if board == "broken":
                raise ValueError("bad reply")
            return [fake_listed_job(board, "Jaipur")]
        prepared = watchlist.prepare(self.cfg, watchlist.parse_plain(
            "careers_url\nhttps://jobs.lever.co/broken\nhttps://jobs.lever.co/fine\n"))
        results = discovery.check_file(self.cfg, prepared, fetch, sleep=lambda seconds: None)
        self.assertEqual([r["state"] for r in results], ["failed", "good"])


class ImportPageTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        (self.folder / "settings.json").write_text(json.dumps({"request_delay_seconds": 0, "preferred_locations": ["Jaipur"]}),
                                                   encoding="utf-8")
        self.cfg = load_config(data_dir=self.folder)
        self.app = create_app(self.folder, fetch=self.fetch)
        self.client = self.app.test_client()

    def fetch(self, platform, board, name):
        return {"in-city": [fake_listed_job(board, "Jaipur")], "abroad": [fake_listed_job(board, "Berlin")]}[board]

    def upload(self, text, name="companies.csv"):
        data = {"file": (io.BytesIO(text if isinstance(text, bytes) else text.encode("utf-8")), name)}
        response = self.client.post("/companies/import", data=data, content_type="multipart/form-data", follow_redirects=True)
        self.app.extensions["freshlist_file_check"].wait()
        return response

    def page(self):
        return self.client.get("/companies/import").get_data(as_text=True)

    TWO = "company,careers_url\nGood Co,https://jobs.lever.co/in-city\nFar Co,https://jobs.lever.co/abroad\n"

    def test_nothing_is_added_until_you_confirm(self):
        self.upload(self.TWO)
        self.assertIn("Good Co", self.page())
        self.assertEqual(watchlist.read(self.cfg), [])

    def test_only_a_good_company_starts_ticked(self):
        self.upload(self.TWO)
        self.assertEqual(self.page().count("checked"), 1)

    def test_only_what_you_tick_is_added(self):
        self.upload(self.TWO)
        results = self.app.extensions["freshlist_file_check"].result
        wanted = next(i for i, row in enumerate(results) if row["company"] == "Far Co")
        response = self.client.post("/companies/import/add", data={"keep": [str(wanted)]}, follow_redirects=True)
        self.assertIn("Added 1 company", response.get_data(as_text=True))
        self.assertEqual([row["company"] for row in watchlist.read(self.cfg)], ["Far Co"])

    def test_ticking_nothing_adds_nothing(self):
        self.upload(self.TWO)
        response = self.client.post("/companies/import/add", data={}, follow_redirects=True)
        self.assertIn("Tick at least one", response.get_data(as_text=True))
        self.assertEqual(watchlist.read(self.cfg), [])

    def test_a_file_saved_by_excel_is_read(self):
        self.upload("company,careers_url\r\nCafé Co,https://jobs.lever.co/in-city\r\n".encode("cp1252"))
        self.assertIn("Café Co", self.page())
        self.upload(b"\xef\xbb\xbfcompany,careers_url\nBom Co,https://jobs.lever.co/in-city\n")
        self.assertIn("Bom Co", self.page())

    def test_a_spreadsheet_file_asks_for_a_csv(self):
        response = self.upload(b"PK", name="companies.xlsx")
        self.assertIn("save the spreadsheet as a CSV", response.get_data(as_text=True))

    def test_a_file_over_the_limit_is_refused(self):
        response = self.upload(b"x" * (2 * 1024 * 1024 + 1))
        self.assertEqual(response.status_code, 413)
        self.assertIn("up to 2 MB", response.get_data(as_text=True))

    def test_pasted_links_go_through_the_same_check(self):
        self.client.post("/companies/import", data={"links": "https://jobs.lever.co/in-city/1"})
        self.app.extensions["freshlist_file_check"].wait()
        self.assertIn("In City", self.page())

    def test_choosing_nothing_says_so(self):
        response = self.client.post("/companies/import", data={}, follow_redirects=True)
        self.assertIn("Choose a CSV file", response.get_data(as_text=True))

    def test_text_in_a_file_cannot_run_as_code(self):
        self.upload("company,careers_url\n<script>alert(1)</script>,https://jobs.lever.co/in-city\n")
        self.assertNotIn("<script>alert", self.page())

    def test_another_website_cannot_upload(self):
        response = self.client.post("/companies/import", data={"links": "https://jobs.lever.co/in-city"},
                                    headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_older_company_list_offers_to_convert(self):
        (self.folder / "companies.csv").write_text("name,platform,board\nAcme,lever,acme\n", encoding="utf-8")
        self.assertIn("Convert my list", self.page())


class OptionalSourceBoxTests(NewJobsPageTests):
    def test_a_ticked_source_is_used_for_that_run_only(self):
        self.jobs.update({})
        app = create_app(self.folder, fetch=lambda platform, board, name: [])
        client = app.test_client()
        client.post("/jobs/run", data={"starter": "on"})
        app.extensions["freshlist_run"].wait(30)
        self.assertIn("starter companies", app.extensions["freshlist_run"].result.sources)
        self.assertFalse(load_config(data_dir=self.folder)["use_starter_companies"])

    def test_with_nothing_ticked_only_your_list_is_read(self):
        self.run_and_wait()
        self.assertEqual(self.app.extensions["freshlist_run"].result.sources, ["watch-list"])

    def test_an_empty_list_points_you_to_companies(self):
        (self.folder / "companies.csv").unlink()
        response = self.client.post("/jobs/run", follow_redirects=True)
        self.assertIn("on the Companies page", response.get_data(as_text=True))


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_mac_icon_runs_this_copy_and_can_be_double_clicked(self):
        path = create_shortcut(str(self.folder), "/my data", python="/my python/bin/python", system="darwin")
        text = path.read_text()
        self.assertEqual(path.name, "Freshlist.command")
        self.assertTrue(path.stat().st_mode & 0o100)
        self.assertIn("'/my python/bin/python' -m jobfinder --data-dir '/my data' ui", text)

    def test_windows_icon(self):
        path = create_shortcut(str(self.folder), "C:/data", python="C:/py.exe", system="win32")
        self.assertEqual(path.name, "Freshlist.bat")
        self.assertIn('-m jobfinder --data-dir', path.read_text())

    def test_a_port_used_by_another_program_gives_a_readable_message(self):
        with socket.socket() as other:
            other.bind(("127.0.0.1", 0))
            other.listen()
            port = other.getsockname()[1]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli_main(["ui", "--no-open", "--port", str(port)]), 1)
        self.assertIn("already in use", out.getvalue())

    def test_a_missing_folder_gives_a_readable_message(self):
        with self.assertRaises(ValidationError):
            create_shortcut(str(self.folder / "nowhere"))


class LearnPageTests(TrackerCase):
    def page(self, url="/learn"):
        return self.client.get(url).get_data(as_text=True)

    def fake_match(self, missing="Python, Tableau"):
        (self.folder / "last_run.json").write_text(json.dumps({"date": date.today().isoformat(), "matches": [
            {"number": 1, "job_id": "lever:a:1", "track": "pm", "company": "Example Analytics", "role": "Product Analyst",
             "posted_date": "2026-01-10", "apply_link": "https://jobs.lever.co/a/1", "location": "Remote",
             "matched_by": "", "match": 1, "details": {"Missing skills": missing}}]}), encoding="utf-8")

    def test_empty_says_how_to_start(self):
        self.assertIn("Add a job to your tracker", self.page())

    def test_adding_a_job_from_a_run_lists_the_skills_it_names(self):
        self.fake_match()
        response = self.client.post("/jobs/track", data={"job_id": "lever:a:1"}, follow_redirects=True)
        self.assertIn("2 skills it names", response.get_data(as_text=True))
        self.assertEqual([(i["topic"], i["source"]) for i in learning.read(self.cfg)], [("Python", "job"), ("Tableau", "job")])
        text = self.page()
        self.assertIn("Tableau", text)
        self.assertIn("from the job", text)

    def test_a_job_naming_no_new_skills_adds_nothing(self):
        self.fake_match(missing="")
        self.client.post("/jobs/track", data={"job_id": "lever:a:1"})
        self.assertEqual(learning.read(self.cfg), [])

    def test_you_can_add_your_own_item_with_a_date(self):
        entry = self.add()
        response = self.client.post(f"/learn/{entry['id']}/items", data={"topic": "Explain my SEO project", "learn_by": "2026-12-01"})
        self.assertTrue(response.headers["Location"].endswith(f"#job-{entry['id']}"))
        item = learning.read(self.cfg)[0]
        self.assertEqual((item["topic"], item["learn_by"], item["source"], item["status"]),
                         ("Explain my SEO project", "2026-12-01", "you", "to_learn"))

    def test_an_empty_or_repeated_item_is_refused(self):
        entry = self.add()
        self.assertIn("Write what you need", self.client.post(f"/learn/{entry['id']}/items", data={"topic": " "},
                                                              follow_redirects=True).get_data(as_text=True))
        self.client.post(f"/learn/{entry['id']}/items", data={"topic": "SQL"})
        self.assertIn("already on the list", self.client.post(f"/learn/{entry['id']}/items", data={"topic": "sql"},
                                                              follow_redirects=True).get_data(as_text=True))
        self.assertEqual(len(learning.read(self.cfg)), 1)

    def test_tick_untick_and_remove(self):
        entry = self.add()
        item = learning.add(self.cfg, entry["id"], "SQL")
        self.client.post(f"/learn/items/{item['id']}/done", data={"done": "on"})
        self.assertEqual(learning.read(self.cfg)[0]["status"], "done")
        self.client.post(f"/learn/items/{item['id']}/done", data={})
        self.assertEqual(learning.read(self.cfg)[0]["status"], "to_learn")
        self.client.post(f"/learn/items/{item['id']}/remove")
        self.assertEqual(learning.read(self.cfg), [])

    def test_a_learn_by_date_can_be_set_and_cleared(self):
        entry = self.add()
        item = learning.add(self.cfg, entry["id"], "SQL")
        self.client.post(f"/learn/items/{item['id']}/date", data={"learn_by": "2026-12-01"})
        self.assertEqual(learning.read(self.cfg)[0]["learn_by"], "2026-12-01")
        self.client.post(f"/learn/items/{item['id']}/date", data={"learn_by": ""})
        self.assertEqual(learning.read(self.cfg)[0]["learn_by"], "")

    def test_dates_save_to_the_tracker_and_status_stays_as_it_was(self):
        entry = self.add()
        self.client.post(f"/learn/{entry['id']}/dates", data={"apply_deadline": "2026-12-10", "interview_date": "2026-12-20"})
        saved = self.entry()
        self.assertEqual((saved["apply_deadline"], saved["interview_date"], saved["status"]), ("2026-12-10", "2026-12-20", "to_apply"))

    def test_a_bad_date_is_refused(self):
        entry = self.add()
        response = self.client.post(f"/learn/{entry['id']}/dates", data={"apply_deadline": "soon"}, follow_redirects=True)
        self.assertIn("YYYY-MM-DD", response.get_data(as_text=True))
        self.assertEqual(self.entry()["apply_deadline"], "")

    def test_what_you_changed_on_your_resume_is_shown_with_the_job(self):
        self.add(changes_made="Added a Tableau dashboard project")
        self.assertIn("Added a Tableau dashboard project", self.page())

    def test_a_deadline_appears_in_its_month_and_not_in_another(self):
        self.add(apply_deadline="2026-12-10")
        self.assertIn("Apply by", self.page("/learn?month=2026-12"))
        self.assertIn("December 2026", self.page("/learn?month=2026-12"))
        self.assertNotIn("Apply by:", self.page("/learn?month=2027-01"))

    def test_a_bad_month_falls_back_to_this_month(self):
        self.assertIn(date.today().strftime("%B %Y"), self.page("/learn?month=nonsense"))
        self.assertEqual(self.client.get("/learn?month=2026-13").status_code, 200)

    def test_a_deadline_drops_off_once_you_have_applied(self):
        self.add(apply_deadline="2026-12-10", status="applied")
        self.assertEqual([e for e in agenda.events(self.cfg) if e["kind"] == "deadline"], [])

    def test_a_finished_job_is_hidden_until_you_ask(self):
        self.add(status="closed", company="Demo Payments")
        self.assertNotIn("Demo Payments", self.page())
        self.assertIn("Demo Payments", self.page("/learn?show=all"))

    def test_the_soonest_job_comes_first(self):
        self.add(company="Later Co", interview_date=(date.today() + timedelta(days=9)).isoformat())
        self.add(company="Sooner Co", apply_deadline=(date.today() + timedelta(days=2)).isoformat())
        self.add(company="No Date Co")
        self.assertEqual([job["company"] for job in agenda.jobs_to_prepare(self.cfg)], ["Sooner Co", "Later Co", "No Date Co"])

    def test_a_missed_deadline_and_a_soon_one_show_on_today(self):
        self.add(company="Missed Co", apply_deadline=(date.today() - timedelta(days=1)).isoformat())
        self.add(company="Soon Co", interview_date=(date.today() + timedelta(days=2)).isoformat())
        text = self.client.get("/today").get_data(as_text=True)
        self.assertIn("Deadline passed", text)
        self.assertIn("Soon Co", text)

    def test_a_mistyped_date_in_a_file_does_not_break_the_page(self):
        entry = self.add()
        rows = tracker.read(self.cfg)
        rows[0]["apply_deadline"] = "next week"
        tracker._save(self.cfg, rows)
        learning.add(self.cfg, entry["id"], "SQL")
        items = learning.read(self.cfg)
        items[0]["learn_by"] = "someday"
        learning._save(self.cfg, items)
        self.assertEqual(self.client.get("/learn").status_code, 200)
        self.assertEqual(self.client.get("/today").status_code, 200)

    def test_text_cannot_run_as_code(self):
        entry = self.add()
        learning.add(self.cfg, entry["id"], "<script>alert(1)</script>", "2026-12-01")
        self.assertNotIn("<script>alert", self.page("/learn?month=2026-12"))

    def test_an_older_tracker_file_without_the_new_columns_still_works(self):
        (self.folder / "tracker.csv").write_text(
            "id,company,role,source,status\nt-old,Old Co,Analyst,linkedin,to_apply\n", encoding="utf-8")
        self.assertIn("Old Co", self.page())

    def test_another_website_cannot_change_your_list(self):
        entry = self.add()
        response = self.client.post(f"/learn/{entry['id']}/items", data={"topic": "SQL"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(learning.read(self.cfg), [])

    def test_a_skill_you_add_to_your_resume_goes_to_learn(self):
        entry = self.add()
        response = self.client.post(f"/tracker/{entry['id']}", data=self.form(
            entry, changes_made="Added a Tableau dashboard project and Python scripts"), follow_redirects=True)
        self.assertIn("2 new things to learn", response.get_data(as_text=True))
        self.assertEqual([(i["topic"], i["source"]) for i in learning.read(self.cfg)], [("Python", "resume"), ("Tableau", "resume")])
        self.assertIn("added to your resume", self.page())

    def test_a_skill_already_in_your_profile_is_not_listed(self):
        (self.folder / "profiles").mkdir()
        (self.folder / "profiles" / "pm.json").write_text(json.dumps({"name": "pm", "skills": ["SQL"], "titles": []}), encoding="utf-8")
        self.add(changes_made="Moved my SQL project to the top and added Tableau")
        self.assertEqual([i["topic"] for i in learning.read(self.cfg)], ["Tableau"])

    def test_a_change_naming_no_skill_becomes_one_thing_to_explain(self):
        self.add(changes_made="Reworded my summary")
        self.assertEqual([i["topic"] for i in learning.read(self.cfg)], ["Be ready to explain: Reworded my summary"])

    def test_editing_the_change_adds_only_what_is_new(self):
        entry = self.add(changes_made="Added Tableau")
        tracker.update(self.cfg, entry["id"], changes_made="Added Tableau and Python")
        tracker.update(self.cfg, entry["id"], notes="nothing about the resume")
        self.assertEqual(sorted(i["topic"] for i in learning.read(self.cfg)), ["Python", "Tableau"])

    def test_adding_by_hand_with_a_change_lists_it_too(self):
        response = self.client.post("/tracker/new", data={"company": "Sample Robotics", "role": "SEO Executive",
                                                          "source": "referral", "status": "to_apply",
                                                          "changes_made": "Added Google Search Console"}, follow_redirects=True)
        self.assertIn("1 new thing to learn", response.get_data(as_text=True))

    def test_each_item_points_to_the_interview_or_the_deadline(self):
        soon = (date.today() + timedelta(days=5)).isoformat()
        later = (date.today() + timedelta(days=9)).isoformat()
        entry = self.add(apply_deadline=soon, changes_made="Added Tableau")
        text = self.page()
        self.assertIn(f"Before the deadline to apply: {soon}", text)
        tracker.update(self.cfg, entry["id"], interview_date=later)
        self.assertIn(f"Before the interview: {later}", self.page())

    def test_an_items_own_date_comes_before_the_jobs_date(self):
        entry = self.add(apply_deadline=(date.today() + timedelta(days=5)).isoformat())
        learning.add(self.cfg, entry["id"], "SQL", "2026-12-01")
        self.assertIn("Learn by: 2026-12-01", self.page())

    def test_personal_learning_file_is_ignored_by_git(self):
        ignored = (Path(__file__).resolve().parent.parent / ".gitignore").read_text()
        self.assertIn("learning.csv", ignored.splitlines())


class AutostartTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.calls = []

    def run_command(self, command, **kwargs):
        self.calls.append(command)

        class Done:
            returncode, stderr = 0, ""
        return Done()

    def test_the_agent_runs_this_copy_without_opening_a_browser(self):
        settings = autostart.agent("/my data", 8765, python="/venv/bin/python")
        self.assertEqual(settings["ProgramArguments"],
                         ["/venv/bin/python", "-m", "jobfinder", "--data-dir", "/my data", "ui", "--no-open", "--port", "8765"])
        self.assertTrue(settings["RunAtLoad"])
        self.assertEqual(settings["KeepAlive"], {"SuccessfulExit": False})

    def test_turning_on_writes_the_agent_and_starts_it(self):
        path = autostart.turn_on("/my data", folder=str(self.folder), run=self.run_command, system="darwin")
        self.assertEqual(path.name, "local.freshlist.ui.plist")
        import plistlib
        self.assertEqual(plistlib.loads(path.read_bytes())["Label"], "local.freshlist.ui")
        self.assertEqual([call[1] for call in self.calls], ["bootout", "bootstrap"])

    def test_turning_off_stops_and_removes_it(self):
        autostart.turn_on("/my data", folder=str(self.folder), run=self.run_command, system="darwin")
        self.assertTrue(autostart.turn_off(folder=str(self.folder), run=self.run_command, system="darwin"))
        self.assertFalse(autostart.agent_path(str(self.folder)).exists())
        self.assertFalse(autostart.turn_off(folder=str(self.folder), run=self.run_command, system="darwin"))

    def test_a_refusal_from_macos_is_readable(self):
        def refuse(command, **kwargs):
            class Done:
                returncode, stderr = 5, "Input/output error"
            return Done()
        with self.assertRaisesRegex(ValidationError, "Input/output error"):
            autostart.turn_on(folder=str(self.folder), run=refuse, system="darwin")

    def test_other_systems_are_pointed_to_the_desktop_icon(self):
        with self.assertRaisesRegex(ValidationError, "shortcut"):
            autostart.turn_on(folder=str(self.folder), run=self.run_command, system="linux")
        self.assertEqual(self.calls, [])

    def test_pages_carry_an_icon_for_a_pinned_tab(self):
        with tempfile.TemporaryDirectory() as data:
            client = create_app(data).test_client()
            self.assertIn('rel="mask-icon"', client.get("/companies").get_data(as_text=True))
            self.assertEqual(client.get("/static/icon.svg").status_code, 200)


if __name__ == "__main__":
    unittest.main()

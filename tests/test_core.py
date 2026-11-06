"""Offline tests of the core library. All data below is fake, and nothing here uses the internet."""
import ast
import csv
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobfinder import storage
from jobfinder.config import load_config
from jobfinder.core import review, runner, today as today_module, tracker, watchlist
from jobfinder.errors import NotFoundError, StorageError, ValidationError
from jobfinder.models import Job
from jobfinder.sources import SourceError, greenhouse
from jobfinder.vocab import Vocab

NOW = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 4)
VOCAB = Vocab.load()
PROFILE = {"name": "pm", "skills": ["SQL", "User Research", "Roadmapping"], "titles": ["Associate Product Manager"]}


def job(job_id, title="Associate Product Manager", days=1, board="acme", company="Acme"):
    return Job("lever", board, company, job_id, title, "Remote", f"https://jobs.lever.co/{board}/{job_id}",
               f"0-1 years of experience. SQL and user research. Reference {job_id}.", NOW - timedelta(days=days))


class CoreCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.cfg = load_config(data_dir=self.dir)
        self.cfg["request_delay_seconds"] = 0

    def add_companies(self, *boards):
        for board in boards:
            watchlist.add(self.cfg, f"https://jobs.lever.co/{board}", board.title(), today=TODAY)

    def run_on(self, jobs, now=NOW, today=TODAY, **kwargs):
        def fetch(platform, board, name):
            found = jobs[board]
            if isinstance(found, Exception):
                raise found
            return found
        return runner.run(self.cfg, [PROFILE], VOCAB, fetch=fetch, now=now, today=today,
                          sleep=lambda seconds: None, **kwargs)


class WatchlistTests(CoreCase):
    def test_platform_is_detected_from_the_link(self):
        for url, expected in (("https://boards.greenhouse.io/acme/jobs/1", ("greenhouse", "acme")),
                              ("https://job-boards.greenhouse.io/acme", ("greenhouse", "acme")),
                              ("https://jobs.lever.co/acme/abc", ("lever", "acme")),
                              ("https://jobs.ashbyhq.com/acme", ("ashby", "acme")),
                              ("https://acme.example/careers", ("other", "")),
                              ("https://jobs.eu.lever.co/acme", ("other", ""))):
            self.assertEqual(watchlist.detect_platform(url), expected, url)

    def test_add_list_set_tier_and_set_status(self):
        row = watchlist.add(self.cfg, "https://jobs.lever.co/acme-labs", today=TODAY)
        self.assertEqual((row["company"], row["platform"], row["tier"], row["status"], row["added_date"]),
                         ("Acme Labs", "lever", "watch", "active", "2026-11-04"))
        with self.assertRaises(ValidationError):
            watchlist.add(self.cfg, "https://jobs.lever.co/acme-labs/123")     # the same company
        watchlist.set_tier(self.cfg, "acme labs", "deep")
        watchlist.set_status(self.cfg, "Acme Labs", "quiet")
        self.assertEqual(len(watchlist.list_companies(self.cfg, tier="deep", status="quiet")), 1)
        with self.assertRaises(ValidationError):
            watchlist.set_tier(self.cfg, "Acme Labs", "gold")
        with self.assertRaises(NotFoundError):
            watchlist.set_status(self.cfg, "Nobody", "active")

    def test_bulk_import_takes_plain_lists_and_reports_what_it_skips(self):
        plain = self.dir / "plain.csv"
        plain.write_text("name,platform,board\nAcme,lever,acme\nBeta,greenhouse,beta\nAcme again,lever,acme\n")
        result = watchlist.import_csv(self.cfg, str(plain), today=TODAY)
        self.assertEqual([r["company"] for r in result["added"]], ["Acme", "Beta"])
        self.assertEqual(len(result["skipped"]), 1)
        (self.dir / "bad.csv").write_text("hello,world\n1,2\n")
        with self.assertRaises(ValidationError):
            watchlist.import_csv(self.cfg, str(self.dir / "bad.csv"))

    def test_the_starter_list_can_be_imported(self):
        from jobfinder.config import STARTER_COMPANIES
        result = watchlist.import_csv(self.cfg, str(STARTER_COMPANIES), today=TODAY)
        self.assertGreater(len(result["added"]), 100)
        self.assertEqual(result["skipped"], [])
        self.assertTrue(all(watchlist.board_of(row) for row in result["added"]))

    def test_migrate_converts_the_old_file_and_keeps_a_backup(self):
        old = "# comment\nname,platform,board,email,x_handle\nAcme,lever,acme,hi@acme.example,@acme\nBeta,ashby,beta,,\n"
        path = self.dir / "companies.csv"
        path.write_text(old)
        with self.assertRaises(StorageError):       # an old file is explained, not misread
            watchlist.read(self.cfg)
        result = watchlist.migrate(self.cfg, today=TODAY)
        self.assertEqual(result["migrated"], 2)
        self.assertEqual(Path(result["backup"]).read_text(), old)
        rows = watchlist.read(self.cfg)
        self.assertEqual(rows[0]["careers_url"], "https://jobs.lever.co/acme")
        self.assertEqual(watchlist.board_of(rows[1]), "beta")
        self.assertIn("hi@acme.example", rows[0]["notes"])
        with self.assertRaises(ValidationError):    # a second run changes nothing
            watchlist.migrate(self.cfg, today=TODAY)

    def test_only_active_readable_companies_are_fetched(self):
        self.add_companies("acme", "beta")
        watchlist.set_status(self.cfg, "Beta", "quiet")
        watchlist.add(self.cfg, "https://careers.example.com", "Custom", today=TODAY)
        companies, skipped = watchlist.for_run(watchlist.read(self.cfg))
        self.assertEqual([c["board"] for c in companies], ["acme"])
        self.assertEqual([s["company"] for s in skipped], ["Custom"])


class NewJobMemoryTests(CoreCase):
    def test_a_job_is_shown_once_and_a_new_job_still_appears(self):
        self.add_companies("acme")
        first = self.run_on({"acme": [job("1")]})
        self.assertEqual([m["job_id"] for m in first.new_matches], ["lever:acme:1"])
        second = self.run_on({"acme": [job("1")]}, now=NOW + timedelta(days=1), today=TODAY + timedelta(days=1))
        self.assertEqual((second.new_matches, second.already_shown), ([], 1))
        third = self.run_on({"acme": [job("1"), job("2")]}, now=NOW + timedelta(days=2), today=TODAY + timedelta(days=2))
        self.assertEqual([m["job_id"] for m in third.new_matches], ["lever:acme:2"])
        self.assertEqual(third.new_matches[0]["number"], 1)
        memory = storage.read_json(self.dir / "seen_jobs.json")
        self.assertEqual(memory["shown"]["lever:acme:1"], "2026-11-04")    # keeps its first_seen date

    def test_a_job_that_did_not_match_is_not_remembered_as_shown(self):
        self.add_companies("acme")
        self.run_on({"acme": [job("1", "Office Chef")]})
        self.assertEqual(storage.read_json(self.dir / "seen_jobs.json")["shown"], {})

    def test_numbers_hold_for_a_second_run_on_the_same_day(self):
        self.add_companies("acme")
        self.run_on({"acme": [job("1")]})
        again = self.run_on({"acme": [job("1"), job("2")]})
        self.assertEqual(again.new_matches[0]["number"], 2)
        self.assertEqual([m["number"] for m in runner.last_run(self.cfg)["matches"]], [1, 2])
        with open(again.csv_path, encoding="utf-8-sig") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 2)    # the day's file keeps the earlier match

    def test_when_nothing_can_be_read_nothing_is_remembered(self):
        self.add_companies("acme")
        result = self.run_on({"acme": SourceError("HTTP 500")})
        self.assertFalse(result.saved)
        self.assertFalse((self.dir / "seen_jobs.json").exists())
        self.assertEqual(review.review(self.cfg, TODAY)["checkpoint"]["state"], "not_started")


class FreshnessTests(CoreCase):
    def test_the_seven_day_rule(self):
        self.add_companies("acme")
        result = self.run_on({"acme": [job("in", days=6.9), job("out", days=7.1)]})
        self.assertEqual([m["job_id"] for m in result.new_matches], ["lever:acme:in"])
        self.assertEqual(result.stats["stale_dropped"], 1)

    def test_the_rule_comes_from_the_config(self):
        self.cfg["freshness_days"] = 2
        self.add_companies("acme")
        self.assertEqual(self.run_on({"acme": [job("old", days=3)]}).new_matches, [])

    def test_greenhouse_uses_the_first_published_date_not_the_last_edit(self):
        posted = (NOW - timedelta(days=30)).isoformat()
        edited = (NOW - timedelta(days=1)).isoformat()
        payload = {"jobs": [{"id": 1, "title": "x", "first_published": posted, "updated_at": edited}]}
        self.assertEqual(greenhouse.fetch("a", "A", lambda url: payload)[0].posted_at.date(), (NOW - timedelta(days=30)).date())
        payload = {"jobs": [{"id": 1, "title": "x", "updated_at": edited}]}
        self.assertEqual(greenhouse.fetch("a", "A", lambda url: payload)[0].posted_at.date(), (NOW - timedelta(days=1)).date())


class RunTests(CoreCase):
    def test_a_failed_company_is_returned_and_the_rest_carry_on(self):
        self.add_companies("acme", "beta")
        steps = []
        result = self.run_on({"acme": [job("1")], "beta": SourceError("not found")}, progress=steps.append)
        self.assertEqual(len(result.new_matches), 1)
        self.assertEqual([(f["company"], f["error"]) for f in result.failed], [("Beta", "not found")])
        self.assertEqual(sorted(s["company"] for s in steps), ["Acme", "Beta"])
        self.assertEqual(max(s["done"] for s in steps), 2)
        self.assertEqual({s["total"] for s in steps}, {2})

    def test_a_match_has_what_you_need_and_updates_last_match_date(self):
        self.add_companies("acme", "beta")
        match = self.run_on({"acme": [job("1")], "beta": []}).new_matches[0]
        self.assertEqual((match["company"], match["role"], match["posted_date"], match["apply_link"]),
                         ("Acme", "Associate Product Manager", "2026-11-03", "https://jobs.lever.co/acme/1"))
        rows = {row["company"]: row["last_match_date"] for row in watchlist.read(self.cfg)}
        self.assertEqual(rows, {"Acme": "2026-11-04", "Beta": ""})

    def test_optional_sources_are_off_by_default(self):
        for key in ("use_starter_companies", "use_remote_boards", "use_alert_emails", "use_yc_discovery"):
            self.assertFalse(self.cfg[key], key)
        self.add_companies("acme")
        self.cfg["preferred_locations"] = ["Remote"]
        asked = []

        def fetch(platform, board, name):
            asked.append((platform, board))
            return []
        result = runner.run(self.cfg, [PROFILE], VOCAB, fetch=fetch, now=NOW, today=TODAY, sleep=lambda s: None)
        self.assertEqual(asked, [("lever", "acme")])
        self.assertEqual(result.sources, ["watch-list"])

    def test_turning_on_the_starter_list_adds_it(self):
        self.cfg["use_starter_companies"] = True
        self.add_companies("acme")
        result = runner.run(self.cfg, [PROFILE], VOCAB, fetch=lambda p, b, n: [], now=NOW, today=TODAY, sleep=lambda s: None)
        self.assertIn("starter companies", result.sources)
        self.assertGreater(result.companies_read, 100)

    def test_yc_discovery_refuses_while_it_is_off(self):
        from jobfinder.core import discovery
        from jobfinder.errors import ConfigError
        with self.assertRaises(ConfigError):
            discovery.find_yc(self.cfg, get_json=lambda *a, **k: self.fail("must not fetch"))

    def test_an_empty_watch_list_is_explained(self):
        with self.assertRaises(ValidationError) as caught:
            runner.run(self.cfg, [PROFILE], VOCAB, fetch=lambda p, b, n: [], now=NOW, today=TODAY)
        self.assertIn("companies add", str(caught.exception))

    def test_demo_uses_fake_jobs_and_remembers_nothing(self):
        result = runner.demo(self.cfg, [PROFILE], VOCAB, now=NOW)
        self.assertTrue(all("(fake)" in row["Company"] for row in result.rows))
        self.assertFalse((self.dir / "seen_jobs.json").exists())


def entry_fields(**more):
    return {"company": "Acme", "role": "Analyst", "source": "linkedin", **more}


class TrackerTests(CoreCase):
    def test_applied_is_refused_when_a_change_has_no_explanation(self):
        entry = tracker.add(self.cfg, today=TODAY, changes_made="Moved SQL to the top", **entry_fields())
        with self.assertRaises(ValidationError) as caught:
            tracker.update(self.cfg, entry["id"], today=TODAY, status="applied")
        self.assertIn("how you would explain", str(caught.exception))
        self.assertEqual(tracker.read(self.cfg)[0]["status"], "to_apply")     # nothing was saved
        with self.assertRaises(ValidationError):                              # a blank explanation is still empty
            tracker.update(self.cfg, entry["id"], today=TODAY, status="applied", how_i_would_explain_it="   ")
        with self.assertRaises(ValidationError):                              # a date also means applied
            tracker.update(self.cfg, entry["id"], today=TODAY, applied_date="2026-11-01")
        done = tracker.update(self.cfg, entry["id"], today=TODAY, status="applied",
                              how_i_would_explain_it="I used SQL daily on my last project")
        self.assertEqual(done["status"], "applied")

    def test_the_rule_also_holds_when_adding_and_when_changing_later(self):
        with self.assertRaises(ValidationError):
            tracker.add(self.cfg, today=TODAY, status="applied", changes_made="Reworded summary", **entry_fields())
        entry = tracker.add(self.cfg, today=TODAY, status="applied", **entry_fields())     # no changes: fine
        with self.assertRaises(ValidationError):
            tracker.update(self.cfg, entry["id"], today=TODAY, changes_made="Reworded summary")

    def test_follow_up_is_applied_date_plus_seven_days(self):
        entry = tracker.add(self.cfg, today=TODAY, status="applied", **entry_fields())
        self.assertEqual((entry["applied_date"], entry["followup_date"]), ("2026-11-04", "2026-11-11"))
        moved = tracker.update(self.cfg, entry["id"], today=TODAY, applied_date="2026-11-28")
        self.assertEqual(moved["followup_date"], "2026-12-05")        # across a month end
        own = tracker.update(self.cfg, entry["id"], today=TODAY, followup_date="2026-12-01")
        self.assertEqual(own["followup_date"], "2026-12-01")          # your own date stays
        back = tracker.update(self.cfg, entry["id"], today=TODAY, status="to_apply")
        self.assertEqual((back["applied_date"], back["followup_date"]), ("", ""))

    def test_the_seven_comes_from_the_config(self):
        self.cfg["followup_days"] = 3
        self.assertEqual(tracker.add(self.cfg, today=TODAY, status="applied", **entry_fields())["followup_date"], "2026-11-07")

    def test_ids_stay_the_same_when_rows_are_edited_or_sorted(self):
        first = tracker.add(self.cfg, today=TODAY, **entry_fields(role="One"))
        second = tracker.add(self.cfg, today=TODAY, **entry_fields(role="Two"))
        self.assertNotEqual(first["id"], second["id"])
        columns, rows = storage.read_csv(self.dir / "tracker.csv")
        storage.write_csv(self.dir / "tracker.csv", columns, list(reversed(rows)))
        tracker.update(self.cfg, first["id"], today=TODAY, notes="edited")
        self.assertEqual({r["id"]: r["role"] for r in tracker.read(self.cfg)}, {first["id"]: "One", second["id"]: "Two"})

    def test_values_are_checked(self):
        for bad in ({"source": "friend"}, {"resume_version": "v9"}, {"status": "maybe"}, {"outreach_status": "soon"},
                    {"applied_date": "4 Nov"}, {"company": ""}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                tracker.add(self.cfg, today=TODAY, **{**entry_fields(), **bad})
        with self.assertRaises(ValidationError):
            tracker.add(self.cfg, today=TODAY, id="t-mine", **entry_fields())
        with self.assertRaises(ValidationError):
            tracker.add(self.cfg, today=TODAY, unknown="x", **entry_fields())

    def test_adding_from_the_last_run_by_number(self):
        self.add_companies("acme")
        self.run_on({"acme": [job("1")]})
        entry = tracker.add_from_run(self.cfg, 1, today=TODAY)
        self.assertEqual((entry["company"], entry["source"], entry["resume_version"], entry["apply_link"], entry["posted_date"]),
                         ("Acme", "freshlist", "pm", "https://jobs.lever.co/acme/1", "2026-11-03"))
        with self.assertRaises(ValidationError):       # the same job twice
            tracker.add_from_run(self.cfg, 1, today=TODAY)
        with self.assertRaises(NotFoundError):
            tracker.add_from_run(self.cfg, 9, today=TODAY)

    def test_list_filters_and_due(self):
        tracker.add(self.cfg, today=TODAY, **entry_fields(role="Waiting"))
        old = tracker.add(self.cfg, today=TODAY, status="applied", applied_date="2026-10-20", **entry_fields(role="Old"))
        tracker.add(self.cfg, today=TODAY, status="applied", **entry_fields(role="Recent"))
        tracker.add(self.cfg, today=TODAY, status="rejected", applied_date="2026-10-20", **entry_fields(role="No"))
        self.assertEqual([r["role"] for r in tracker.list_entries(self.cfg, "to_apply")], ["Waiting"])
        due = tracker.due(self.cfg, TODAY)
        self.assertEqual([(r["id"], r["days_overdue"]) for r in due], [(old["id"], 8)])

    def test_a_hand_edited_file_that_breaks_the_rule_is_reported(self):
        tracker.add(self.cfg, today=TODAY, **entry_fields())
        columns, rows = storage.read_csv(self.dir / "tracker.csv")
        rows[0].update(status="applied", applied_date="2026-11-01", changes_made="Edited by hand")
        storage.write_csv(self.dir / "tracker.csv", columns, rows)
        self.assertEqual(len(tracker.problems(self.cfg)), 1)

    def test_export_to_xlsx_never_turns_text_into_a_formula(self):
        from openpyxl import load_workbook
        tracker.add(self.cfg, today=TODAY, **entry_fields(role='=HYPERLINK("http://bad.example","x")'))
        path = tracker.export_xlsx(self.cfg, str(self.dir / "out.xlsx"))
        sheet = load_workbook(path).active
        self.assertEqual([cell.value for cell in sheet[1]], tracker.COLUMNS)
        self.assertEqual(sheet.cell(row=2, column=3).data_type, "s")


class TodayTests(CoreCase):
    def test_days_alternate_with_no_repeat_across_a_month_end(self):
        days = [today_module.which_day(date(2026, 10, 29) + timedelta(days=n)) for n in range(6)]
        self.assertEqual(sorted(set(days)), ["A", "B"])
        self.assertTrue(all(a != b for a, b in zip(days, days[1:])))

    def test_the_day_can_be_overridden(self):
        natural = today_module.which_day(TODAY)
        other = "B" if natural == "A" else "A"
        plan = today_module.checklist(self.cfg, TODAY, override=other.lower())
        self.assertEqual((plan["day"], plan["chosen_by"]), (other, "override"))
        with self.assertRaises(ValidationError):
            today_module.checklist(self.cfg, TODAY, override="C")

    def test_day_a_shows_todays_new_matches(self):
        self.add_companies("acme")
        self.assertFalse(today_module.checklist(self.cfg, TODAY, "A")["ran_today"])
        self.run_on({"acme": [job("1")]})
        plan = today_module.checklist(self.cfg, TODAY, "A")
        self.assertTrue(plan["ran_today"] and plan["steps"][0]["done"])
        self.assertEqual(plan["new_matches"][0]["number"], 1)
        self.assertFalse(plan["new_matches"][0]["in_tracker"])
        tracker.add_from_run(self.cfg, 1, today=TODAY)
        self.assertTrue(today_module.checklist(self.cfg, TODAY, "A")["new_matches"][0]["in_tracker"])
        self.assertEqual(today_module.checklist(self.cfg, TODAY + timedelta(days=2), "A")["new_matches"], [])

    def test_day_b_lists_outreach_followups_and_the_capped_scan(self):
        tracker.add(self.cfg, today=TODAY, status="applied", applied_date="2026-11-03", **entry_fields(role="Recent"))
        tracker.add(self.cfg, today=TODAY, status="applied", applied_date="2026-11-03", outreach_status="sent", **entry_fields(role="Sent"))
        tracker.add(self.cfg, today=TODAY, status="applied", applied_date="2026-10-20", **entry_fields(role="Old"))
        plan = today_module.checklist(self.cfg, TODAY, "B")
        self.assertEqual([r["role"] for r in plan["outreach_pending"]], ["Recent"])
        self.assertEqual([r["role"] for r in plan["followups_due"]], ["Old"])
        text = " ".join(step["text"] for step in plan["steps"])
        self.assertIn("30 minutes", text)
        self.assertIn("LinkedIn", text)


class ReviewTests(CoreCase):
    def test_a_company_is_flagged_quiet_after_14_days_and_nothing_changes(self):
        self.add_companies("acme", "beta")
        self.run_on({"acme": [job("1")], "beta": []})
        before = (self.dir / "companies.csv").read_text(encoding="utf-8-sig")
        at_13 = review.review(self.cfg, TODAY + timedelta(days=13))
        self.assertEqual(at_13["quiet"], [])
        at_14 = review.review(self.cfg, TODAY + timedelta(days=14))
        self.assertEqual(sorted(c["company"] for c in at_14["quiet"]), ["Acme", "Beta"])
        self.assertIn("Consider swapping", at_14["quiet"][0]["suggestion"])
        self.assertEqual((self.dir / "companies.csv").read_text(encoding="utf-8-sig"), before)

    def test_a_recent_match_keeps_a_company_off_the_quiet_list(self):
        self.add_companies("acme", "beta")
        self.run_on({"acme": [], "beta": []})
        later = TODAY + timedelta(days=14)
        self.run_on({"acme": [job("1", days=-13)], "beta": []}, now=NOW + timedelta(days=14), today=later)
        self.assertEqual([c["company"] for c in review.review(self.cfg, later)["quiet"]], ["Beta"])
        counts = {c["company"]: c["matches_total"] for c in review.review(self.cfg, later)["companies"]}
        self.assertEqual(counts, {"Acme": 1, "Beta": 0})

    def test_nothing_is_flagged_before_the_first_live_run(self):
        watchlist.add(self.cfg, "https://jobs.lever.co/acme", "Acme", today=TODAY - timedelta(days=60))
        self.assertEqual(review.review(self.cfg, TODAY)["quiet"], [])

    def test_only_active_companies_are_flagged(self):
        self.add_companies("acme", "beta")
        self.run_on({"acme": [], "beta": []})
        watchlist.set_status(self.cfg, "Beta", "swapped")
        self.assertEqual([c["company"] for c in review.review(self.cfg, TODAY + timedelta(days=20))["quiet"]], ["Acme"])

    def test_checkpoint_warns_when_too_few_matches_in_the_first_14_days(self):
        self.add_companies("acme")
        self.run_on({"acme": [job("1"), job("2", "Associate Product Manager, Growth")]})
        self.assertEqual(review.review(self.cfg, TODAY + timedelta(days=13))["checkpoint"]["state"], "running")
        checkpoint = review.review(self.cfg, TODAY + timedelta(days=14))["checkpoint"]
        self.assertTrue(checkpoint["warning"])
        self.assertEqual(checkpoint["found"], 2)
        self.assertIn("widening", checkpoint["message"])

    def test_checkpoint_passes_and_its_numbers_come_from_the_config(self):
        self.add_companies("acme")
        self.run_on({"acme": [job("1"), job("2", "Associate Product Manager, Growth")]})
        self.cfg["checkpoint_min_matches"] = 2
        self.assertEqual(review.review(self.cfg, TODAY)["checkpoint"]["state"], "passed")
        self.cfg["checkpoint_min_matches"], self.cfg["checkpoint_days"] = 5, 30
        self.assertEqual(review.review(self.cfg, TODAY + timedelta(days=20))["checkpoint"]["state"], "running")


class StorageTests(CoreCase):
    def test_a_failed_write_leaves_the_old_file_whole(self):
        path = self.dir / "tracker.csv"
        storage.write_csv(path, ["id"], [{"id": "keep"}])
        with self.assertRaises(TypeError):
            storage.write_text(path, 123)     # fails part way through writing
        self.assertEqual(storage.read_csv(path)[1], [{"id": "keep"}])
        self.assertEqual([f.name for f in self.dir.iterdir()], ["tracker.csv"])    # no temporary file left

    def test_a_damaged_memory_file_is_reported_not_overwritten(self):
        (self.dir / "seen_jobs.json").write_text("{not json")
        self.add_companies("acme")
        with self.assertRaises(StorageError):
            self.run_on({"acme": []})
        self.assertEqual((self.dir / "seen_jobs.json").read_text(), "{not json")

    def test_columns_added_by_hand_are_kept(self):
        tracker.add(self.cfg, today=TODAY, **entry_fields())
        columns, rows = storage.read_csv(self.dir / "tracker.csv")
        rows[0]["my_column"] = "mine"
        storage.write_csv(self.dir / "tracker.csv", columns + ["my_column"], rows)
        tracker.add(self.cfg, today=TODAY, **entry_fields(role="Second"))
        self.assertEqual(tracker.read(self.cfg)[0]["my_column"], "mine")


class LayerTests(unittest.TestCase):
    def test_the_core_never_prints_asks_or_exits(self):
        package = Path(__file__).resolve().parent.parent / "jobfinder"
        terminal = {"cli.py", "setup.py", "__main__.py"}
        for path in package.rglob("*.py"):
            if path.name in terminal:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("print", "input", "exit", "quit"):
                    self.fail(f"{path.name}:{node.lineno} calls {node.func.id}")
                if isinstance(node, ast.Name) and node.id == "SystemExit":
                    self.fail(f"{path.name}:{node.lineno} uses SystemExit")
                if isinstance(node, ast.Attribute) and node.attr == "exit" and getattr(node.value, "id", "") == "sys":
                    self.fail(f"{path.name}:{node.lineno} calls sys.exit")


if __name__ == "__main__":
    unittest.main()

"""Offline tests. All job data below is fake, shaped like the documented platform responses."""
import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from jobfinder import filters
from jobfinder.setup import run_setup
from jobfinder.models import Job
from jobfinder.cli import load_profiles
from jobfinder.pipeline import (ProfileError, clean_list, prepare_profile, profile_files, read_companies, run_pipeline, write_csv,
                                COLUMNS)
from jobfinder.sources import SourceError, fetch_board
from jobfinder.textutil import norm, parse_dt, strip_html
from jobfinder.vocab import Vocab

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
VOCAB = Vocab.load()


def iso(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


GREENHOUSE = {"jobs": [
    {"id": 1, "title": "Associate Product Manager", "updated_at": iso(2),
     "location": {"name": "Hyderabad"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
     "content": "&lt;p&gt;0-1 years of experience. You will use SQL, Agile and user research. Stipend: Rs. 25,000 per month&lt;/p&gt;"},
    {"id": 2, "title": "Associate Product Manager", "updated_at": iso(12),
     "location": {"name": "Hyderabad"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
     "content": "&lt;p&gt;Fresh grads welcome.&lt;/p&gt;"},
    {"id": 3, "title": "Senior Product Manager", "updated_at": iso(1),
     "location": {"name": "Bengaluru"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/3",
     "content": "&lt;p&gt;6+ years of experience&lt;/p&gt;"},
    {"id": 4, "title": "Product Analyst", "updated_at": iso(1),
     "location": {"name": "Bengaluru"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/4",
     "content": "&lt;p&gt;3-5 years of experience with SQL and metrics.&lt;/p&gt;"},
    {"id": 5, "title": "Founder's Office Associate", "updated_at": iso(3),
     "location": {"name": "Remote"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/5",
     "content": "&lt;p&gt;Work with the founder on strategy and operations.&lt;/p&gt;"},
    {"id": 6, "title": "Office Chef", "updated_at": iso(1),
     "location": {"name": "Hyderabad"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/6", "content": ""},
]}

LEVER = [
    {"id": "abc", "text": "SEO Specialist", "hostedUrl": "https://jobs.lever.co/beta/abc",
     "applyUrl": "https://jobs.lever.co/beta/abc/apply", "createdAt": int((NOW - timedelta(days=1)).timestamp() * 1000),
     "categories": {"location": "Hyderabad", "commitment": "Full-time"}, "workplaceType": "onsite",
     "descriptionPlain": "Freshers can apply. Keyword research, SEO, Ahrefs. Shift 10 am to 6 pm.",
     "lists": [{"text": "Requirements", "content": "<li>Google Search Console</li>"}],
     "additionalPlain": "", "salaryRange": {"min": 300000, "max": 500000, "currency": "INR", "interval": "per-year-salary"}},
    {"id": "nodate", "text": "Content Writer", "hostedUrl": "https://jobs.lever.co/beta/nodate",
     "categories": {"location": "Pune"}, "descriptionPlain": "Writing blog posts. 1 year of experience."},
]

ASHBY = {"jobs": [
    {"id": "x1", "title": "Business Operations Associate", "location": "Hyderabad", "isRemote": False,
     "publishedAt": iso(4), "jobUrl": "https://jobs.ashbyhq.com/gamma/x1", "isListed": True,
     "descriptionHtml": "<p>Entry level. Excel, project management, data analysis.</p>",
     "compensation": {"scrapeableCompensationSalarySummary": "INR 6L - 9L"}},
    {"id": "x2", "title": "Business Operations Associate", "location": "Remote", "isRemote": True,
     "publishedAt": iso(1), "jobUrl": "https://jobs.ashbyhq.com/gamma/x2", "isListed": False,
     "descriptionHtml": "<p>Hidden listing.</p>"},
]}

PAYLOADS = {
    "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true": GREENHOUSE,
    "https://api.lever.co/v0/postings/beta?mode=json": LEVER,
    "https://api.ashbyhq.com/posting-api/job-board/gamma?includeCompensation=true": ASHBY,
}


def fake_get_json(url):
    if url not in PAYLOADS:
        raise SourceError("not found (check the board name in companies.csv)")
    return PAYLOADS[url]


def fake_fetch(platform, board, name):
    return fetch_board(platform, board, name, get_json=fake_get_json)


COMPANIES = [
    {"name": "Acme", "platform": "greenhouse", "board": "acme", "email": "careers@acme.test", "x_handle": "@acmefounder"},
    {"name": "Beta", "platform": "lever", "board": "beta", "email": "", "x_handle": ""},
    {"name": "Gamma", "platform": "ashby", "board": "gamma", "email": "", "x_handle": ""},
    {"name": "Broken", "platform": "lever", "board": "doesnotexist", "email": "", "x_handle": ""},
]

PM = {"name": "pm", "skills": ["Product Management", "Agile", "User Research", "SQL", "Metrics", "Strategy", "Operations"],
      "titles": ["Associate Product Manager", "Product Analyst", "Founder's Office", "Strategy and Operations Associate"]}
SEO = {"name": "seo", "skills": ["SEO", "Keyword Research", "Ahrefs", "Google Search Console", "Writing"],
       "titles": ["SEO Executive", "Content Writer"]}


class TextTests(unittest.TestCase):
    def test_strip_html_handles_escaped_markup(self):
        self.assertEqual(strip_html("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;"), "Hello & welcome")

    def test_norm_drops_apostrophes(self):
        self.assertEqual(norm("Founder's Office"), "founders office")

    def test_parse_dt_formats(self):
        self.assertEqual(parse_dt("2026-10-21T12:00:00Z"), NOW)
        self.assertEqual(parse_dt(int(NOW.timestamp() * 1000)), NOW)
        self.assertEqual(parse_dt("2026-10-21T12:00:00.12Z"), NOW.replace(microsecond=120000))
        self.assertIsNone(parse_dt("garbage"))
        self.assertIsNone(parse_dt(None))


class FilterTests(unittest.TestCase):
    def test_experience_cases(self):
        cases = [
            ("0-1 years of experience", 0),
            ("1 to 3 years experience", 1),
            ("3+ years of experience in product", 3),
            ("Freshers welcome", 0),
            ("We were founded 10 years ago and love tea", None),
            ("Nothing about it", None),
            ("Minimum 5 yrs experience", 5),
            ("0 - 1 yr", 0),
        ]
        for text, expected in cases:
            self.assertEqual(filters.experience_requirement(text)[0], expected, text)

    def test_buckets(self):
        self.assertEqual(filters.experience_bucket("0-1 years", 1)[0], filters.WITHIN)
        self.assertEqual(filters.experience_bucket("2+ years of experience", 1)[0], filters.TOO_SENIOR)
        self.assertEqual(filters.experience_bucket("no numbers here", 1)[0], filters.NOT_STATED)

    def test_title_and_excluded_words(self):
        phrases = VOCAB.title_phrases("Founder's Office")
        self.assertTrue(filters.title_matches("Founders' Office Associate", phrases))
        self.assertTrue(filters.title_matches("Associate - Founder's Office", phrases))
        self.assertFalse(filters.title_matches("Office Chef", phrases))
        self.assertTrue(filters.has_excluded_word("Senior Product Manager", ["senior"]))
        self.assertFalse(filters.has_excluded_word("Associate Product Manager", ["senior", "lead"]))

    def test_shift_pay_location(self):
        self.assertTrue(filters.shift_mentioned("Shift 10 am to 6 pm"))
        self.assertTrue(filters.shift_mentioned("10-6 shift"))
        self.assertFalse(filters.shift_mentioned("9 to 5"))
        self.assertIn("25,000", filters.pay_from_text("Stipend: Rs. 25,000 per month"))
        self.assertEqual(filters.pay_from_text("Great team"), "")
        self.assertTrue(filters.location_matches("Hyderabad, Telangana", ["hyderabad"]))
        self.assertFalse(filters.location_matches("Pune", ["hyderabad"]))


class VocabTests(unittest.TestCase):
    def test_extract_and_suggest(self):
        text = "Led agile sprints, ran user research, wrote SQL and Python. SEO and keyword research on Ahrefs."
        skills = VOCAB.extract_skills(text)
        for expected in ["Agile", "User Research", "SQL", "Python", "SEO", "Keyword Research", "Ahrefs"]:
            self.assertIn(expected, skills)
        self.assertNotIn("Git/GitHub", skills)
        titles = VOCAB.suggest_titles(skills)
        self.assertIn("SEO Executive", titles)

    def test_longer_skill_phrase_is_not_counted_twice(self):
        skills = VOCAB.extract_skills("Keyword research and content strategy for our blog.")
        self.assertIn("Keyword Research", skills)
        self.assertIn("Content Strategy", skills)
        self.assertNotIn("Research", skills)        # only inside 'keyword research'
        self.assertNotIn("Strategy", skills)        # only inside 'content strategy'
        # Said on its own as well, it still counts.
        both = VOCAB.extract_skills("Keyword research. We value research and strategy.")
        self.assertIn("Research", both)
        self.assertIn("Strategy", both)
        # So two buried words no longer suggest an unrelated role.
        self.assertNotIn("Founder's Office", VOCAB.suggest_titles(VOCAB.extract_skills(
            "SEO, keyword research, Ahrefs, Semrush, content strategy, GEO, blog writing.")))

    def test_git_does_not_match_github_or_digit(self):
        self.assertEqual(VOCAB.extract_skills("digital gift"), [])
        self.assertEqual(VOCAB.extract_skills("github"), ["Git/GitHub"])


class SourceTests(unittest.TestCase):
    def test_greenhouse_normalizes(self):
        jobs = fetch_board("greenhouse", "acme", "Acme", get_json=fake_get_json)
        self.assertEqual(len(jobs), 6)
        self.assertEqual(jobs[0].url, "https://boards.greenhouse.io/acme/jobs/1")
        self.assertIn("0-1 years", jobs[0].description)
        self.assertNotIn("<", jobs[0].description)

    def test_lever_dates_and_apply_link(self):
        jobs = fetch_board("lever", "beta", "Beta", get_json=fake_get_json)
        self.assertEqual(jobs[0].url, "https://jobs.lever.co/beta/abc")
        self.assertIsNotNone(jobs[0].posted_at)
        self.assertIsNone(jobs[1].posted_at)
        self.assertIn("INR", jobs[0].pay_text)

    def test_ashby_skips_unlisted(self):
        jobs = fetch_board("ashby", "gamma", "Gamma", get_json=fake_get_json)
        self.assertEqual([j.job_id for j in jobs], ["x1"])
        self.assertEqual(jobs[0].pay_text, "INR 6L - 9L")

    def test_unknown_platform_and_bad_board(self):
        with self.assertRaises(SourceError):
            fetch_board("workday", "x", "X", get_json=fake_get_json)
        with self.assertRaises(SourceError):
            fetch_board("lever", "nope", "Nope", get_json=fake_get_json)


class PipelineTests(unittest.TestCase):
    def run_once(self, state=None, now=NOW, settings=None, profiles=None):
        state = state if state is not None else {"first_seen": {}, "dropped": {}}
        rows, stats, failures = run_pipeline(COMPANIES, profiles or [PM, SEO], settings or {}, VOCAB,
                                             state, fake_fetch, now=now)
        return rows, stats, failures, state

    def test_end_to_end(self):
        rows, stats, failures, state = self.run_once()
        roles = {(r["Company"], r["Role"]) for r in rows}
        self.assertIn(("Acme", "Associate Product Manager"), roles)       # fresh, 0-1 years
        self.assertIn(("Acme", "Founder's Office Associate"), roles)      # experience not stated
        self.assertIn(("Beta", "SEO Specialist"), roles)
        self.assertIn(("Beta", "Content Writer"), roles)                  # no date, first seen
        self.assertIn(("Gamma", "Business Operations Associate"), roles)
        self.assertNotIn(("Acme", "Senior Product Manager"), roles)
        self.assertNotIn(("Acme", "Product Analyst"), roles)              # 3-5 years
        self.assertNotIn(("Acme", "Office Chef"), roles)                  # not a wanted title
        self.assertEqual(stats["stale_dropped"], 1)                       # job 2, 12 days old
        self.assertEqual(stats["boards_failed"], 1)
        self.assertEqual(len(failures), 1)

    def test_every_row_has_apply_link_and_columns(self):
        rows, _, _, _ = self.run_once()
        for row in rows:
            self.assertTrue(row["Apply link"].startswith("https://"))
            self.assertEqual(set(row), set(COLUMNS))

    def test_stated_experience_ranks_above_not_stated(self):
        rows, _, _, _ = self.run_once()
        for track in ("pm", "seo"):                                     # ranking applies inside each track
            buckets = [r["Experience bucket"] for r in rows if r["Track"] == track]
            stated = [b == "Within your range" for b in buckets]
            self.assertEqual(stated, sorted(stated, reverse=True), track)

    def test_track_choice_and_reasons(self):
        rows, _, _, _ = self.run_once()
        seo = next(r for r in rows if r["Role"] == "SEO Specialist")
        self.assertEqual(seo["Track"], "seo")
        self.assertIn("Ahrefs", seo["Matched skills"])
        self.assertEqual(seo["Shift 10-6 mentioned"], "yes")
        self.assertEqual(seo["Location match"], "yes")
        apm = next(r for r in rows if r["Role"] == "Associate Product Manager")
        self.assertEqual(apm["Track"], "pm")
        self.assertEqual(apm["Company email"], "careers@acme.test")
        self.assertEqual(apm["Founder X handle"], "@acmefounder")
        self.assertIn("25,000", apm["Pay"])

    def test_pay_flag_when_missing(self):
        rows, _, _, _ = self.run_once()
        founders = next(r for r in rows if r["Role"] == "Founder's Office Associate")
        self.assertEqual(founders["Pay"], "Not stated")

    def test_stale_jobs_stay_gone_and_state_remembers(self):
        rows, stats, _, state = self.run_once()
        self.assertIn("greenhouse:acme:2", state["dropped"])
        # Run again the next day with the same feed: the dropped job must not come back as new.
        rows2, stats2, _, state2 = self.run_once(state=state, now=NOW + timedelta(days=1))
        self.assertEqual(stats2["already_dropped"], 1)
        self.assertNotIn("2", [r["Apply link"].rsplit("/", 1)[-1] for r in rows2 if r["Company"] == "Acme"])

    def test_undated_job_goes_stale_after_a_week(self):
        _, _, _, state = self.run_once()
        later = NOW + timedelta(days=8)
        rows, stats, _, state = self.run_once(state=state, now=later)
        self.assertNotIn("Content Writer", [r["Role"] for r in rows])
        self.assertIn("lever:beta:nodate", state["dropped"])

    def test_location_require_mode(self):
        rows, _, _, _ = self.run_once(settings={"location_mode": "require"})
        self.assertTrue(all(r["Location match"] == "yes" for r in rows))
        self.assertNotIn("Content Writer", [r["Role"] for r in rows])      # Pune

    def test_csv_and_companies_reader(self):
        rows, _, _, _ = self.run_once()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out", "jobs.csv")
            write_csv(rows, out)
            with open(out, encoding="utf-8-sig") as handle:
                read_back = list(csv.DictReader(handle))
            self.assertEqual(len(read_back), len(rows))
            self.assertEqual(list(read_back[0].keys()), COLUMNS)
            companies_file = os.path.join(tmp, "companies.csv")
            with open(companies_file, "w", encoding="utf-8") as handle:
                handle.write("# comment\nname,platform,board,email,x_handle\nAcme,greenhouse,acme,,\n")
            self.assertEqual(read_companies(companies_file)[0]["board"], "acme")


class PayTests(unittest.TestCase):
    def test_parse_annual_pay(self):
        p = filters.parse_annual_pay
        self.assertEqual(p("INR 6L - 9L", "INR"), (600000, 900000))
        self.assertEqual(p("3.0-4.5 LPA", "INR"), (300000, 450000))
        self.assertEqual(p("Rs. 25,000 per month", "INR"), (300000, 300000))
        self.assertEqual(p("INR 300000-500000 per-year-salary", "INR"), (300000, 500000))
        self.assertEqual(p("$80k-$100k", "USD"), (80000, 100000))
        self.assertIsNone(p("$80k-$100k", "INR"))            # never converts currencies
        self.assertIsNone(p("INR 40000 - 60000", "INR"))     # bare numbers: could be monthly, so no guess
        self.assertIsNone(p("Rs 500 per hour", "INR"))
        self.assertIsNone(p("", "INR"))
        self.assertIsNone(p("Great team", "INR"))

    def run_with(self, **settings):
        state = {"first_seen": {}, "dropped": {}}
        rows, stats, _ = run_pipeline(COMPANIES, [PM, SEO], settings, VOCAB, state, fake_fetch, now=NOW)
        return rows, stats

    def test_no_minimum_means_no_pay_filtering(self):
        rows, stats = self.run_with()
        self.assertEqual(stats["pay_dropped"], 0)
        self.assertTrue(all(r["Pay check"] == "No pay range set" for r in rows))

    def test_prefer_mode_keeps_low_pay_but_ranks_it_lower_and_flags(self):
        rows, stats = self.run_with(min_annual_pay=400000, pay_mode="prefer")
        self.assertEqual(stats["pay_dropped"], 0)
        apm = next(r for r in rows if r["Role"] == "Associate Product Manager")
        self.assertEqual(apm["Pay check"], "Below your range")           # Rs 25,000 per month = 300,000 a year
        seo = next(r for r in rows if r["Role"] == "SEO Specialist")
        self.assertEqual(seo["Pay check"], "Within your range")           # top of range 500,000
        ops = next(r for r in rows if r["Role"] == "Business Operations Associate")
        self.assertEqual((ops["Track"], ops["Pay check"]), ("pm", "Within your range"))   # 6L to 9L
        self.assertLess(ops["Rank"], apm["Rank"])                          # same track: within pay ranks first

    def test_require_mode_drops_only_known_low_pay(self):
        rows, stats = self.run_with(min_annual_pay=400000, pay_mode="require")
        roles = [r["Role"] for r in rows]
        self.assertNotIn("Associate Product Manager", roles)          # known and below
        self.assertIn("Founder's Office Associate", roles)            # pay not stated: kept
        self.assertEqual(stats["pay_dropped"], 1)


class SetupTests(unittest.TestCase):
    def scripted(self, answers):
        queue = list(answers)
        return lambda prompt: queue.pop(0)

    def test_setup_writes_settings_and_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles = os.path.join(tmp, "profiles")
            os.makedirs(profiles)
            with open(os.path.join(profiles, "pm.json"), "w") as handle:
                json.dump({"name": "pm", "skills": ["SQL"], "titles": ["Product Analyst"]}, handle)
            with open(os.path.join(profiles, "example.json"), "w") as handle:
                json.dump({"name": "example", "skills": [], "titles": ["Keep Me"]}, handle)
            settings_path = os.path.join(tmp, "settings.json")
            answers = [
                "Hyderabad, Pune",              # 1 cities
                "remove",                       # 2 other cities
                "2",                            # 3 experience
                "5",                            # 4 freshness
                "more than 8 lakhs per annum",  # 5 pay, said the way a person says it
                "remove",                       # 6 outside the range
                "",                             # resume: Enter skips (profile already exists)
                "",                             # skills for pm: Enter keeps them
                "Associate Product Manager, Growth Rocketeer",   # roles for pm
                "", "",                         # no deal-breakers, suggested priority skills
            ]
            said = []
            run_setup(settings_path, profiles, VOCAB, ask=self.scripted(answers), say=said.append)
            with open(settings_path) as handle:
                saved = json.load(handle)
            self.assertEqual(saved["preferred_locations"], ["Hyderabad", "Pune"])
            self.assertEqual(saved["location_mode"], "require")
            self.assertEqual(saved["max_experience_years"], 2)
            self.assertEqual(saved["freshness_days"], 5)
            self.assertEqual(saved["pay_currency"], "INR")
            self.assertEqual(saved["min_annual_pay"], 800000)
            self.assertIsNone(saved["max_annual_pay"])
            self.assertEqual(saved["pay_mode"], "require")
            with open(os.path.join(profiles, "pm.json")) as handle:
                self.assertEqual(json.load(handle)["titles"], ["Associate Product Manager", "Growth Rocketeer"])
            with open(os.path.join(profiles, "example.json")) as handle:
                self.assertEqual(json.load(handle)["titles"], ["Keep Me"])
            self.assertTrue(any("Growth Rocketeer" in line for line in said))   # warned: not a known role
            self.assertTrue(any("at least 8 LPA" in line for line in said))     # repeated back what it understood

    def test_polluted_saved_cities_are_repaired_and_stray_keys_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w") as handle:
                json.dump({"preferred_locations": ["[hyderabad, remote]", "\x1b"]}, handle)
            said = []
            asked = []
            def ask(prompt):
                asked.append(prompt)
                return "\x1b" if len(asked) == 1 else ""          # an Escape keypress, then Enter
            run_setup(path, os.path.join(tmp, "nope"), VOCAB, ask=ask, say=said.append)
            self.assertIn("[hyderabad, remote]", asked[0])          # shown cleaned, one bracket pair only
            self.assertNotIn("[[", asked[0])
            with open(path) as handle:
                saved = json.load(handle)
            self.assertEqual(saved["preferred_locations"], ["hyderabad", "remote"])

    def test_empty_roles_get_suggestions_and_enter_accepts_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles = os.path.join(tmp, "profiles")
            os.makedirs(profiles)
            with open(os.path.join(profiles, "seo.json"), "w") as handle:
                json.dump({"name": "seo", "skills": ["SEO", "Keyword Research", "Ahrefs"], "titles": []}, handle)
            run_setup(os.path.join(tmp, "settings.json"), profiles, VOCAB, say=lambda s: None,
                      ask=self.scripted(["", "", "", "", "none", "", "", "", "", ""]))   # Enter at skills, roles, deal-breakers, priority
            with open(os.path.join(profiles, "seo.json")) as handle:
                self.assertIn("SEO Executive", json.load(handle)["titles"])

    def test_empty_roles_cannot_be_skipped_when_no_suggestion_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles = os.path.join(tmp, "profiles")
            os.makedirs(profiles)
            with open(os.path.join(profiles, "x.json"), "w") as handle:
                json.dump({"name": "x", "skills": [], "titles": []}, handle)
            said = []
            run_setup(os.path.join(tmp, "settings.json"), profiles, VOCAB, say=said.append,
                      ask=self.scripted(["", "", "", "", "none", "", "", "", "SEO Executive", "", ""]))
            with open(os.path.join(profiles, "x.json")) as handle:
                self.assertEqual(json.load(handle)["titles"], ["SEO Executive"])
            self.assertTrue(any("at least one role" in line for line in said))

    def test_blank_or_comma_only_roles_keep_the_old_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles = os.path.join(tmp, "profiles")
            os.makedirs(profiles)
            with open(os.path.join(profiles, "seo.json"), "w") as handle:
                json.dump({"name": "seo", "skills": [], "titles": ["SEO Executive"]}, handle)
            run_setup(os.path.join(tmp, "settings.json"), profiles, VOCAB, say=lambda s: None,
                      ask=self.scripted(["", "", "", "", "none", "", "", " , ,", "", ""]))
            with open(os.path.join(profiles, "seo.json")) as handle:
                self.assertEqual(json.load(handle)["titles"], ["SEO Executive"])

    def test_pay_range_and_dollars(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            run_setup(path, os.path.join(tmp, "nope"), VOCAB, say=lambda s: None,
                      ask=self.scripted(["", "", "", "", "6-12 LPA", "keep", ""]))
            with open(path) as handle:
                saved = json.load(handle)
            self.assertEqual((saved["min_annual_pay"], saved["max_annual_pay"]), (600000, 1200000))
            run_setup(path, os.path.join(tmp, "nope"), VOCAB, say=lambda s: None,
                      ask=self.scripted(["", "", "", "", "$80k", "", ""]))
            with open(path) as handle:
                saved = json.load(handle)
            self.assertEqual((saved["pay_currency"], saved["min_annual_pay"]), ("USD", 80000))

    def test_enter_keeps_everything_and_none_clears_pay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w") as handle:
                json.dump({"min_annual_pay": 500000, "pay_mode": "require"}, handle)
            run_setup(path, os.path.join(tmp, "nope"), VOCAB, say=lambda s: None,
                      ask=self.scripted(["", "", "", "", "none", ""]))
            with open(path) as handle:
                saved = json.load(handle)
            self.assertIsNone(saved["min_annual_pay"])
            self.assertEqual(saved["max_experience_years"], 1)
            self.assertEqual(saved["preferred_locations"], ["Hyderabad", "Remote"])

    def test_bad_answers_are_asked_again_with_a_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            said = []
            answers = ["", "banglore", "keep", "abc", "-1", "3", "", "banana", "8 LPA", "keep", ""]
            run_setup(path, os.path.join(tmp, "nope"), VOCAB, say=said.append, ask=self.scripted(answers))
            with open(path) as handle:
                saved = json.load(handle)
            self.assertEqual(saved["location_mode"], "prefer")
            self.assertEqual(saved["max_experience_years"], 3)
            self.assertEqual(saved["min_annual_pay"], 800000)
            self.assertTrue(any("keep or remove" in line for line in said))
            self.assertTrue(any("could not find a number" in line for line in said))


class SafetyTests(unittest.TestCase):
    def test_profile_with_no_roles_is_refused_not_treated_as_match_everything(self):
        empty = {"name": "empty", "skills": ["SQL"], "titles": []}
        state = {"first_seen": {}, "dropped": {}}
        with self.assertRaises(ProfileError) as caught:
            run_pipeline(COMPANIES, [PM, empty], {}, VOCAB, state, fake_fetch, now=NOW)
        self.assertIn("empty", str(caught.exception))

    def test_unrelated_titles_never_appear(self):
        state = {"first_seen": {}, "dropped": {}}
        rows, stats, _ = run_pipeline(COMPANIES, [PM, SEO], {}, VOCAB, state, fake_fetch, now=NOW)
        self.assertNotIn("Office Chef", [r["Role"] for r in rows])
        self.assertGreaterEqual(stats["title_mismatch"], 1)

    def test_pay_outranks_shift_and_location(self):
        state = {"first_seen": {}, "dropped": {}}
        rows, _, _ = run_pipeline(COMPANIES, [PM, SEO], {"min_annual_pay": 450000, "pay_mode": "prefer"},
                                  VOCAB, state, fake_fetch, now=NOW)
        for track in ("pm", "seo"):                                     # ranking applies inside each track
            stated = [r for r in rows if r["Experience bucket"] == "Within your range" and r["Track"] == track]
            tiers = [r["Pay check"] for r in stated]
            self.assertEqual(tiers, sorted(tiers, key=lambda t: {"Within your range": 0}.get(t, 1 if "Not stated" in t else 2)))


class PayPreferenceTests(unittest.TestCase):
    def test_typed_the_way_people_say_it(self):
        p = filters.parse_pay_preference
        self.assertEqual(p("more than 8 lakhs per annum"), ("INR", 800000, None))
        self.assertEqual(p(">8 lakhs rupees"), ("INR", 800000, None))
        self.assertEqual(p("8 LPA"), ("INR", 800000, None))
        self.assertEqual(p("6-12 LPA"), ("INR", 600000, 1200000))
        self.assertEqual(p("up to 10 LPA"), ("INR", None, 1000000))
        self.assertEqual(p("$80k"), ("USD", 80000, None))
        self.assertEqual(p("50k per month"), ("INR", 600000, None))
        with self.assertRaises(ValueError):
            p("lots")
        with self.assertRaises(ValueError):
            p("80", "USD")

    def test_range_checks_in_the_pipeline(self):
        state = {"first_seen": {}, "dropped": {}}
        rows, stats, _ = run_pipeline(COMPANIES, [PM, SEO], {"min_annual_pay": 400000, "max_annual_pay": 550000,
                                                              "pay_mode": "prefer"}, VOCAB, state, fake_fetch, now=NOW)
        checks = {r["Role"]: r["Pay check"] for r in rows}
        self.assertEqual(checks["SEO Specialist"], "Within your range")        # 300,000 to 500,000 overlaps
        self.assertEqual(checks["Business Operations Associate"], "Above your range")   # 6L to 9L
        self.assertEqual(checks["Associate Product Manager"], "Below your range")       # 300,000


class ManualTestingBugTests(unittest.TestCase):
    """Bugs found by hand on 2026-10-22: backup files read as profiles, 'keep' saved as a role,
    and a profile with no usable roles letting a run go ahead."""

    def scripted(self, answers):
        queue = list(answers)
        return lambda prompt: queue.pop(0)

    def make_profiles(self, tmp, files):
        folder = os.path.join(tmp, "profiles")
        os.makedirs(folder)
        for name, data in files.items():
            with open(os.path.join(folder, name), "w") as handle:
                json.dump(data, handle)
        return folder

    # a) backup copies
    def test_setup_ignores_backup_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            seo = {"name": "seo", "skills": ["SEO"], "titles": ["SEO Executive"]}
            folder = self.make_profiles(tmp, {"seo.json": seo, "seo.backup.json": seo, "example.json": seo})
            asked = []
            def ask(prompt):
                asked.append(prompt)
                return ""
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, ask=ask, say=lambda s: None)
            self.assertEqual(sum("Roles [" in p for p in asked), 1)        # seo asked once, not twice
            self.assertEqual([p.name for p in profile_files(folder)], ["seo.json"])

    def test_run_skips_backup_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            seo = {"name": "seo", "skills": ["SEO"], "titles": ["SEO Executive"]}
            folder = self.make_profiles(tmp, {"seo.json": seo, "seo.backup.json": seo})
            loaded = load_profiles([os.path.join(folder, "seo.json"), os.path.join(folder, "seo.backup.json")])
            self.assertEqual(len(loaded), 1)

    # b) non-answers
    def test_keep_is_refused_as_a_role_and_enter_keeps_old_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_profiles(tmp, {"pm.json": {"name": "pm", "skills": ["SQL"],
                                                          "titles": ["Product Analyst"]}})
            said = []
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, say=said.append,
                      ask=self.scripted(["", "", "", "", "none", "", "", "keep", "yes", "", "", ""]))
            with open(os.path.join(folder, "pm.json")) as handle:
                self.assertEqual(json.load(handle)["titles"], ["Product Analyst"])
            self.assertTrue(any("'keep' is not a role name" in line and "press Enter" in line for line in said))

    def test_keep_mixed_with_real_roles_is_refused_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_profiles(tmp, {"seo.json": {"name": "seo", "skills": [], "titles": ["SEO Executive"]}})
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, say=lambda s: None,
                      ask=self.scripted(["", "", "", "", "none", "", "", "Content Writer, none", "Content Writer", "", ""]))
            with open(os.path.join(folder, "seo.json")) as handle:
                self.assertEqual(json.load(handle)["titles"], ["Content Writer"])

    def test_keep_is_refused_as_a_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_profiles(tmp, {"seo.json": {"name": "seo", "skills": ["SEO"], "titles": ["SEO Executive"]}})
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, say=lambda s: None,
                      ask=self.scripted(["", "", "", "", "none", "", "keep", "SEO, Ahrefs", "", "", ""]))
            with open(os.path.join(folder, "seo.json")) as handle:
                self.assertEqual(json.load(handle)["skills"], ["SEO", "Ahrefs"])

    def test_saved_keep_and_escape_roles_are_cleaned_on_load(self):
        self.assertEqual(clean_list(["keep", "\x1b", " ", "SEO Executive"]), ["SEO Executive"])

    # c) no usable roles stops the run before anything is fetched
    def test_profile_whose_only_role_is_keep_stops_the_run(self):
        fetched = []
        def fetch(platform, board, name):
            fetched.append(board)
            return []
        bad = {"name": "seo", "skills": ["SEO"], "titles": ["keep"]}
        with self.assertRaises(ProfileError) as caught:
            run_pipeline(COMPANIES, [PM, bad], {}, VOCAB, {"first_seen": {}, "dropped": {}}, fetch, now=NOW)
        self.assertIn("Stopped", str(caught.exception))
        self.assertEqual(fetched, [])

    # e) the demo always prints a kept count
    def test_demo_prints_kept_count_even_when_stopped(self):
        import contextlib
        import io
        from jobfinder.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_profiles(tmp, {"seo.json": {"name": "seo", "skills": ["SEO"], "titles": []}})
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["demo", "--profiles", os.path.join(folder, "seo.json"),
                             "--settings", os.path.join(tmp, "none.json"), "--out-dir", os.path.join(tmp, "out")])
            self.assertEqual(code, 1)
            self.assertIn("Kept: 0", out.getvalue())


class RolesFromSkillsTests(unittest.TestCase):
    """The process: skills from the resume, confirmed by the user, then the roles that need those skills."""

    def test_roles_come_from_confirmed_skills_with_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "profiles")
            os.makedirs(folder)
            with open(os.path.join(folder, "seo.json"), "w") as handle:
                json.dump({"name": "seo", "skills": ["SQL"], "titles": ["seo strategist"]}, handle)
            said = []
            queue = ["", "", "", "", "none",
                     "",                                         # resume: Enter skips
                     "SEO, Keyword Research, Ahrefs, Writing",   # the user corrects the skills
                     "",                                         # Enter accepts the roles that need them
                     "", ""]                                     # no deal-breakers, suggested priorities
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, say=said.append,
                      ask=lambda prompt: queue.pop(0))
            with open(os.path.join(folder, "seo.json")) as handle:
                saved = json.load(handle)
            self.assertEqual(saved["skills"], ["SEO", "Keyword Research", "Ahrefs", "Writing"])
            self.assertEqual(saved["titles"], VOCAB.suggest_titles(saved["skills"]))
            self.assertIn("SEO Executive", saved["titles"])
            self.assertNotIn("seo strategist", saved["titles"])
            self.assertTrue(any("SEO Executive: 3 of 8 skills it needs" in line for line in said))
            self.assertTrue(any("Roles saved now: seo strategist" in line for line in said))

    def test_role_matches_need_two_skills(self):
        self.assertEqual(VOCAB.role_matches(["SEO"]), [])
        title, matched, _ = VOCAB.role_matches(["GEO", "SEO"])[0]
        self.assertEqual((title, sorted(matched)), ("GEO Specialist", ["GEO", "SEO"]))


class ResumeInSetupTests(unittest.TestCase):
    """Setup asks for the resume, reads its skills, the user confirms them, then roles follow from them."""

    def test_setup_reads_resume_and_makes_a_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            resume = os.path.join(tmp, "My Resume.txt")
            with open(resume, "w") as handle:
                handle.write("SEO, keyword research on Ahrefs and Semrush, Google Search Console, content strategy.")
            folder = os.path.join(tmp, "profiles")          # does not exist yet: a first-time user
            said = []
            queue = ["", "", "", "", "none",
                     "'" + resume.replace(" ", "\\ ") + "'",   # dragged into the terminal: quotes and "\ "
                     "seo",                                   # profile name
                     "",                                      # no more resumes
                     "",                                      # Enter accepts the skills found
                     "",                                      # Enter accepts the roles that need them
                     "", ""]                                  # no deal-breakers, suggested priorities
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, say=said.append,
                      ask=lambda prompt: queue.pop(0))
            with open(os.path.join(folder, "seo.json")) as handle:
                saved = json.load(handle)
            self.assertIn("Ahrefs", saved["skills"])
            self.assertIn("SEO Executive", saved["titles"])
            self.assertEqual(saved["titles"], VOCAB.suggest_titles(saved["skills"]))
            self.assertNotIn(resume, json.dumps(saved))      # the resume path is not stored
            self.assertTrue(any("Skills found in your resume" in line for line in said))

    def test_missing_resume_file_is_asked_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            said = []
            queue = ["", "", "", "", "none", os.path.join(tmp, "nope.docx"), ""]
            run_setup(os.path.join(tmp, "settings.json"), os.path.join(tmp, "profiles"), VOCAB,
                      say=said.append, ask=lambda prompt: queue.pop(0))
            self.assertTrue(any("Could not find a file" in line for line in said))
            self.assertTrue(any("No profiles yet" in line for line in said))

    def test_experience_accepts_a_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            queue = ["", "", "0-1", "7 days", "none", ""]
            run_setup(path, os.path.join(tmp, "nope"), VOCAB, say=lambda s: None, ask=lambda prompt: queue.pop(0))
            with open(path) as handle:
                saved = json.load(handle)
            self.assertEqual((saved["max_experience_years"], saved["freshness_days"]), (1, 7))


class TwoTrackTests(unittest.TestCase):
    """One person, two interests (PM and SEO): each profile is a track with its own roles."""

    def test_results_are_grouped_and_ranked_per_track(self):
        rows, _, _ = run_pipeline(COMPANIES, [PM, SEO], {}, VOCAB, {"first_seen": {}, "dropped": {}},
                                  fake_fetch, now=NOW)
        tracks = [r["Track"] for r in rows]
        self.assertEqual(tracks, sorted(tracks, key=["pm", "seo"].index))     # all pm, then all seo
        for track in ("pm", "seo"):
            self.assertEqual([r["Rank"] for r in rows if r["Track"] == track],
                             list(range(1, tracks.count(track) + 1)))

    def test_roles_are_grouped_by_category(self):
        groups = dict(VOCAB.role_groups(["SEO", "Keyword Research", "Ahrefs", "Content Strategy",
                                         "Product Management", "Roadmapping", "Agile", "User Research"]))
        self.assertIn("SEO Executive", [t for t, _, _ in groups["SEO & Content"]])
        self.assertIn("Associate Product Manager", [t for t, _, _ in groups["Product & Strategy"]])
        for title in VOCAB.titles:                                             # every role has a category
            self.assertNotEqual(VOCAB.category(title), "Other", title)

    def test_one_resume_with_two_interests_makes_two_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            resume = os.path.join(tmp, "resume.txt")
            with open(resume, "w") as handle:
                handle.write("Product management, roadmap, agile sprints, user research, product specs. "
                             "Also SEO, keyword research, Ahrefs, Semrush and content strategy.")
            folder = os.path.join(tmp, "profiles")
            with open(resume) as handle:
                categories = [c for c, _ in VOCAB.role_groups(VOCAB.extract_skills(handle.read()))]
            said = []
            queue = ["", "", "", "", "none",
                     resume, "pm", "",          # one resume, profile 'pm', no more resumes
                     "",                        # Enter accepts the skills
                     str(categories.index("Product & Strategy") + 1),   # pm searches Product & Strategy only
                     "seo",                     # the other category becomes track 'seo'
                     "", "", "",                # pm: Enter accepts roles, no deal-breakers, priorities
                     "", "", ""]                # seo: the same
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, say=said.append,
                      ask=lambda prompt: queue.pop(0))
            with open(os.path.join(folder, "pm.json")) as handle:
                pm = json.load(handle)
            with open(os.path.join(folder, "seo.json")) as handle:
                seo = json.load(handle)
            self.assertEqual(pm["skills"], seo["skills"])
            self.assertTrue(all(VOCAB.category(t) == "Product & Strategy" for t in pm["titles"]))
            self.assertIn("SEO Executive", seo["titles"])
            self.assertNotIn("Associate Product Manager", seo["titles"])
            self.assertTrue(any(". Product & Strategy" in line for line in said))

    def test_bad_category_answer_is_asked_again(self):
        said = []
        queue = ["keep", "9", "2"]
        from jobfinder.setup import _ask_categories
        groups = VOCAB.role_groups(["SEO", "Keyword Research", "Product Management", "Roadmapping"])
        chosen = _ask_categories(lambda p: queue.pop(0), said.append, "x", groups)
        self.assertEqual(chosen, [groups[1][0]])
        self.assertTrue(any("press Enter" in line for line in said))

    def test_run_uses_every_profile_by_default(self):
        import contextlib
        import io
        from jobfinder.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "profiles")
            os.makedirs(folder)
            for name, profile in (("pm", PM), ("seo", SEO), ("seo.backup", SEO), ("example", SEO)):
                with open(os.path.join(folder, f"{name}.json"), "w") as handle:
                    json.dump(profile, handle)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["demo", "--profiles-dir", folder, "--settings", os.path.join(tmp, "none.json"),
                             "--out-dir", os.path.join(tmp, "out")])
            self.assertEqual(code, 0)
            self.assertIn("Using every profile", out.getvalue())
            self.assertIn("per track: pm", out.getvalue())
            self.assertIn("=== Track 'seo'", out.getvalue())


class DiscoveryTests(unittest.TestCase):
    """Location first: job links found by searching for your city become companies to watch."""

    def test_links_become_boards(self):
        from jobfinder.discover import extract_boards
        text = ("https://jobs.lever.co/keyloop/4f2a/apply  https://job-boards.greenhouse.io/groww/jobs/1 "
                "boards.greenhouse.io/embed/job_board?for=druva  https://jobs.ashbyhq.com/sarvam/c04?x=1 "
                "jobs.lever.co/leverdemo/x  https://jobs.lever.co/keyloop/other  https://jobs.eu.lever.co/foo/1")
        self.assertEqual(extract_boards(text), [("lever", "keyloop"), ("greenhouse", "groww"),
                                                ("greenhouse", "druva"), ("ashby", "sarvam")])

    def test_check_board_counts_jobs_in_your_cities(self):
        from jobfinder.discover import check_board
        result = check_board("lever", "beta", ["Hyderabad"], fake_fetch)
        self.assertEqual((result["jobs"], result["in_your_cities"]), (2, 1))
        self.assertFalse(check_board("lever", "nope", ["Hyderabad"], fake_fetch)["ok"])

    def test_add_command_appends_only_companies_with_jobs_in_your_cities(self):
        import contextlib
        import io
        from unittest import mock
        from jobfinder import cli
        with tempfile.TemporaryDirectory() as tmp:
            companies = os.path.join(tmp, "companies.csv")
            with open(companies, "w") as handle:
                handle.write("# comment\nname,platform,board,email,x_handle\nAcme,greenhouse,acme,,\n")
            settings = os.path.join(tmp, "settings.json")
            with open(settings, "w") as handle:
                json.dump({"preferred_locations": ["Hyderabad"], "request_delay_seconds": 0}, handle)
            links = ("https://boards.greenhouse.io/acme/jobs/1 https://jobs.lever.co/beta/abc "
                     "https://jobs.ashbyhq.com/gamma/x1 https://jobs.lever.co/doesnotexist/1")
            out = io.StringIO()
            with mock.patch.object(cli, "fetch_board", fake_fetch), contextlib.redirect_stdout(out):
                code = cli.main(["add", links, "--companies", companies, "--settings", settings, "--yes"])
            self.assertEqual(code, 0)
            boards = {(r["platform"], r["board"]) for r in read_companies(companies)}
            self.assertEqual(boards, {("greenhouse", "acme"), ("lever", "beta"), ("ashby", "gamma")})
            self.assertIn("Already in", out.getvalue())                    # acme was not added twice
            self.assertIn("could not read", out.getvalue())                # the wrong board is reported

    def test_certificate_problem_gives_a_clear_message(self):
        import ssl
        import urllib.error
        from unittest import mock
        from jobfinder import sources
        failure = urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate"))
        with mock.patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaises(SourceError) as caught:
                sources.http_get_json("https://example.com")
        self.assertIn("certificate", str(caught.exception))
        self.assertIn("pip install certifi", str(caught.exception))


class SeniorWordTests(unittest.TestCase):
    def test_senior_words_inside_your_role_name_do_not_count(self):
        words = ["senior", "staff", "head", "lead"]
        roles = VOCAB.title_phrases("Chief of Staff")
        self.assertFalse(filters.has_excluded_word("Chief of Staff (Growth, B2B SaaS)", words, roles))
        self.assertTrue(filters.has_excluded_word("Senior Chief of Staff", words, roles))
        self.assertTrue(filters.has_excluded_word("Staff Software Engineer", words, roles))
        self.assertTrue(filters.has_excluded_word("Chief of Staff", words))          # no role given: old behaviour


class RemoteLocationTests(unittest.TestCase):
    def test_remote_means_remote_in_your_country(self):
        wanted = ["Hyderabad", "Remote"]
        for location, expected in [("Remote, United States", False), ("Tokyo, Japan (Remote)", False),
                                   ("Remote", True), ("Remote - India", True), ("Bengaluru (Remote)", True),
                                   ("Hyderabad", True), ("Pune", False)]:
            self.assertEqual(filters.location_matches(location, wanted), expected, location)
        self.assertTrue(filters.location_matches("Remote, United States", wanted, remote_country=""))


class EntryLevelTests(unittest.TestCase):
    def test_entry_and_higher_level_titles(self):
        self.assertTrue(filters.entry_level_title("Product Manager I"))
        self.assertTrue(filters.entry_level_title("Junior SEO Executive"))
        self.assertTrue(filters.entry_level_title("Associate - Founder's Office"))
        self.assertFalse(filters.entry_level_title("Product Manager"))
        self.assertTrue(filters.entry_level_title("Product Manager I, Growth"))
        self.assertFalse(filters.entry_level_title("Lead IBM i (AS/400) Infrastructure Design Engineer"))
        self.assertFalse(filters.entry_level_title("Product Support Consultant - L1/L2 Client"))
        self.assertTrue(filters.higher_level_title("Product Manager II"))
        self.assertFalse(filters.higher_level_title("Product Manager I"))
        self.assertTrue(filters.title_matches("Product Manager I", VOCAB.title_phrases("Associate Product Manager")))

    def test_entry_title_ranks_between_stated_and_not_stated(self):
        def job(job_id, title, text):
            return Job("demo", "x", "X", job_id, title, "Hyderabad", f"https://e/{job_id}", text, NOW)
        jobs = [job("1", "Business Analyst", "Excel and SQL."),                  # nothing stated
                job("2", "Associate Business Analyst", "Excel and SQL."),        # entry title, years not stated
                job("3", "Business Analyst", "0-1 years of experience."),        # years stated
                job("4", "Business Analyst II", "Excel."),                       # higher level: removed
                job("5", "Product Manager I", "Roadmap.")]                       # entry PM title now matches
        profile = {"name": "pm", "skills": ["SQL"], "titles": ["Business Analyst", "Associate Product Manager"]}
        rows, stats, _ = run_pipeline([{"name": "X", "platform": "demo", "board": "x"}], [profile], {}, VOCAB,
                                      {"first_seen": {}, "dropped": {}}, lambda p, b, n: jobs, now=NOW)
        self.assertEqual([r["Apply link"][-1] for r in rows], ["3", "2", "5", "1"])
        self.assertEqual(rows[1]["Experience bucket"], "Entry-level title (years not stated)")
        self.assertEqual(stats["senior_title"], 1)


def fake_linkedin_alert(path, sent="Wed, 21 Oct 2026 04:23:35 +0000", jobs=None, sender="LinkedIn <jobs-noreply@linkedin.com>"):
    """Writes a FAKE LinkedIn alert email with the same layout as a real one. All names are invented."""
    from email.message import EmailMessage
    jobs = jobs or [("Founder's Office", "Kestrel Labs (fake)", "Hyderabad, Telangana, India", "1001", True),
                    ("Associate Product Manager", "Orbit Fintech (fake)", "India", "1002", False),
                    ("SEO Executive", "Inkwell Media (fake)", "Remote", "1003", True)]
    token = "&otpToken=FAKE0TOKEN&trackingId=abc"
    lines = ["Expand your search", "", "Recommendations based on your activity.", "  ", "", "SaaS jobs",
             "https://www.linkedin.com/comm/jobs/search-results/?keywords=x" + token, "", "      "]
    for title, company, location, job_id, apply_line in jobs:
        lines += [title, company, location] + (["Apply with resume & profile"] if apply_line else [])
        lines += [f"View job: https://www.linkedin.com/comm/jobs/view/{job_id}/?refId=r{token}", "",
                  "---------------------------------------------------------", "  ", "      "]
    lines += ["View all jobs: https://www.linkedin.com/comm/jobs/collections/?x=1" + token, "",
              "This email was intended for A Fake Person (Student)", "Unsubscribe: https://www.linkedin.com/u" + token]
    message = EmailMessage()
    message["From"], message["Subject"], message["Date"] = sender, "Kestrel Labs is hiring", sent
    message.set_content("\r\n".join(lines))
    message.add_alternative("<html><body>fake</body></html>", subtype="html")
    with open(path, "w") as handle:
        handle.write(message.as_string())


class AlertInboxTests(unittest.TestCase):
    def test_linkedin_alert_jobs_are_read_with_clean_links(self):
        from jobfinder.alerts import read_alerts
        with tempfile.TemporaryDirectory() as tmp:
            fake_linkedin_alert(os.path.join(tmp, "a.eml"))
            jobs, notes = read_alerts(tmp)
        self.assertEqual(notes, [])
        self.assertEqual([(j.title, j.company, j.location) for j in jobs],
                         [("Founder's Office", "Kestrel Labs (fake)", "Hyderabad, Telangana, India"),
                          ("Associate Product Manager", "Orbit Fintech (fake)", "India"),
                          ("SEO Executive", "Inkwell Media (fake)", "Remote")])
        self.assertEqual(jobs[0].url, "https://www.linkedin.com/jobs/view/1001/")
        for job in jobs:                                          # no tracking or one-time login tokens kept
            self.assertNotIn("?", job.url)
            self.assertNotIn("otp", job.url.lower())
        self.assertEqual(jobs[0].posted_at, datetime(2026, 10, 21, 4, 23, 35, tzinfo=timezone.utc))

    def test_same_job_in_two_emails_appears_once_and_other_senders_are_reported(self):
        from jobfinder.alerts import read_alerts
        with tempfile.TemporaryDirectory() as tmp:
            fake_linkedin_alert(os.path.join(tmp, "a.eml"))
            fake_linkedin_alert(os.path.join(tmp, "b.eml"), sent="Thu, 22 Oct 2026 04:00:00 +0000")
            fake_linkedin_alert(os.path.join(tmp, "c.eml"), sender="Someone <alerts@example.com>")
            jobs, notes = read_alerts(tmp)
        self.assertEqual(len(jobs), 3)
        self.assertTrue(all(j.posted_at.day == 22 for j in jobs))          # newest email wins
        self.assertEqual(len(notes), 1)
        self.assertIn("not supported yet", notes[0])

    def test_run_includes_alert_jobs_per_track(self):
        import contextlib
        import io
        from jobfinder.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            inbox = os.path.join(tmp, "inbox")
            os.makedirs(inbox)
            sent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")
            fake_linkedin_alert(os.path.join(inbox, "a.eml"), sent=sent)
            folder = os.path.join(tmp, "profiles")
            os.makedirs(folder)
            for name, profile in (("pm", {**PM, "titles": PM["titles"] + ["Founder's Office"]}), ("seo", SEO)):
                with open(os.path.join(folder, f"{name}.json"), "w") as handle:
                    json.dump(profile, handle)
            settings = os.path.join(tmp, "settings.json")
            with open(settings, "w") as handle:
                json.dump({"remote_sources": [], "starter_companies": False}, handle)   # tests never use the internet
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["run", "--profiles-dir", folder, "--inbox", inbox,
                             "--companies", os.path.join(tmp, "none.csv"), "--settings", settings,
                             "--state", os.path.join(tmp, "state.json"), "--out-dir", os.path.join(tmp, "out")])
            self.assertEqual(code, 0)
            with open(os.path.join(tmp, "out", os.listdir(os.path.join(tmp, "out"))[0]), encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            tracks = {r["Role"]: r["Track"] for r in rows}
            self.assertEqual(tracks, {"Founder's Office": "pm", "Associate Product Manager": "pm",
                                      "SEO Executive": "seo"})
            self.assertTrue(all(r["Date basis"].startswith("alert email date") for r in rows))
            self.assertIn("Read 3 jobs from job-alert emails", out.getvalue())

    def test_old_alert_email_jobs_count_as_stale(self):
        from jobfinder.alerts import read_alerts
        with tempfile.TemporaryDirectory() as tmp:
            fake_linkedin_alert(os.path.join(tmp, "a.eml"), sent="Wed, 01 Jan 2025 04:00:00 +0000")
            jobs, _ = read_alerts(tmp)
        rows, stats, _ = run_pipeline([{"name": "alerts", "platform": "alerts", "board": "inbox"}], [PM, SEO], {},
                                      VOCAB, {"first_seen": {}, "dropped": {}}, lambda p, b, n: jobs, now=NOW)
        self.assertEqual((len(rows), stats["stale_dropped"]), (0, 3))


class SkillsFirstTests(unittest.TestCase):
    """Jobs can be kept for your skills even when their title is not on your list."""

    def job(self, job_id, title, text, location="Hyderabad", company="Acme"):
        return Job("demo", "x", company, job_id, title, location, f"https://e/{job_id}", text, NOW)

    def run_jobs(self, jobs, profiles, **settings):
        return run_pipeline([{"name": "X", "platform": "demo", "board": "x"}], profiles,
                            {"preferred_locations": ["Hyderabad", "Remote"], **settings}, VOCAB,
                            {"first_seen": {}, "dropped": {}}, lambda p, b, n: jobs, now=NOW)

    def test_resume_phrases_take_skill_lists_but_not_personal_details(self):
        text = ("Priya Sharma | Hyderabad | priya@example.com | +91 98765 43210\n"
                "Skills: Java, Spring Boot, AWS, Docker, Kubernetes, Git, C++, Go\n"
                "Core skills - Talent Acquisition | Payroll | HRMS\n"
                "Education: B.Tech, JNTU Hyderabad, 2024\n"
                "I built a payment service in Java, deployed on AWS, with tests.")
        # Java, AWS, Docker and Kubernetes are built-in skills, so they are found anyway and not repeated here.
        self.assertEqual(VOCAB.resume_phrases(text), ["Spring Boot", "Talent Acquisition", "Payroll", "HRMS"])

    def test_title_not_listed_but_enough_skills_is_kept_in_your_city_only(self):
        analyst = {"name": "data", "skills": ["SQL", "Python", "Power BI", "Tableau"], "titles": []}
        text = "0-1 years of experience. SQL, Python, Power BI and Tableau dashboards."
        rows, stats, _ = self.run_jobs([self.job("1", "Insights Associate", text),
                                        self.job("2", "Insights Associate", text, location="Austin, TX"),
                                        self.job("3", "Insights Associate", "SQL and Python only.")], [analyst])
        self.assertEqual([r["Apply link"][-1] for r in rows], ["1"])        # 2: wrong city, 3: only 2 skills
        self.assertEqual(rows[0]["Matched by"], "skills: Power BI, Python, SQL, Tableau")
        self.assertIn("Power BI", rows[0]["Matched skills"])
        self.assertEqual(stats["kept_by_skills"], 1)

    def test_titles_only_mode_and_title_matches_rank_first(self):
        profile = {"name": "pm", "skills": ["SQL", "Data Analysis", "Power BI", "Tableau"], "titles": ["Business Analyst"]}
        text = "0-1 years. SQL, data analysis, Power BI, Tableau."
        jobs = [self.job("1", "Insights Associate", text), self.job("2", "Business Analyst", text)]
        rows, _, _ = self.run_jobs(jobs, [profile])
        self.assertEqual([(r["Apply link"][-1], r["Matched by"]) for r in rows],
                         [("2", "role title"), ("1", "skills: Data Analysis, Power BI, SQL, Tableau")])
        rows, _, _ = self.run_jobs(jobs, [profile], match_by="titles")
        self.assertEqual([r["Apply link"][-1] for r in rows], ["2"])

    def test_a_track_with_roles_keeps_only_jobs_needing_its_role_skills(self):
        pm = {"name": "pm", "skills": ["Product Management", "Roadmapping", "User Research", "Python", "SQL", "Git/GitHub",
                                       "Research", "Presentation"], "titles": ["Associate Product Manager"]}
        rows, _, _ = self.run_jobs([
            self.job("1", "Backend Developer", "0-1 years. Python, SQL and Git."),                     # developer job
            self.job("2", "Research Associate", "0-1 years. Research, presentation, Python, SQL."),  # broad skills
            self.job("3", "Growth Generalist", "0-1 years. Product roadmap, user research, SQL.")], [pm])
        self.assertEqual([r["Apply link"][-1] for r in rows], ["3"])

    def test_geo_matches_only_in_capitals(self):
        self.assertEqual(VOCAB.extract_skills("geo-distributed systems"), [])
        self.assertEqual(VOCAB.extract_skills("GEO and AI overviews"), ["GEO"])

    def test_boilerplate_and_company_name_skills_do_not_count(self):
        profile = {"name": "pm", "skills": ["Communication", "Documentation", "Presentation", "Git/GitHub", "SQL"],
                   "titles": []}
        filler = [self.job(f"f{i}", "Driver", "Communication, documentation and presentation.") for i in range(25)]
        jobs = filler + [self.job("1", "Insights Associate", "Communication, documentation, presentation, SQL, git.",
                                  company="gitlab")]
        rows, _, _ = self.run_jobs(jobs, [profile])
        self.assertEqual(rows, [])     # only SQL is specific: the rest is in most jobs, and Git is in the company name

    def test_profile_without_roles_needs_skills_and_skills_mode(self):
        no_roles = {"name": "x", "skills": ["SQL", "Python", "Tableau"], "titles": []}
        self.run_jobs([], [no_roles])                                              # allowed
        with self.assertRaises(ProfileError):
            self.run_jobs([], [no_roles], match_by="titles")
        with self.assertRaises(ProfileError):
            self.run_jobs([], [{"name": "y", "skills": ["SQL"], "titles": []}])

    def test_setup_lets_a_skills_only_track_leave_roles_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            resume = os.path.join(tmp, "cv.txt")
            with open(resume, "w") as handle:
                handle.write("Tools: Ansible, Prometheus, Grafana, Nagios, Puppet\n")
            folder = os.path.join(tmp, "profiles")
            said = []
            queue = ["", "", "", "", "none", resume, "ops", "", "", "", "", ""]   # ..., skills, roles, deal-breakers, priority
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, say=said.append,
                      ask=lambda prompt: queue.pop(0))
            with open(os.path.join(folder, "ops.json")) as handle:
                saved = json.load(handle)
            self.assertEqual(saved["skills"], ["Ansible", "Prometheus", "Grafana", "Nagios", "Puppet"])
            self.assertEqual(saved["titles"], [])
            self.assertTrue(any("leave them empty" in line for line in said))


class RemoteSourceTests(unittest.TestCase):
    """Himalayas and Remote OK, with fake feed data (no internet)."""

    def himalayas_feed(self, url):
        self.urls.append(url)
        if "page=1" not in url:
            return {"jobs": []}
        stamp = lambda days: int((NOW - timedelta(days=days)).timestamp())
        return {"jobs": [
            {"guid": "https://himalayas.app/companies/kite/jobs/apm", "title": "Associate Product Manager",
             "companyName": "Kite (fake)", "locationRestrictions": ["India"], "pubDate": stamp(1),
             "applicationLink": "https://himalayas.app/companies/kite/jobs/apm?utm_source=api",
             "description": "<p>0-1 years. Roadmap, SQL.</p>", "minSalary": 600000, "maxSalary": 900000,
             "currency": "INR", "salaryPeriod": "year"},
            {"guid": "https://himalayas.app/companies/orbit/jobs/seo", "title": "SEO Executive",
             "companyName": "Orbit (fake)", "locationRestrictions": [], "pubDate": stamp(2),
             "applicationLink": "https://himalayas.app/companies/orbit/jobs/seo?utm_source=api", "description": "SEO."},
            {"guid": "https://himalayas.app/companies/old/jobs/x", "title": "SEO Executive", "companyName": "Old",
             "pubDate": stamp(30), "expiryDate": stamp(1), "applicationLink": "https://himalayas.app/x"}]}

    def setUp(self):
        self.urls = []

    def test_himalayas_keeps_links_and_marks_who_can_apply(self):
        from jobfinder.sources.remote import fetch_himalayas
        jobs = fetch_himalayas(self.himalayas_feed, "India", 7, NOW, lambda s: None)
        self.assertIn("country=India", self.urls[0])
        self.assertEqual([(j.title, j.location) for j in jobs],
                         [("Associate Product Manager", "Remote (open to India)"), ("SEO Executive", "Remote (worldwide)")])
        self.assertTrue(jobs[0].url.endswith("?utm_source=api"))              # their apply link, unchanged
        self.assertEqual(filters.parse_annual_pay(jobs[0].pay_text, "INR"), (600000, 900000))
        self.assertTrue(all(filters.location_matches(j.location, ["Remote"]) for j in jobs))

    def test_one_download_per_day_is_reused(self):
        from jobfinder.sources.remote import fetch_himalayas
        with tempfile.TemporaryDirectory() as tmp:
            fetch_himalayas(self.himalayas_feed, "India", 7, NOW, lambda s: None, cache_dir=tmp)
            calls = len(self.urls)
            again = fetch_himalayas(self.himalayas_feed, "India", 7, NOW, lambda s: None, cache_dir=tmp)
            self.assertEqual(len(self.urls), calls)
            self.assertEqual(len(again), 2)

    def test_remoteok_keeps_only_jobs_clearly_open_to_your_country(self):
        from jobfinder.sources.remote import fetch_remoteok
        feed = [{"legal": "API terms"},
                {"id": 1, "position": "Content Writer", "company": "A", "location": "Worldwide", "url": "https://remoteok.com/1",
                 "date": "2026-10-20T00:00:00+00:00"},
                {"id": 2, "position": "SEO Executive", "company": "B", "location": "India", "url": "https://remoteok.com/2"},
                {"id": 3, "position": "SEO Executive", "company": "C", "location": "", "url": "https://remoteok.com/3"},
                {"id": 4, "position": "SEO Executive", "company": "D", "location": "Remote - US", "url": "https://remoteok.com/4"}]
        jobs = fetch_remoteok(lambda url: feed, "India", NOW)
        self.assertEqual([(j.company, j.location) for j in jobs],
                         [("A", "Remote (worldwide)"), ("B", "Remote (open to India)")])


class CompanyListTests(unittest.TestCase):
    def test_yc_candidates_and_board_guesses(self):
        from jobfinder.discover import board_guesses, yc_candidates
        companies = [{"name": "Kite Labs", "slug": "kite-labs", "website": "https://www.kitelabs.in/about",
                      "all_locations": "Bengaluru, KA, India", "regions": ["India"], "isHiring": True},
                     {"name": "Orbit", "slug": "orbit", "website": "https://orbit.com",
                      "all_locations": "San Francisco, CA, USA", "regions": ["United States of America", "Remote"]},
                     {"name": "Dormant", "slug": "dormant", "all_locations": "Pune, MH, India", "isHiring": False}]
        self.assertEqual([c["name"] for c in yc_candidates(companies, ["Hyderabad", "india"])], ["Kite Labs"])
        self.assertEqual([c["name"] for c in yc_candidates(companies, ["india", "remote"])], ["Kite Labs", "Orbit"])
        self.assertEqual(board_guesses(companies[0]), ["kite-labs", "kitelabs"])

    def test_find_board_tries_each_name_on_each_platform(self):
        from jobfinder.discover import check_board, find_board
        company = {"name": "Beta Inc", "slug": "beta-inc", "website": "https://beta.io"}
        result = find_board(company, lambda platform, board: check_board(platform, board, ["Hyderabad"], fake_fetch),
                            lambda s: None)
        self.assertEqual((result["platform"], result["board"], result["name"]), ("lever", "beta", "Beta Inc"))
        missing = find_board({"name": "Nope", "slug": "nope"}, lambda p, b: {"ok": False}, lambda s: None)
        self.assertFalse(missing["ok"])

    def test_starter_list_is_valid_and_has_no_duplicates(self):
        from jobfinder.cli import STARTER_COMPANIES
        rows = read_companies(str(STARTER_COMPANIES))
        self.assertGreaterEqual(len(rows), 20)
        pairs = [(r["platform"], r["board"]) for r in rows]
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertTrue(all(r["platform"] in ("greenhouse", "lever", "ashby") and r["name"] for r in rows))


class PriorityAndDealBreakerTests(unittest.TestCase):
    """For someone who wants PM work that is about planning, not coding."""
    PM = {"name": "pm", "skills": ["Product Management", "Roadmapping", "User Research", "Product Specs", "SQL", "Python"],
          "titles": ["Associate Product Manager", "Product Owner"], "deal_breakers": ["Coding"],
          "priority_skills": ["Product Management", "Roadmapping", "User Research", "Product Specs", "SQL"]}

    def job(self, job_id, title, text):
        return Job("demo", "x", "Acme", job_id, title, "Hyderabad", f"https://e/{job_id}", text, NOW)

    def run_jobs(self, jobs, profiles):
        return run_pipeline([{"name": "X", "platform": "demo", "board": "x"}], profiles, {"preferred_locations": ["Hyderabad"]},
                            VOCAB, {"first_seen": {}, "dropped": {}}, lambda p, b, n: jobs, now=NOW)

    def test_coding_terms_are_found_exactly(self):
        terms = VOCAB.deal_breaker_terms("Coding", "Python, SQL and C# on .NET; the rest is planning in Notion.")
        self.assertEqual(terms, [".NET", "C#", "python", "sql"])
        self.assertEqual(VOCAB.deal_breaker_terms("Coding", "Plan the roadmap; talk to the rest of the team."), [])
        self.assertTrue(VOCAB.deal_breaker_title("Coding", "Junior Software Developer"))
        self.assertFalse(VOCAB.deal_breaker_title("Coding", "Associate Product Manager"))

    def test_coding_heavy_jobs_are_removed_and_light_mentions_flagged(self):
        rows, stats, _ = self.run_jobs([
            self.job("1", "Associate Product Manager", "0-1 years. Roadmap, user research, specs. SQL and APIs a plus."),
            self.job("2", "Associate Product Manager", "0-1 years. Roadmap and user research. Write code in Python, "
                                                       "Java and SQL; build backend APIs."),
            self.job("3", "Associate Product Manager", "0-1 years. Roadmap, user research and product specs."),
            self.job("4", "Product Owner - Developer", "0-1 years. Roadmap.")], [self.PM])
        self.assertEqual([r["Apply link"][-1] for r in rows], ["3", "1"])      # clean first, flagged second
        self.assertEqual(rows[1]["Deal-breaker check"], "mentions coding: apis, sql")
        self.assertEqual(rows[0]["Deal-breaker check"], "")
        self.assertEqual(stats["deal_breaker"], 2)                              # 2: coding-heavy, 4: developer title

    def test_more_priority_skills_rank_higher_and_deal_breaker_skills_are_not_priorities(self):
        rows, _, _ = self.run_jobs([
            self.job("1", "Associate Product Manager", "0-1 years. Roadmap."),
            self.job("2", "Associate Product Manager", "0-1 years. Roadmap, user research and product specs.")], [self.PM])
        self.assertEqual([r["Apply link"][-1] for r in rows], ["2", "1"])
        self.assertEqual(rows[0]["Priority skills"], "Product Management, Product Specs, Roadmapping, User Research")
        prepared = prepare_profile(self.PM, VOCAB)
        self.assertNotIn("SQL", prepared["_priority"])                          # SQL is Coding, a deal-breaker here

    def test_setup_saves_deal_breakers_and_suggests_priorities_without_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "profiles")
            os.makedirs(folder)
            with open(os.path.join(folder, "pm.json"), "w") as handle:
                json.dump({"name": "pm", "skills": ["Product Management", "Roadmapping", "SQL", "Python"],
                           "titles": ["Associate Product Manager", "Business Analyst"]}, handle)
            said, asked = [], []
            # ..., resume, skills, categories (the one with the saved roles comes first), split track, roles, ...
            answers = ["", "", "", "", "none", "", "", "", "", "", "banana", "coding", ""]
            def ask(prompt):
                asked.append(prompt)
                return answers.pop(0)
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, say=said.append, ask=ask)
            with open(os.path.join(folder, "pm.json")) as handle:
                saved = json.load(handle)
            self.assertEqual(saved["deal_breakers"], ["Coding"])
            self.assertEqual(saved["priority_skills"], ["Product Management", "Roadmapping"])
            self.assertTrue(any("Please type one of: Coding" in line for line in said))



def answer_by_prompt(answers, asked=None):
    """An ask() that answers by the words in each prompt; anything not listed gets Enter."""
    def ask(prompt):
        if asked is not None:
            asked.append(prompt)
        for words, answer in answers:
            if words in prompt:
                return answer.pop(0) if isinstance(answer, list) else answer
        return ""
    return ask


class DataEngineerTrialTests(unittest.TestCase):
    """From a real trial: a data engineer found no jobs. Each test is one cause, fixed."""
    RESUME = ("Kamal P | Hyderabad | kamal@example.com\n"
              "Data Engineer | Acme Analytics | 2022 - Present\n"
              "Built ETL/ELT pipelines on AWS and modelled the warehouse.\n"
              "Skills: SQL, Python, PySpark, Airflow, dbt, Bash, Spring Boot\n"
              "Cloud: AWS (S3, RDS, Redshift, EC2), Glue, Lambda, VPC\n")

    def job(self, job_id, title, text, location="Hyderabad", days=1):
        return Job("demo", "x", "Acme", job_id, title, location, f"https://e/{job_id}", text,
                   None if days is None else NOW - timedelta(days=days))

    def run_jobs(self, jobs, profiles, state=None, now=NOW, **settings):
        state = state if state is not None else {"first_seen": {}, "dropped": {}}
        rows, stats, _ = run_pipeline([{"name": "X", "platform": "demo", "board": "x"}], profiles,
                                      {"preferred_locations": ["Hyderabad"], "max_experience_years": 4, **settings},
                                      VOCAB, state, lambda p, b, n: jobs, now=now)
        return rows, stats, state

    def test_resume_gives_data_roles_and_skills_without_editing_any_file(self):
        self.assertEqual(VOCAB.resume_phrases(self.RESUME), ["Spring Boot"])     # brackets split; known ones found anyway
        skills = VOCAB.extract_skills(self.RESUME)
        for skill in ("AWS", "Apache Spark", "Airflow", "dbt", "ETL/ELT", "Redshift", "Shell Scripting"):
            self.assertIn(skill, skills)
        self.assertEqual(VOCAB.resume_titles(self.RESUME)[0], "Data Engineer")
        self.assertEqual(VOCAB.role_groups(skills)[0][0], "Data & Engineering")
        with tempfile.TemporaryDirectory() as tmp:
            resume = os.path.join(tmp, "cv.txt")
            with open(resume, "w") as handle:
                handle.write(self.RESUME)
            said = []
            ask = answer_by_prompt([("Path to your resume", resume), ("Short name", "kp"), ("Pay [", "none")])
            run_setup(os.path.join(tmp, "settings.json"), os.path.join(tmp, "profiles"), VOCAB,
                      say=said.append, ask=ask)
            with open(os.path.join(tmp, "profiles", "kp.json")) as handle:
                saved = json.load(handle)
        self.assertEqual(saved["titles"][0], "Data Engineer")
        self.assertIn("Spring Boot", saved["skills"])
        self.assertFalse([s for s in saved["skills"] if "(" in s])
        self.assertTrue(any("Roles named in your resume: Data Engineer" in line for line in said))

    def test_mistyped_role_is_offered_the_built_in_spelling(self):
        self.assertEqual(VOCAB.closest_title("Data Enginner"), "Data Engineer")
        self.assertEqual(VOCAB.closest_title("data engineer"), None)             # already right
        self.assertEqual(VOCAB.closest_title("Growth Hacker"), None)             # nothing close: kept as typed
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "profiles")
            os.makedirs(folder)
            with open(os.path.join(folder, "kp.json"), "w") as handle:
                json.dump({"name": "kp", "skills": ["SQL", "Python", "Airflow"], "titles": ["Data Analyst"]}, handle)
            asked = []
            ask = answer_by_prompt([("Pay [", "none"), ("  Roles [", "Data Enginner, Data Analyst, Growth Hacker"),
                                    ("Did you mean", "")], asked)
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, say=lambda s: None, ask=ask)
            with open(os.path.join(folder, "kp.json")) as handle:
                saved = json.load(handle)
        self.assertEqual(saved["titles"], ["Data Engineer", "Data Analyst", "Growth Hacker"])
        self.assertEqual(sum("Did you mean 'Data Engineer'" in prompt for prompt in asked), 1)

    def test_same_resume_twice_is_not_made_into_two_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            resume = os.path.join(tmp, "cv.txt")
            with open(resume, "w") as handle:
                handle.write(self.RESUME)
            said = []
            ask = answer_by_prompt([("Pay [", "none"), ("Path to your resume", resume),
                                    ("Path to another resume", [resume, ""]), ("Short name", "kp")])
            run_setup(os.path.join(tmp, "settings.json"), os.path.join(tmp, "profiles"), VOCAB,
                      say=said.append, ask=ask)
            self.assertEqual(os.listdir(os.path.join(tmp, "profiles")), ["kp.json"])
        self.assertTrue(any("You already gave this resume (profile 'kp')" in line for line in said))

    def test_a_priority_skill_not_in_the_skills_still_counts_and_one_is_enough(self):
        profile = {"name": "kp", "skills": ["SQL", "Python", "Airflow"], "titles": [], "priority_skills": ["Data Engineering"]}
        prepared = prepare_profile(profile, VOCAB)
        self.assertIn("Data Engineering", prepared["_skills"])
        self.assertEqual(prepared["_core_needed"], 1)
        rows, stats, _ = self.run_jobs([self.job("1", "Platform Associate",
                                                 "2 years of experience. Data pipelines with SQL, Python and Airflow.")],
                                       [profile])
        self.assertEqual([r["Apply link"][-1] for r in rows], ["1"])
        self.assertEqual(stats["kept_by_skills"], 1)

    def test_allowing_older_jobs_brings_back_jobs_removed_earlier(self):
        profile = {"name": "kp", "skills": ["SQL"], "titles": ["Data Engineer"]}
        jobs = [self.job("1", "Data Engineer", "SQL.", days=10)]
        rows, stats, state = self.run_jobs(jobs, [profile], freshness_days=7)
        self.assertEqual((len(rows), stats["stale_dropped"]), (0, 1))
        rows, stats, state = self.run_jobs(jobs, [profile], state=state, freshness_days=7)   # still gone, not new
        self.assertEqual((len(rows), stats["already_dropped"]), (0, 1))
        rows, _, state = self.run_jobs(jobs, [profile], state=state, freshness_days=15)      # wider window: back
        self.assertEqual(len(rows), 1)
        self.assertNotIn("demo:x:1", state["dropped"])

    def test_older_memory_files_still_work(self):
        profile = {"name": "kp", "skills": ["SQL"], "titles": ["Data Engineer"]}
        jobs = [self.job("1", "Data Engineer", "SQL.", days=10), self.job("2", "Data Engineer", "SQL.", days=None)]
        old = {"first_seen": {}, "dropped": {"demo:x:1": "2026-10-19", "demo:x:2": "2026-10-19"}}  # day removed only
        rows, stats, _ = self.run_jobs(jobs, [profile], state=old, freshness_days=15)
        self.assertEqual([r["Apply link"][-1] for r in rows], ["1"])      # dated: checked again by its own date
        self.assertEqual(stats["already_dropped"], 1)                     # undated: its age is unknown, so it stays gone

    def test_other_cities_means_other_cities_in_your_country_not_abroad(self):
        profile = {"name": "kp", "skills": ["SQL"], "titles": ["Data Analyst"]}
        rows, stats, _ = self.run_jobs([self.job("1", "Data Analyst", "SQL.", location="Pune"),
                                        self.job("2", "Data Analyst", "SQL.", location="Mohali, IND"),
                                        self.job("3", "Data Analyst", "SQL.", location="Bangkok"),
                                        self.job("4", "Data Analyst", "SQL.", location="Remote - USA"),
                                        self.job("5", "Data Analyst", "SQL.", location="Remote (open to India)")],
                                       [profile])
        self.assertEqual(sorted(r["Apply link"][-1] for r in rows), ["1", "2", "5"])
        self.assertEqual(stats["location_dropped"], 2)
        rows, _, _ = self.run_jobs([self.job("3", "Data Analyst", "SQL.", location="Bangkok")], [profile],
                                   remote_country="")                                   # no country set: anywhere
        self.assertEqual(len(rows), 1)

    def test_senior_titles_are_judged_by_their_years_once_you_allow_3_or_more(self):
        profile = {"name": "kp", "skills": ["SQL"], "titles": ["Data Engineer"]}
        jobs = [self.job("1", "Senior Data Engineer", "SQL. 3-5 years of experience."),
                self.job("2", "Senior Data Engineer", "SQL. 7+ years of experience."),
                self.job("3", "Lead Data Engineer", "SQL. 3 years of experience.")]
        rows, _, _ = self.run_jobs(jobs, [profile])                                     # 4 years allowed
        self.assertEqual([r["Apply link"][-1] for r in rows], ["1"])
        rows, _, _ = self.run_jobs(jobs, [profile], max_experience_years=1)
        self.assertEqual(rows, [])

    def test_level_marks_follow_the_years_you_set(self):
        self.assertTrue(filters.higher_level_title("Data Engineer II"))           # default: 1 year
        self.assertFalse(filters.higher_level_title("Data Engineer II", 4))
        self.assertTrue(filters.higher_level_title("Data Engineer III", 4))
        rows, _, _ = self.run_jobs([self.job("1", "Data Engineer II", "SQL, Python. 3+ years of experience.")],
                                   [{"name": "kp", "skills": ["SQL", "Python"], "titles": ["Data Engineer"]}])
        self.assertEqual(len(rows), 1)


class FindingJobsTests(unittest.TestCase):
    """The agent finds jobs by itself: a built-in company list, and a search for your roles."""

    def test_himalayas_is_searched_for_each_role(self):
        from jobfinder.sources.remote import fetch_himalayas
        urls = []

        def feed(url):
            urls.append(url)
            if "page=1" not in url:
                return {"jobs": []}
            return {"jobs": [{"guid": "g1", "title": "Data Engineer", "companyName": "Kite (fake)",
                              "locationRestrictions": ["India"], "pubDate": int(NOW.timestamp()),
                              "applicationLink": "https://himalayas.app/x", "description": "SQL."}]}
        with tempfile.TemporaryDirectory() as tmp:
            jobs = fetch_himalayas(feed, "India", 7, NOW, lambda s: None, cache_dir=tmp,
                                   queries=["Data Engineer", "Data Analyst"], full=False)
            self.assertEqual(sorted(os.listdir(tmp)), ["himalayas-india-search-data-analyst-2026-10-21.json",
                                                       "himalayas-india-search-data-engineer-2026-10-21.json"])
        self.assertTrue(all("q=Data%20" in url for url in urls))
        self.assertEqual([j.title for j in jobs], ["Data Engineer"])       # the same job in two searches appears once

    def test_job_lists_are_read_side_by_side_and_failures_stay_per_company(self):
        from jobfinder.cli import prefetch
        companies = [{"name": "A", "platform": "greenhouse", "board": "a"},
                     {"name": "B", "platform": "greenhouse", "board": "b"},
                     {"name": "C", "platform": "lever", "board": "c"}]

        def fetch(platform, board, name):
            if board == "b":
                raise ConnectionResetError("reset by peer")
            if board == "c":
                raise SourceError("not found")
            return ["job"]
        fetched = prefetch(companies, fetch, 1.5, sleep=lambda s: None)
        self.assertEqual(fetched.read("greenhouse", "a", "A"), ["job"])
        for platform, board, name in (("greenhouse", "b", "B"), ("lever", "c", "C")):
            with self.assertRaises(SourceError):
                fetched.read(platform, board, name)

    def test_starter_list_is_large_and_spans_the_three_platforms(self):
        companies = read_companies(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                "data", "companies-starter.csv"))
        self.assertGreaterEqual(len(companies), 100)
        self.assertEqual({c["platform"] for c in companies}, {"greenhouse", "lever", "ashby"})


if __name__ == "__main__":
    unittest.main()

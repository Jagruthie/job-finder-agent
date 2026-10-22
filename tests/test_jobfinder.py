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
from jobfinder.pipeline import (ProfileError, clean_list, profile_files, read_companies, run_pipeline, write_csv,
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
                      ask=self.scripted(["", "", "", "", "none", "", "", ""]))   # Enter at skills, then Enter at roles
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
                      ask=self.scripted(["", "", "", "", "none", "", "", "", "SEO Executive"]))
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
                      ask=self.scripted(["", "", "", "", "none", "", "", " , ,"]))
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
                      ask=self.scripted(["", "", "", "", "none", "", "", "keep", "yes", ""]))
            with open(os.path.join(folder, "pm.json")) as handle:
                self.assertEqual(json.load(handle)["titles"], ["Product Analyst"])
            self.assertTrue(any("'keep' is not a role name" in line and "press Enter" in line for line in said))

    def test_keep_mixed_with_real_roles_is_refused_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_profiles(tmp, {"seo.json": {"name": "seo", "skills": [], "titles": ["SEO Executive"]}})
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, say=lambda s: None,
                      ask=self.scripted(["", "", "", "", "none", "", "", "Content Writer, none", "Content Writer"]))
            with open(os.path.join(folder, "seo.json")) as handle:
                self.assertEqual(json.load(handle)["titles"], ["Content Writer"])

    def test_keep_is_refused_as_a_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_profiles(tmp, {"seo.json": {"name": "seo", "skills": ["SEO"], "titles": ["SEO Executive"]}})
            run_setup(os.path.join(tmp, "settings.json"), folder, VOCAB, say=lambda s: None,
                      ask=self.scripted(["", "", "", "", "none", "", "keep", "SEO, Ahrefs", ""]))
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
                     ""]                                         # Enter accepts the roles that need them
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
                     ""]                                      # Enter accepts the roles that need them
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
                     "",                        # Enter accepts pm's roles
                     ""]                        # Enter accepts seo's roles
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


if __name__ == "__main__":
    unittest.main()

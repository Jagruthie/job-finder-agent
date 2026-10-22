# Job Finder Agent

Finds **fresh** jobs on company career pages, keeps the ones that match the skills on your resume, and writes a CSV with the exact link to apply.

It reads only public job lists that Greenhouse, Lever and Ashby publish for company career pages. It does not scrape LinkedIn, does not use paid services, and does not send anything on your behalf.

> **Status: version 0.1.** The logic is tested with fake jobs (`python -m unittest discover -s tests`). It has **not yet been run against live career pages**, so expect to fix a field name or two on the first real run.

## What it does

1. Asks for your resume and reads it on your own computer. It lists the skills it finds.
2. **You confirm or edit those skills.** Then it lists the roles that need those skills, with the reasons, and you confirm the roles. This is the confirm step.
3. Fetches open jobs for the companies in your `companies.csv`.
4. Removes jobs older than 7 days and remembers them, so they never return as new.
5. Keeps only the roles you want, asking for 0 to 1 years of experience (jobs that do not state it go in a review bucket).
6. Matches your skills against the skills each job mentions, and shows its reasons.
7. Writes `output/jobs-<date>.csv`, best matches first.

## Try it first on fake jobs

```bash
python -m jobfinder demo --profiles profiles/pm.json profiles/seo.json
```

This runs your real profiles against 12 invented jobs built to trigger each rule (fresh, stale, too senior, experience not stated). Nothing is fetched and no memory is saved. It writes `output/demo-<date>.csv`.

## Quick start

```bash
pip install -r requirements.txt

# 1. Answer a few questions and give your resume (drag the file into the terminal).
#    You confirm the skills it finds, then the roles that need them.
#    One profile per resume, for example "pm" and "seo".
python -m jobfinder setup

# 2. Copy the companies file and add the companies you want to watch
cp companies.example.csv companies.csv

# 3. Run it (daily is a good rhythm)
python -m jobfinder run --profiles profiles/pm.json profiles/seo.json
```

On Windows, use `copy` instead of `cp`.

To add a company, open its careers page and look at the link. `boards.greenhouse.io/acme` means platform `greenhouse`, board `acme`. `jobs.lever.co/acme` means `lever`. `jobs.ashbyhq.com/acme` means `ashby`.

## Reading the CSV

| Column | Meaning |
| --- | --- |
| Apply link | The exact page where you apply |
| Posted / Date basis | The platform's date, or "first seen" when the platform gives none. First-seen dates start counting on your first run, so their real age is unknown |
| Experience bucket | "Within your range" means the posting states a number that fits your setting. "Not stated (review)" means the posting gave no number, so read it |
| Fit % | Share of the skills the job mentions that are on your profile. Only skills in `data/skills.json` are counted |
| Missing skills | Skills the job mentions that are not on your profile |
| Location match / Shift | Preferences only. They raise a job's rank and never remove it |
| Company email / Founder X handle | Only what you typed into `companies.csv` |

## Change your preferences

Run `python -m jobfinder setup`. It asks a few questions in the terminal and saves your answers. Press Enter to keep the value in brackets. Typing `keep` or `yes` is not saved as a role or skill; press Enter instead.

| Question | Setting | Where it is saved |
| --- | --- | --- |
| Your resume (optional on later runs). Read locally; the file is not copied and its path is not saved | `skills` | a profile file in `profiles/` |
| Skills: the ones found in your resume, which you confirm or edit | `skills` | each profile file in `profiles/` |
| Roles: the ones that need 2 or more of your skills, which you confirm or edit | `titles` | each profile file in `profiles/` |
| Cities | `preferred_locations`, `location_mode` | `settings.json` |
| Most years of experience a job may ask for (`1`, or a range such as `0-1`) | `max_experience_years` | `settings.json` |
| Posted within how many days | `freshness_days` | `settings.json` |
| Pay you want, typed your own way, such as `8 LPA`, `more than 8 lakhs`, `6-12 LPA`, `up to 10 LPA` or `$80k` | `min_annual_pay`, `max_annual_pay`, `pay_currency`, `pay_mode` | `settings.json` |

You can also edit these files by hand. Other settings: `exclude_title_words`, `request_delay_seconds`. Backup copies named `*.backup.json` in `profiles/` are ignored.

**How pay works.** `setup` repeats back what it understood. In `prefer` mode, jobs whose stated pay is outside your range stay on the list but rank lower. In `require` mode they are removed. Jobs that do not state pay are always kept, because most postings do not. Pay is only compared when the posting uses your currency, and it is never converted. Monthly pay is multiplied by 12. Hourly pay, and bare numbers with no sign of a period, are treated as not comparable, because guessing would wrongly remove good jobs.

## How results are ranked

In this order: experience stated and within your range first, then pay inside your range, then your preferred cities, then a mention of a 10 to 6 shift, then the skill match. Jobs with no stated pay sit between jobs inside and outside your range.

Every run starts by printing the roles each profile is looking for. If a profile has no roles, the run stops with a message instead of matching every title.

## Privacy: what stays on your computer

Never committed (see `.gitignore`): your resumes, your profiles, `settings.json`, `companies.csv`, `state.json` (the memory of seen and removed jobs) and everything in `output/`. Resumes are read locally and never uploaded.

## Known limits

- **Coverage.** Only companies on Greenhouse, Lever or Ashby can be read, and only the ones you list.
- **Dates can mislead.** Greenhouse and Ashby dates can change when a company edits or reposts an old job, so a few stale jobs will look fresh.
- **Skills matching is word matching.** It misses ideas that are not in `data/skills.json`. Add your own words there.
- **Experience reading is a guess from text.** Check the Experience column.
- **Platform rules.** The platforms' API docs state no usage terms, but check their terms yourself, and keep the request delay.
- **Not built yet:** finding companies automatically, and finding company emails and founder handles automatically.

## Contributing

- Add skills, aliases or role titles in `data/skills.json`.
- Add a new job platform: copy `jobfinder/sources/lever.py`, return `Job` objects, and register it in `jobfinder/sources/__init__.py`.
- Add tests with fake data only. Never commit real resumes or personal data.

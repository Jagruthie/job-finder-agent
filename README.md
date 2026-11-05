# Freshlist

**Freshlist watches a fixed list of company career pages and shows you only the new jobs that match your profile. A built-in tracker logs every application and every resume change.**

You stay in charge. Freshlist never applies, sends or posts anything for you. It is free, uses no paid services, and keeps your data on your own computer.

The repository is named `job-finder-agent`, and the command is `python -m jobfinder`.

> **Status: version 0.2.** Tested offline with fake data, and against live Greenhouse, Lever and Ashby pages in November 2026, on macOS with Python 3.14. Windows and Linux are untested. Built with job hunting in India in mind (see Limitations).

## How it works

1. **Watch-list.** You keep a list of companies in `companies.csv`. Around 30 to 40 is a good size.
2. **Run.** `run` re-checks every active company's public job list and filters by your roles, skills, experience, location and a 7-day freshness rule.
3. **New only.** Freshlist remembers what it has shown you, so each run lists only new matches, with the company, role, posted date and exact apply link.
4. **Tracker.** Every application goes in one file, from any source. If you changed your resume for a job, you can't mark it applied until you have written how you would explain that change in an interview.
5. **Review.** A weekly report counts matches per company and flags quiet companies. It only suggests changes. It never makes them.

## Install and setup

You need Python 3.9 or newer and a terminal.

```bash
git clone https://github.com/Jagruthie/job-finder-agent.git
```

```bash
cd job-finder-agent && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

In every new terminal, switch the folder's Python on again before using Freshlist:

```bash
cd job-finder-agent && source .venv/bin/activate
```

**On Windows** (untested), use `py -m venv .venv` and `.venv\Scripts\activate` instead.

Then set up your profile and try it on invented jobs:

```bash
python -m jobfinder setup
```

```bash
python -m jobfinder demo
```

`setup` asks a few questions and reads your resume on your computer. You confirm the skills and roles it finds, and they are saved as a profile. Each profile is one search track, such as `pm` or `seo`.

Then add companies and run:

```bash
python -m jobfinder companies add https://jobs.lever.co/example-analytics
```

```bash
python -m jobfinder run
```

**Don't want to pick companies one by one?** Import the built-in starter list of about 105 companies, then remove the ones you don't want:

```bash
python -m jobfinder companies import data/companies-starter.csv
```

**Upgrading from 0.1?** Convert your old company list once. A backup is kept:

```bash
python -m jobfinder companies migrate
```

## Command reference

All names and links below are fake examples.

### Watch-list

| Command | What it does |
| --- | --- |
| `companies add <link> [--name] [--tier] [--notes]` | Adds a company and detects the platform from the link |
| `companies list [--tier] [--status]` | Lists your companies |
| `companies set-tier <company> watch\|deep` | Sets a label you can see and filter by. It does not change how a company is checked |
| `companies set-status <company> active\|quiet\|swapped` | Only `active` companies are checked on a run |
| `companies import <file.csv>` | Adds many companies from a plain CSV with a `careers_url` column, or `platform` and `board` columns |
| `companies migrate` | Converts an older `companies.csv` and keeps a backup |
| `add <links or saved page> [--all] [--yes]` | Finds Greenhouse, Lever and Ashby links, checks each company, and asks before adding those with jobs in your cities |

```bash
python -m jobfinder companies add https://job-boards.greenhouse.io/sample-robotics --name "Sample Robotics" --tier deep
```

A careers page on any other system is saved with platform `other`. It stays on your list, but Freshlist can't read it, and each run reports it as skipped.

### Run

```bash
python -m jobfinder run
```

```text
Companies read: 2; failed: 1; cannot be read (not Greenhouse, Lever or Ashby): 0
  - Demo Payments (ashby/demo-payments): not found (check the board name in companies.csv)

New matches: 1 (already shown on earlier runs, left out: 3)

1. Associate Product Manager at Example Analytics  [pm]
   Remote | posted 2026-01-10 | your role: Associate Product Manager
   apply: https://jobs.lever.co/example-analytics/123
```

The day's new matches are also saved to `output/jobs-<date>.csv`.

### Tracker

| Command | What it does |
| --- | --- |
| `tracker add --from-run <number>` | Logs a match from the last run by its number |
| `tracker add --company --role --source [more fields]` | Logs anything else, such as a LinkedIn find or a referral |
| `tracker update <id> [fields]` | Changes an entry. An empty value clears a field |
| `tracker list [--status]` | Lists entries |
| `tracker due` | Lists applications whose follow-up date has arrived |
| `tracker export [--out file.xlsx]` | Writes an Excel copy. `tracker.csv` stays the source of truth |

```bash
python -m jobfinder tracker add --company "Sample Robotics" --role "SEO Executive" --source linkedin --changes-made "Moved SEO project to the top"
```

```bash
python -m jobfinder tracker update t-1a2b3c4d --status applied --how-i-would-explain-it "I ran that project and can walk through the results"
```

Marking an entry applied fills in today's date if none is set, and sets the follow-up date 7 days later.

| Field | Allowed values |
| --- | --- |
| `source` | `freshlist`, `linkedin`, `founder-outreach`, `referral` |
| `resume_version` | `pm`, `seo`, `hybrid`, `custom` (change the list with `resume_versions` in settings) |
| `status` | `to_apply`, `applied`, `interview`, `offer`, `rejected`, `closed`, `skipped` |
| `outreach_status` | `not_started`, `sent`, `replied`, `not_needed` |

Other fields are `apply_link`, `posted_date`, `changes_made`, `how_i_would_explain_it`, `founder_x_handle`, `company_email`, `applied_date`, `followup_date` and `notes`. Dates are written as YYYY-MM-DD.

### Review

```bash
python -m jobfinder review
```

Shows matches per company and flags active companies with no match in 14 days, suggesting you consider swapping them. It also shows a checkpoint: if fewer than 3 live matches were found in the 14 days after your first live run, it warns you and suggests widening your list or your role filter. Both numbers can be changed in settings. Review never changes your files.

### Other commands

| Command | What it does |
| --- | --- |
| `setup` | Sets your preferences and profiles. Run it again any time |
| `demo` | Runs your profiles on 16 invented jobs. Nothing is fetched or remembered |
| `profile <resume> --name <name>` | Makes a profile from a resume without the questions |
| `today [--day A\|B]` | Shows the checklist for the optional rotation below |

Every command also accepts `--data-dir <folder>` before its name, to keep your files somewhere else.

## Optional: the two-day rotation

If a routine helps, `today` works out whether it is Day A or Day B from the date. Days alternate without a break, including across month ends. Override it with `--day`.

- **Day A, find and apply:** run Freshlist, read the new matches, tailor your resume, log each change with its explanation, apply yourself, and log it.
- **Day B, reach out and widen:** handle recent applications without outreach and follow-ups that are due, scan LinkedIn yourself for up to 30 minutes, and log what you find.

`today` only shows a checklist. It doesn't fetch jobs or contact anyone.

## Optional sources (off by default)

A normal run reads only your watch-list. These older sources still work if you turn them on:

| Source | How to turn it on | What it does |
| --- | --- | --- |
| Starter companies | `run --starter`, or `"use_starter_companies": true` | Also reads the built-in list in `data/companies-starter.csv` |
| Remote job boards | `run --remote-boards`, or `"use_remote_boards": true` | Also reads the free Himalayas and Remote OK feeds, if "Remote" is one of your places |
| Saved LinkedIn alert emails | `run --alert-emails`, or `"use_alert_emails": true` | Reads `.eml` alert emails that you saved in `inbox/` yourself. Freshlist never connects to LinkedIn |
| Y Combinator company finder | `discover yc --enable`, or `"use_yc_discovery": true` | Looks for job boards of YC's hiring companies, using an unofficial list, and asks before adding any |

The `true` settings go in `settings.json`. The flags apply to that one command.

## Settings

Run `setup` to change the common ones, or add any of these to `settings.json`. Every setting and its default is in `jobfinder/config.py`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `freshness_days` | 7 | Drop jobs older than this |
| `max_experience_years` | 1 | The most years a job may ask for |
| `preferred_locations` | `["Remote"]` | Jobs elsewhere in your country are kept but ranked lower. Set `location_mode` to `require` to remove them |
| `remote_country` | India | Your country. Jobs abroad are removed |
| `request_delay_seconds` | 1.5 | The wait between requests to the same site |
| `followup_days` | 7 | Follow-up date = applied date + this |
| `quiet_after_days` | 14 | When to flag a company as quiet |
| `checkpoint_days`, `checkpoint_min_matches` | 14, 3 | The checkpoint's window and minimum |

## Planned

None of these are available yet:

- A local web UI (the code is already structured for it, see [ARCHITECTURE.md](ARCHITECTURE.md))
- Company discovery as a built-in part of the normal flow
- Automatic contact finding

## Limitations

- **Only three platforms.** Your watch-list can only be read on Greenhouse, Lever and Ashby. Many companies use other systems.
- **No LinkedIn reading.** Freshlist does not read LinkedIn. The optional alert-email reader only reads emails you saved yourself.
- **No guarantee of interviews.** Freshlist finds openings. It can't promise any outcome.
- **A match is not a verdict.** A job showing up does not mean it is a good fit. Matching compares words, not ideas, so read each posting yourself.
- **Endpoints can change.** These platforms' public endpoints could change or stop working at any time.
- **Experience and dates can mislead.** Years of experience are read from the job text, which is a best guess. A company that closes and reposts a job can make it appear new.
- **Best for India so far.** Other countries work, but only Indian city names are built in, and the starter list was chosen for India.

## Responsible use

- Respect each site's terms and robots.txt, and check them yourself.
- Keep the request rate low. Freshlist waits between requests to the same site by default, so please don't lower that much.
- Review everything before you apply or message anyone. Freshlist never sends or applies anything on its own.
- Check companies yourself before applying, especially internships that ask for a fee.

## Your data

These stay on your computer and are never committed (see `.gitignore`): resumes, your profiles, `settings.json`, `companies.csv` and its backups, `tracker.csv`, `seen_jobs.json`, `last_run.json`, `run_log.json`, `state.json`, `inbox/`, `cache/`, `output/` and any `.xlsx` file.

To see the file formats, look at the fake samples: `companies.example.csv`, `tracker.example.csv`, `settings.example.json` and `profiles/example.json`.

## Contributing

- Read [ARCHITECTURE.md](ARCHITECTURE.md) first. Logic goes in the core, which never prints or asks. The terminal layer only parses and prints.
- Add a job platform by copying `jobfinder/sources/lever.py` and registering it in `jobfinder/sources/__init__.py`.
- Add other spellings of skills and roles in `data/skills.json`.
- Add tests with fake data only, and keep them offline: `python -m unittest discover -s tests`.
- Never commit real resumes, emails or personal data.

## License

Licensed under the [Apache License, Version 2.0](LICENSE). You may use, change and share this code, including commercially, as long as you keep the license and copyright notice and say which files you changed. It comes with no warranty.

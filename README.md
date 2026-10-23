# Freshlist

**Freshlist** is an open-source job finder agent. It finds **fresh** jobs that fit your resume and writes a CSV with the exact link to apply. You stay in charge: it never applies, sends or posts anything for you.

The repository is named `job-finder-agent`, and the command is `python -m jobfinder`.

It reads public job lists (company boards on Greenhouse, Lever and Ashby, and the remote-job boards Himalayas and Remote OK), plus job-alert emails you save from LinkedIn. It does not scrape LinkedIn or any other site, uses no paid services, and keeps your resume and settings on your own computer.

> **Status: version 0.1.** Tested with fake jobs (`python -m unittest discover -s tests`, all offline) and run against live job boards in October 2026. Only tested on macOS with Python 3.14; other systems and Python versions are untested.

## How it works

1. **Setup** asks a few questions (cities, experience, freshness, pay) and reads your resume on your computer.
2. **You confirm the skills and roles** found in your resume, and edit them in your own words. They are saved.
3. For each track you can also set **priority skills** (they count more) and **deal-breakers** (work it must not depend on; built in: Coding).
4. **Every run** collects today's jobs, removes the ones that fail your rules (age, years, place, pay), and **ranks the rest by how well they fit your saved roles and skills**, the way a search engine ranks pages. The strong matches are kept, each with its reasons.
5. You open the CSV, read the reasons, and apply yourself.

**You never list companies or edit files.** The agent reads about 105 companies' job boards that hire in India, and remote-job boards when you choose Remote. Confirming the skills and roles it shows you during setup is all the matching needs, for any field.

**Two interests?** Each profile is one *track*, such as `pm` and `seo`. Give one resume per track (two versions of your resume are fine). Results are grouped and counted per track.

## Install

You need Python 3 (tested with 3.14) and a terminal.

```bash
git clone https://github.com/Jagruthie/job-finder-agent.git
```

```bash
cd job-finder-agent && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\activate` instead of `source .venv/bin/activate`.

## Quick start

```bash
python -m jobfinder setup     # questions, your resume, skills, roles, priorities, deal-breakers
python -m jobfinder demo      # try it on 16 invented jobs (no internet)
python -m jobfinder run       # the real search; daily is a good rhythm
```

Then open the newest file in `output/`, for example `output/jobs-2026-10-22.csv`.

## All commands

| Command | What it does |
| --- | --- |
| `setup` | Asks your preferences, reads your resume, and saves your settings and profiles. Run it again any time to change them |
| `demo` | Runs your profiles against 16 invented jobs built to trigger each rule. Nothing is fetched or remembered |
| `run` | Reads every source, keeps the jobs that fit, writes `output/jobs-<date>.csv` |
| `add <links or file>` | Adds companies from job links or a saved page of search results |
| `discover yc` | Finds Y Combinator startups to watch (add `--remote` for remote-hiring ones) |
| `profile <resume> --name pm` | Makes a profile from a resume without the questions (setup does this too) |

## Where jobs come from

Every run uses your saved filters on all of these:

| Source | What it is | How your filters apply |
| --- | --- | --- |
| Built-in company list (`data/companies-starter.csv`) | About 105 companies checked to have jobs in India or remote-India on their Greenhouse, Lever or Ashby boards (product, data, engineering, sales, operations), about 1,800 jobs in India when checked in October 2026 | All their jobs are read; your filters pick the right ones. You do not need to add any. Turn off with `"starter_companies": false` |
| Your companies (`companies.csv`), optional | Extra companies you added with `add` or `discover`, or by hand | Same as above |
| Remote job boards: [Himalayas](https://himalayas.app) and [Remote OK](https://remoteok.com) | Free public feeds of remote jobs | Used when "Remote" is one of your places: both boards' recent feeds are read, and Himalayas is also searched for each of your roles. Only jobs open to your `remote_country` (default India) or worldwide are kept |
| Job-alert emails in `inbox/` | LinkedIn alerts you saved | The alert's own search, then your filters on top |

**Remote job boards.** Himalayas is searched for jobs open to your country, newest first, in full and by each of your role titles. Remote OK's feed is filtered to jobs that clearly say worldwide, India, Asia or APAC; about half of its jobs give no location, so those are skipped. Each source is downloaded at most once a day and kept in `cache/`, as they ask. Their apply links are kept unchanged and the Platform column names the source, which is how their terms ask to be credited.

**Not used:** Remotive (its free feed returns only 10 jobs; the full API is paid), Product Hunt (its API needs an account, has no jobs, and is non-commercial by default), and sites that do not allow automated reading, such as LinkedIn, Naukri and Indeed.

## Add more companies (optional)

The built-in list is read on every run, so this is only for companies it does not cover.

### From jobs in your city (location first)

Guessing company names finds few jobs in your city. It works much better to start from jobs that are already there.

1. Search the web yourself, for example (change the city and words):
   - `site:jobs.lever.co Hyderabad`
   - `site:boards.greenhouse.io Hyderabad` and `site:job-boards.greenhouse.io Hyderabad`
   - `site:jobs.ashbyhq.com Hyderabad`
   - narrow it with role words: `site:jobs.lever.co Hyderabad "product analyst"`
2. Give the links to the agent. Paste them, or save the search results page and give the file:

   ```bash
   python -m jobfinder add "https://jobs.lever.co/acme/123" "https://boards.greenhouse.io/beta/jobs/9"
   ```

   ```bash
   python -m jobfinder add ~/Downloads/search-results.html
   ```

It finds every Greenhouse, Lever and Ashby link, reads each company's public job list once, shows how many jobs are in your cities, and asks before adding the ones with jobs there. Use `--all` to also add companies with jobs elsewhere. The agent does not search the web by itself, because search engines do not allow automated searching. EU-hosted boards (`jobs.eu.lever.co`, `boards.eu.greenhouse.io`) are not supported yet.

By hand: `boards.greenhouse.io/acme` means platform `greenhouse`, board `acme`; `jobs.lever.co/acme` means `lever`; `jobs.ashbyhq.com/acme` means `ashby`.

### From Y Combinator

```bash
python -m jobfinder discover yc
```

```bash
python -m jobfinder discover yc --remote
```

It reads an unofficial daily copy of Y Combinator's list of hiring companies on your computer (it is not stored in this repo, because it states no license), tries each company's likely board name on Greenhouse, Lever and Ashby, and asks before adding the ones with jobs in India or remote-India. Expect most not to be found: in October 2026, 7 of 50 India-based hiring YC companies used these platforms. A guessed board name can belong to a different company with the same name, which is why only boards with jobs in India are added; check new names in `companies.csv`.

## Add jobs from job-alert emails (LinkedIn)

Many jobs, especially at startups, agencies and large companies, are on sites this agent cannot read. Set up free job alerts there and let the agent screen what they send you.

1. Create LinkedIn job alerts for each track, for example "Associate Product Manager, Hyderabad" and "SEO Executive, Hyderabad".
2. Save the alert emails as `.eml` files in the `inbox/` folder. In Gmail: open the email, then More (⋮) > **Download message**. Do not forward them; forwarding changes the layout.
3. Run as usual. Jobs from the emails go through the same checks, are filed under the right track, and a job in two emails appears once.

Good to know:

- **Only LinkedIn alert emails are understood so far**, tested on one sample. Other senders are listed as "not supported yet"; each needs a sample email before it can be added.
- **Links are cleaned.** Alert links carry tracking codes and one-time login tokens; only the plain job address (such as `https://www.linkedin.com/jobs/view/123/`) is kept.
- **Less detail.** An alert email has no posting date (the email's date is used, so the job may be older) and no description, so only its title can match your roles.

## Which jobs are kept

**First, the rules.** A job is removed when it is older than your `freshness_days`, too senior, abroad (jobs in other cities of your country, `remote_country`, default India, are kept unless you chose to remove them; remote jobs count when they are open to your country), outside your pay range when you chose to remove those, or against a deal-breaker.

**Then, how well it fits.** Every remaining job is scored against what you saved from your resume:

- **Your roles are compared with the job's title.** Only the title: a job whose text says "our recruiter will call you" is not a recruiter job.
- **Your skills are compared with the whole job.**
- **Rare words count more.** A word that few of today's jobs use ("Airflow", "HRBP") counts for a lot; one that most of them use ("communication", equal-opportunity text) counts for almost nothing. This is learned from each day's jobs, so it works for any field with no list to keep up to date. Other spellings from `data/skills.json` count too (PySpark for Spark, HRBP for HR Business Partner).
- **Priority skills count 1.5 times; roles count twice.**

A job is kept when **its title names one of your roles**, or when it **scores at least 45% of today's best match** for the track **and names at least 3 of your skills** (not counting skills found in most of today's jobs).

**Too senior** means a title with a word such as senior, lead, head, director, principal or staff (words inside your own role name, as in "Chief of Staff", do not count), a level mark above your years (II or L2 needs 2 years, III or L3 needs 5), or a posting asking for more years than you set. If you allow 3 or more years, Senior and Sr. titles are kept and judged by the years the posting asks for.

**Stale jobs are remembered** with their date, so they never come back as new. If you later allow older jobs (more `freshness_days`), they are checked again.

**Deal-breakers.** A track that refuses Coding removes posts naming 4 or more different coding terms (Python, Java, backend, "write code", and so on) or with Engineer, Developer or Programmer in the title. Posts naming 2–3 are kept, flagged and ranked lower, since many PM jobs say "SQL a plus". On 105 real PM-type postings (October 2026), 78% named 0–1 coding terms and 3% named 4 or more; on 644 engineering postings, 69% named 4 or more.

## How results are ranked

Results are grouped by track, and Rank restarts at 1 in each track. Inside a track, the best match comes first. A job outside your preferred cities, with pay outside your range, or flagged by a deal-breaker ranks a quarter lower for each, so a strong match in another city still comes before a weak one in yours.

## Reading the CSV

| Column | Meaning |
| --- | --- |
| Rank / Track | Position inside the track (one per profile, such as pm and seo). A job that fits two tracks appears once, in the track whose direction it fits best |
| Matched by | "your role: ..." when the title names one of your roles, or "your skills" |
| Apply link | The exact page where you apply |
| Posted / Date basis | The source's date; "first seen" when it gives none (counting starts on your first run); or the alert email's date |
| Experience bucket | Within your range / Entry-level title (years not stated) / Not stated (review) |
| Priority skills | Your priority skills this job needs |
| Deal-breaker check | Empty, or a flag such as "mentions coding: sql, apis" |
| Match | How well the job fits, where 100 is today's best match for the track |
| Matched skills | Your skills the job names, strongest reason first |
| Missing skills | Skills the job names that are not on your list (from `data/skills.json`), for reading only |
| Location match / Shift | Whether it is in your preferred cities; whether a 10 to 6 shift is mentioned |
| Pay / Pay check | The stated pay, and how it compares with yours |
| Company email / Founder X handle | Only what you typed into `companies.csv` |
| Platform | Where the job came from (greenhouse, lever, ashby, himalayas, remoteok, linkedin) |

## Change your preferences

Run `python -m jobfinder setup` again. Press Enter to keep the value in brackets. Typing `keep` or `yes` is never saved as a role or skill; press Enter instead.

| Question | Setting | Saved in |
| --- | --- | --- |
| Cities you prefer; keep or remove jobs elsewhere | `preferred_locations`, `location_mode` | `settings.json` |
| Most years of experience a job may ask for (`1`, or a range such as `0-1`) | `max_experience_years` | `settings.json` |
| Posted within how many days | `freshness_days` | `settings.json` |
| Pay you want, typed your own way (`8 LPA`, `more than 8 lakhs`, `6-12 LPA`, `up to 10 LPA`, `$80k`), and keep or remove jobs outside it | `min_annual_pay`, `max_annual_pay`, `pay_currency`, `pay_mode` | `settings.json` |
| Your resume (optional on later runs). Read locally; not copied, and its path is not saved | — | — |
| Skills found in your resume, which you confirm or edit in your own words. Items from your resume's skill lists (such as "Tools: Power BI, Tableau") are picked up, including ones in brackets such as "AWS (S3, EC2)"; never your name, email, phone, cities, jobs or education | `skills` | `profiles/<name>.json` |
| Roles found in your resume (such as "Data Engineer" in your job history), which you confirm or edit. A role that looks mistyped ("Data Enginner") is offered the right spelling. Can be empty: the track then matches by skills only | `titles` | `profiles/<name>.json` |
| Deal-breakers: work these jobs must not depend on (Coding), or none | `deal_breakers` | `profiles/<name>.json` |
| Priority skills (optional): what the jobs should mainly be about; they count more. One that is not in your skills list is added to it | `priority_skills` | `profiles/<name>.json` |

Other settings you can edit by hand in `settings.json`: `remote_country` (default India), `remote_sources` (`[]` turns the remote boards off), `starter_companies`, `match_share` (45: % of the day's best match a job needs when its title is not one of your roles), `match_min_skills` (3), `deal_breaker_remove` (4), `deal_breaker_flag` (2), `exclude_title_words` and `request_delay_seconds`. Backup copies named `*.backup.json` in `profiles/` are ignored.

**How pay works.** In `prefer` mode, jobs whose stated pay is outside your range stay on the list but rank lower; in `require` mode they are removed. Jobs that do not state pay are always kept, because most postings do not. Pay is compared only when the posting uses your currency, and it is never converted. Monthly pay is multiplied by 12. Hourly pay, and bare numbers with no sign of a period, are treated as not comparable.

## Privacy: what stays on your computer

Never committed (see `.gitignore`): your resumes, `profiles/` (except `example.json`), `settings.json`, `companies.csv`, `state.json` (the memory of seen and removed jobs), `inbox/` (alert emails, which contain your name and email address), `cache/` and `output/`. Resumes and emails are read locally and never uploaded.

## Known limits

- **Coverage.** Company boards are read only for Greenhouse, Lever and Ashby, and only for the companies on the built-in list and your own. Many companies in India use other systems (Workday, their own career sites) or post only on LinkedIn and Naukri, which this agent cannot read; saved LinkedIn alert emails cover some of that.
- **Dates can mislead.** Greenhouse dates change when a company edits a job, and alert emails have no posting date, so a few older jobs will look fresh.
- **Any field works, but the companies are mostly tech.** The built-in companies hire mostly in tech, product, data, sales and operations, so finance, design or healthcare will find fewer jobs.
- **Matching is word matching.** It finds words, not ideas: "built dashboards" does not count as Power BI, and "talent acquisition" counts as "recruiting" only if `data/skills.json` lists them as spellings of each other. Use the words job posts use. Very short skills (Go, R, C++) are not picked up from resumes, because their letters match ordinary words.
- **Matches by skills alone are a wider net.** On real postings the top matches were right, with some loose ones further down. Raise `match_share` or `match_min_skills` to be stricter.
- **Experience reading is a guess from text.** Check the Experience column.
- **Check companies yourself** before applying, especially internships that ask for a fee.
- **Source terms.** Himalayas and Remote OK ask for credit and links (kept automatically). For Greenhouse, Lever and Ashby, check their terms yourself and keep the request delay.
- **A run takes about 7 minutes**, mostly reading the larger Greenhouse boards one at a time with a polite delay.
- **Not built yet:** other alert-email senders (such as Naukri), extra cities listed on a job, and finding company emails or founder handles.

## Contributing

- Add other spellings of skills and roles in `data/skills.json`. Users never need to; it only helps a job count when it words a skill differently from a resume.
- Add companies to `data/companies-starter.csv`: only ones whose public board has jobs in India or remote-India.
- Add a job platform: copy `jobfinder/sources/lever.py`, return `Job` objects, and register it in `jobfinder/sources/__init__.py`.
- Add tests with fake data only, and keep them offline. Never commit real resumes, emails or personal data.

## License

Copyright 2026 Jagruthie Sadula. Licensed under the [Apache License, Version 2.0](LICENSE): you may use, change and share this code, including commercially, as long as you keep the license and copyright notice and say which files you changed. It comes with no warranty.

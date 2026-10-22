# Job Finder Agent

Finds **fresh** jobs on company career pages, keeps the ones that match the skills on your resume, and writes a CSV with the exact link to apply.

It reads only public job lists that Greenhouse, Lever and Ashby publish for company career pages. It does not scrape LinkedIn, does not use paid services, and does not send anything on your behalf.

> **Status: version 0.1.** The logic is tested with fake jobs (`python -m unittest discover -s tests`). It has **not yet been run against live career pages**, so expect to fix a field name or two on the first real run.

## What it does

1. Asks for your resume and reads it on your own computer. It lists the skills it finds.
2. **You confirm or edit those skills.** Then it lists the roles that need those skills, grouped by category (for example Product & Strategy, SEO & Content), with the reasons. You pick the categories and confirm the roles. This is the confirm step.
   - **Two interests?** Each profile is one *track*. Give two resumes (for example `pm` and `seo`), or give one resume and split the other category into its own track when setup offers it.
3. Fetches open jobs for the companies in your `companies.csv`.
4. Removes jobs older than 7 days and remembers them, so they never return as new.
5. Keeps the jobs that fit you, in two ways:
   - **by role title:** the title matches a role you chose (any city; other cities rank lower), or
   - **by skills:** the title is not on your list, but the job asks for at least 3 of your specific skills, and you have at least half of the skills it mentions. These must be in your cities. Skills found in most postings (hiring notices, "communication") and skills in the company's own name do not count here.
   Either way, jobs asking for more experience than you set are removed (jobs that do not state it go in a review bucket).
6. Shows its reasons: which skills matched, which are missing, and whether a job was kept for its title or its skills.
7. Writes `output/jobs-<date>.csv`, best matches first.

## Try it first on fake jobs

```bash
python -m jobfinder demo
```

This runs every profile in `profiles/` (or the ones you name with `--profiles`) against 12 invented jobs built to trigger each rule (fresh, stale, too senior, experience not stated). Nothing is fetched and no memory is saved. It writes `output/demo-<date>.csv`.

## Quick start

```bash
pip install -r requirements.txt

# 1. Answer a few questions and give your resume (drag the file into the terminal).
#    You confirm the skills it finds, then the roles that need them.
#    One profile per resume, for example "pm" and "seo".
python -m jobfinder setup

# 2. Add companies hiring in your city (see "Find companies hiring near you" below)
python -m jobfinder add "https://jobs.lever.co/acme/123"

# 3. Run it (daily is a good rhythm)
python -m jobfinder run          # every profile in profiles/, one track each
```

On Windows, use `copy` instead of `cp`.

## Where jobs come from

Every run uses your saved filters on all of these, so a rerun repeats the same search with fresh jobs:

| Source | What it is | Follows your filters how |
| --- | --- | --- |
| Starter company list (`data/companies-starter.csv`) | About 20 companies checked to have jobs in India or remote-India on their public Greenhouse, Lever or Ashby boards | All their jobs are read; your city, role, skills and experience filters pick the right ones. Turn off with `"starter_companies": false` |
| Your companies (`companies.csv`) | Companies you or `add` / `discover` added | Same as above |
| Remote job boards: [Himalayas](https://himalayas.app) and [Remote OK](https://remoteok.com) | Free public feeds of remote jobs | Used when "Remote" is one of your places. Only jobs open to your `remote_country` (default India) or worldwide are kept |
| Job-alert emails in `inbox/` | LinkedIn alerts you saved | The alert's own search, then your filters on top |

**Remote job boards.** Himalayas is searched for jobs open to your country, newest first; Remote OK's feed is filtered to jobs that clearly say worldwide, India, Asia or APAC (half its jobs give no location, so those are skipped). Each source is downloaded at most once a day and kept in `cache/`, as they ask. Their apply links are kept unchanged and the Platform column names the source, which is how their terms ask to be credited. Not used: Remotive (its free feed returns only 10 jobs; the full API is paid) and Product Hunt (its API needs an account, has no jobs, and is non-commercial by default).

## Find startups from Y Combinator

```bash
python -m jobfinder discover yc            # YC companies that are hiring and located in India
python -m jobfinder discover yc --remote   # also YC companies that hire remotely (slower)
```

It reads an unofficial daily copy of Y Combinator's list of hiring companies (on your computer; the list is not stored in this repo, because it states no license), tries each company's likely board name on Greenhouse, Lever and Ashby, shows what it finds, and asks before adding the ones with jobs in India or remote-India to `companies.csv`.

Expect most YC companies not to be found: in October 2026, 7 of 50 India-based hiring YC companies used these three platforms; the rest use other systems or YC's own job pages, which this agent cannot read. A guessed board name can belong to a different company with the same name, which is why only boards with jobs in India are added; check new names in `companies.csv`.

## Find companies hiring near you (location first)

Guessing company names finds few jobs in your city. It works much better to start from jobs that are already in your city, then add the companies behind them.

1. Search the web yourself, for example (change the city and words):
   - `site:jobs.lever.co Hyderabad`
   - `site:boards.greenhouse.io Hyderabad` and `site:job-boards.greenhouse.io Hyderabad`
   - `site:jobs.ashbyhq.com Hyderabad`
   - add role words to narrow it: `site:jobs.lever.co Hyderabad "product analyst"`
2. Give the links to the agent. Paste them, or save the search results page and give the file:

   ```bash
   python -m jobfinder add "https://jobs.lever.co/acme/123" "https://boards.greenhouse.io/beta/jobs/9"
   python -m jobfinder add ~/Downloads/search-results.html
   ```

   It finds every Greenhouse, Lever and Ashby link, reads each company's public job list once, shows how many jobs are in your cities, and asks before adding the ones with jobs there to `companies.csv`. Use `--all` to also add companies with jobs elsewhere.

The agent does not run web searches by itself, because search engines do not allow automated searching. Only Greenhouse, Lever and Ashby links can be added; EU-hosted boards (`jobs.eu.lever.co`, `boards.eu.greenhouse.io`) are not supported yet.

You can also add a company by hand: `boards.greenhouse.io/acme` means platform `greenhouse`, board `acme`. `jobs.lever.co/acme` means `lever`. `jobs.ashbyhq.com/acme` means `ashby`.

## Add jobs from job-alert emails (LinkedIn)

Many jobs, especially at startups, agencies and large companies, are on job sites this agent cannot read. Set up free job alerts there, then let the agent screen what they send you.

1. Create job alerts on LinkedIn for each track (for example "Associate Product Manager, Hyderabad" and "SEO Executive, Hyderabad").
2. Save the alert emails as `.eml` files in the `inbox/` folder. In Gmail: open the email, then More (⋮) > **Download message**. Do not forward them; forwarding changes the layout.
3. Run as usual: `python -m jobfinder run`. Jobs from the emails go through the same role, entry-level, location and freshness checks, are filed under the right track, and the same job in two emails appears once.

Good to know:

- **Only LinkedIn alert emails are understood so far.** Emails from other senders are listed as "not supported yet". Other sites (Naukri, Cutshort, Instahyre, Wellfound) need a sample email each before they can be added.
- **Links are cleaned.** Alert links carry tracking codes and one-time login tokens. The agent keeps only the plain job address, such as `https://www.linkedin.com/jobs/view/123/`.
- **Dates and skills are limited.** An alert email does not say when a job was posted, so the email's date is used (the job may be older). It also has no job description, so Fit % is usually empty for these jobs.
- **Privacy.** `inbox/` is in `.gitignore`: these emails contain your name and email address and stay on your computer. Everything is read locally.

## Reading the CSV

| Column | Meaning |
| --- | --- |
| Rank / Track | Results are grouped by track (one per profile, such as pm and seo), best first inside each track. Rank restarts at 1 for each track. A job that fits two tracks appears once, in the track whose skills fit it better |
| Apply link | The exact page where you apply |
| Posted / Date basis | The platform's date, or "first seen" when the platform gives none. First-seen dates start counting on your first run, so their real age is unknown |
| Experience bucket | "Within your range": the posting states years that fit your setting. "Entry-level title (years not stated)": no years given, but the title says Associate, Junior, Trainee, Graduate or "I" (as in Product Manager I). "Not stated (review)": no years and no sign either way, so read it. Ranked in that order. Titles marked II, III or IV are removed as above entry level |
| Matched by | "role title", or "skills: ..." with the specific skills that earned a job its place when its title is not on your list. Title matches rank above skills matches |
| Fit % | Share of the skills the job mentions that are on your profile. Built-in skills (`data/skills.json`) and your own skills (matched by their exact words) both count |
| Missing skills | Skills the job mentions that are not on your profile |
| Location match / Shift | Preferences only. They raise a job's rank and never remove it |
| Company email / Founder X handle | Only what you typed into `companies.csv` |

## Change your preferences

Run `python -m jobfinder setup`. It asks a few questions in the terminal and saves your answers. Press Enter to keep the value in brackets. Typing `keep` or `yes` is not saved as a role or skill; press Enter instead.

| Question | Setting | Where it is saved |
| --- | --- | --- |
| Your resume (optional on later runs). Read locally; the file is not copied and its path is not saved | `skills` | a profile file in `profiles/` |
| Skills: the ones found in your resume, which you confirm or edit. Besides the built-in list, it picks up items from your resume's own skill lists (such as "Tools: Power BI, Tableau"), never your name, email, phone, cities or education | `skills` | each profile file in `profiles/` |
| Categories this track searches, when your skills fit roles in more than one. The rest can become a separate track | — | a new profile file |
| Roles: the ones that need 2 or more of your skills, which you confirm or edit | `titles` | each profile file in `profiles/` |
| Cities | `preferred_locations`, `location_mode` | `settings.json` |
| Most years of experience a job may ask for (`1`, or a range such as `0-1`) | `max_experience_years` | `settings.json` |
| Posted within how many days | `freshness_days` | `settings.json` |
| Pay you want, typed your own way, such as `8 LPA`, `more than 8 lakhs`, `6-12 LPA`, `up to 10 LPA` or `$80k` | `min_annual_pay`, `max_annual_pay`, `pay_currency`, `pay_mode` | `settings.json` |

Roles can be left empty for a track that should match by skills only. You can also edit these files by hand. Other settings: `exclude_title_words`, `request_delay_seconds`, `remote_country` (default India), `match_by` (`titles_or_skills`, or `titles` to keep only listed roles), `skill_match_min` (default 3) and `skill_match_share` (default 50). Backup copies named `*.backup.json` in `profiles/` are ignored.

**How pay works.** `setup` repeats back what it understood. In `prefer` mode, jobs whose stated pay is outside your range stay on the list but rank lower. In `require` mode they are removed. Jobs that do not state pay are always kept, because most postings do not. Pay is only compared when the posting uses your currency, and it is never converted. Monthly pay is multiplied by 12. Hourly pay, and bare numbers with no sign of a period, are treated as not comparable, because guessing would wrongly remove good jobs.

## How results are ranked

In this order: experience stated and within your range first, then pay inside your range, then your preferred cities, then a mention of a 10 to 6 shift, then the skill match. Jobs with no stated pay sit between jobs inside and outside your range.

Every run starts by printing the roles each profile is looking for. If a profile has no roles, the run stops with a message instead of matching every title.

## Privacy: what stays on your computer

Never committed (see `.gitignore`): your resumes, your profiles, `settings.json`, `companies.csv`, `state.json` (the memory of seen and removed jobs) and everything in `output/`. Resumes are read locally and never uploaded.

## Known limits

- **Coverage.** Only companies on Greenhouse, Lever or Ashby can be read, and only the ones you list.
- **Dates can mislead.** Greenhouse and Ashby dates can change when a company edits or reposts an old job, so a few stale jobs will look fresh.
- **Skills matching is word matching.** It finds exact words, not ideas: "built dashboards" does not count as Power BI. Very short skills (Go, R, C++) are skipped because their letters match ordinary words. Add aliases in `data/skills.json` to help.
- **Broad skills never keep a job by themselves.** Skills listed as `generic_skills` in `data/skills.json` (research, communication, Excel and similar) count for Fit % only. In a track with roles, a job kept for its skills must also need at least 2 specific skills those roles need, so a PM track does not pick up developer jobs because you know Python and SQL. Skills in `exact_case` (such as GEO) match only in capitals.
- **Matching by skills is a wider net.** On real postings it mostly found sensible leads, but some are loose. If it brings in jobs you do not want, raise `skill_match_min`, or set `match_by` to `titles`.
- **Experience reading is a guess from text.** Check the Experience column.
- **Platform rules.** The platforms' API docs state no usage terms, but check their terms yourself, and keep the request delay.
- **Not built yet:** finding companies automatically, and finding company emails and founder handles automatically.

## Contributing

- Add skills, aliases or role titles in `data/skills.json`. Give each role a `category` so setup can group it into a track.
- Add a new job platform: copy `jobfinder/sources/lever.py`, return `Job` objects, and register it in `jobfinder/sources/__init__.py`.
- Add tests with fake data only. Never commit real resumes or personal data.

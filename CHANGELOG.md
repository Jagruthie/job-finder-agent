# Changelog

## Unreleased

### Added

- A local web page, opened with `python -m jobfinder ui`, with Today, New jobs, Tracker, Companies and Review screens. It runs only on your computer and uses the same core as the terminal.
- Add many companies on the page from a CSV file or pasted links. Each one is checked first, and only the ones you tick join your list.
- `python -m jobfinder shortcut` makes a desktop icon, so daily use needs no typing in a terminal.
- On a Mac, `python -m jobfinder autostart on` keeps the page running in the background, so you can pin it as a Safari tab. `autostart off` removes it.
- Tick-boxes on New jobs for the optional sources on that one check.
- A Learn tab: things to learn for each job, with a month calendar of apply deadlines, interviews, follow-ups and learn-by dates. Adding a match to the tracker lists the skills its posting names that your profile does not have, and saving a resume change lists the new skills you added. Each item shows the job's interview or deadline to apply.
- `apply_deadline` and `interview_date` columns in the tracker. Older tracker files keep working.

### Changed

- The message for the tracker's "applied" rule now says it in plain words.
- A mistyped date in `tracker.csv` no longer stops `tracker due` or `today`. That entry is left out of those lists.

## 0.2.0 (unreleased)

Freshlist now watches a fixed list of company career pages and shows only new matching jobs, with a tracker for applications and resume changes.

### Added

- Watch-list in `companies.csv`, with `companies add`, `list`, `set-tier`, `set-status`, `import` and `migrate`.
- New-job memory: `run` shows each matching job once and returns the companies it could not read.
- `tracker` commands, plus export to `.xlsx`. An entry cannot be marked applied if a resume change has no explanation.
- `today`, a checklist for an optional two-day rotation.
- `review`, a weekly report with quiet-company flags and a checkpoint. It only suggests and changes nothing.
- A core library that a future UI can call, described in `ARCHITECTURE.md`. Settings are in one config file, and data files are written safely.

### Changed

- `run` reads only your watch-list by default.
- The built-in company list, remote job boards, LinkedIn alert emails and `discover yc` still work, but are optional and off by default.
- `add` now saves to the new watch-list columns.
- Greenhouse jobs use their first-published date, so editing an old job no longer makes it look fresh.
- `setup` saves only the settings it asks about, and keeps anything you set by hand.

### Upgrading

Run `python -m jobfinder companies migrate` once to convert an older `companies.csv`. A backup of the old file is kept. `state.json` is no longer used, and the new memory starts empty, so your first run may show many jobs as new.

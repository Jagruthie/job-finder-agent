# Architecture

Freshlist has two layers, so a web page can later use the same logic as the terminal.

```
  Terminal layer                    Core library                           Files
  jobfinder/cli.py   ─── calls ──▶  jobfinder/core/      ─── uses ──▶      jobfinder/storage.py
  jobfinder/setup.py                  watchlist, runner, tracker,            companies.csv, tracker.csv, learning.csv,
  (parse, ask, print)                 today, review, discovery, profiles     seen_jobs.json, last_run.json,
                                    jobfinder/pipeline.py, filters.py,       run_log.json
  A future web UI    ─── calls ──▶    match.py, sources/
```

## The rules of each layer

**Core library** (everything except `cli.py`, `setup.py` and `__main__.py`)

- Never prints, asks for input or exits the program. A test (`LayerTests` in `tests/test_core.py`) enforces this.
- Takes plain arguments and returns dictionaries, lists or dataclasses that a screen can show as tables.
- Reports something the person can fix by raising an error from `jobfinder/errors.py`, with a readable message.
- Reports partial problems as data. For example, `RunResult.failed` lists companies that could not be read.

**Terminal layer** (`cli.py`, `setup.py`)

- Parses commands, asks questions, calls the core and prints what comes back. It holds no job-search logic.

## Where things live

| Thing | Place |
| --- | --- |
| Every default path, number and allowed value | `jobfinder/config.py` |
| A person's own overrides | `settings.json`, loaded by `config.load_config` |
| All reading and writing of data files | `jobfinder/storage.py`. Writes go to a temporary file, which then replaces the real one, so a crash cannot leave half a file |

## Stable ids

- A job's id is `platform:board:job id` from its source, such as `lever:acme:123`.
- A tracker entry gets a random id such as `t-1a2b3c4d` when it is created. Editing or sorting rows never changes it.
- A match's number is only a shortcut for the day it was found. The numbers stay the same for later runs that day.

## The web UI

`jobfinder/web/` is a small Flask app, opened with `python -m jobfinder ui`. It listens only on this computer and holds no job-search logic. A run goes through `web/background.py`, one at a time, and the company list can't change until it finishes. Each screen loads the config and calls the same functions the terminal does:

| Screen | Call |
| --- | --- |
| Run, with progress per company | `core.runner.run(cfg, profiles, progress=callback)`. The callback gets `{company, platform, ok, jobs, error, done, total}` |
| Watch-list table | `core.watchlist.list_companies`, `add`, `set_tier`, `set_status`, `import_csv` |
| Add many from a file or links | `core.watchlist.parse_plain` or `parse_links`, then `prepare`, `core.discovery.check_file`, and `watchlist.add_rows` for what you pick |
| Tracker table | `core.tracker.list_entries`, `add`, `add_from_run`, `update`, `due`, `export_xlsx` |
| Learn page and calendar | `core.agenda.month_view`, `jobs_to_prepare`, and `core.learning.add`, `set_done`, `remove` |
| Today page | `core.today.checklist` |
| Review page | `core.review.review` |

Things to know when building it:

- `runner.run` takes minutes and calls the progress callback from worker threads, so run it in the background.
- The storage module has no locking, because the terminal runs one command at a time. A UI should allow one write at a time.
- Catch `errors.FreshlistError` and show its message. `tracker.update` saves nothing when it raises.

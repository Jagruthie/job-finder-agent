"""Every file path, number and allowed value Freshlist uses, in one place.

Change a default here. A person's own choices go in settings.json, which overrides these (see load_config).
"""
from __future__ import annotations

from pathlib import Path

from .errors import ConfigError
from .storage import read_json
from .textutil import clean_locations

DEFAULT_SETTINGS = {
    # Where your files live. Every path below is inside data_dir.
    "data_dir": ".",
    "companies_file": "companies.csv",
    "tracker_file": "tracker.csv",
    "seen_file": "seen_jobs.json",      # jobs already shown, and the dates needed to judge freshness
    "last_run_file": "last_run.json",   # the numbered matches from the last run
    "run_log_file": "run_log.json",     # the first live run and every match, for the review command
    "profiles_dir": "profiles",
    "output_dir": "output",
    "inbox_dir": "inbox",
    "cache_dir": "cache",

    # Which jobs count.
    "freshness_days": 7,
    "max_experience_years": 1,
    "preferred_locations": ["Remote"],   # setup asks for your own cities
    "location_mode": "prefer",          # "prefer" ranks higher, "require" drops other places
    "remote_country": "India",          # "Remote" in your cities means remote in this country (empty: anywhere)
    # Deal-breakers (per profile, such as Coding): a post naming this many different terms of it is removed,
    # and one naming at least `deal_breaker_flag` of them is kept but flagged and ranked lower.
    "deal_breaker_remove": 4,
    "deal_breaker_flag": 2,
    "pay_currency": "INR",              # INR or USD; pay in another currency is never compared
    "min_annual_pay": None,             # full yearly amount, e.g. 300000 for 3 LPA. None means no minimum
    "max_annual_pay": None,             # optional upper end of your range. None means no maximum
    "pay_mode": "prefer",               # "prefer" ranks outside-range jobs lower, "require" drops jobs whose stated pay is outside your range
    "exclude_title_words": ["senior", "sr", "lead", "head", "director", "vp", "principal", "staff"],
    # A job whose title is not one of your roles is kept when it scores at least this % of the day's best match
    # for the track and names at least this many of your skills.
    "match_share": 45,
    "match_min_skills": 3,

    # Being polite to job sites: seconds between two requests to the same site.
    "request_delay_seconds": 1.5,

    # Optional sources, all off by default. The run command reads only your watch-list unless you turn one on.
    "use_starter_companies": False,     # also read data/companies-starter.csv
    "use_remote_boards": False,         # also read remote_sources, when "Remote" is one of your cities
    "remote_sources": ["himalayas", "remoteok"],
    "use_alert_emails": False,          # also read saved LinkedIn job-alert emails from inbox_dir
    "use_yc_discovery": False,          # allow the 'discover yc' command

    # Tracker.
    "followup_days": 7,                 # followup_date = applied_date + this many days
    "resume_versions": ["pm", "seo", "hybrid", "custom"],

    # The two-day rotation.
    "outreach_window_days": 2,          # Day B lists applications from the last this many days without outreach
    "linkedin_scan_minutes": 30,

    # Review.
    "quiet_after_days": 14,             # flag an active company with no match for this many days
    "review_recent_days": 7,            # the "this week" count in the review
    "checkpoint_days": 14,              # the checkpoint looks at this many days from the first live run...
    "checkpoint_min_matches": 3,        # ...and warns if it found fewer live matches than this
}

# Allowed values.
PLATFORMS = ("greenhouse", "lever", "ashby")            # the platforms Freshlist can read
COMPANY_PLATFORMS = PLATFORMS + ("other",)              # "other" is listed but cannot be read
TIERS = ("watch", "deep")
COMPANY_STATUSES = ("active", "quiet", "swapped")
TRACKER_SOURCES = ("freshlist", "linkedin", "founder-outreach", "referral")
TRACKER_STATUSES = ("to_apply", "applied", "interview", "offer", "rejected", "closed", "skipped")
APPLIED_STATUSES = ("applied", "interview", "offer", "rejected")   # these mean you have applied
OUTREACH_STATUSES = ("not_started", "sent", "replied", "not_needed")

STARTER_COMPANIES = Path(__file__).resolve().parent.parent / "data" / "companies-starter.csv"


def file_path(cfg: dict, key: str) -> Path:
    """The full path of a file or folder named in the settings, such as file_path(cfg, "tracker_file")."""
    return Path(cfg["data_dir"]) / cfg[key]


def load_config(settings_path=None, data_dir=None) -> dict:
    """The defaults above with settings.json on top. `settings_found` says whether the file was there."""
    folder = Path(data_dir) if data_dir else Path(DEFAULT_SETTINGS["data_dir"])
    path = Path(settings_path) if settings_path else folder / "settings.json"
    saved = read_json(path)
    if saved is not None and not isinstance(saved, dict):
        raise ConfigError(f"{path} should hold settings such as {{\"freshness_days\": 7}}.")
    cfg = {**DEFAULT_SETTINGS, **{k: v for k, v in (saved or {}).items() if not k.startswith("_")}}
    if data_dir:
        cfg["data_dir"] = str(data_dir)
    cfg["preferred_locations"] = clean_locations(cfg.get("preferred_locations")) or list(
        DEFAULT_SETTINGS["preferred_locations"])
    cfg["settings_path"], cfg["settings_found"] = str(path), saved is not None
    return cfg

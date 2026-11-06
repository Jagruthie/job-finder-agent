"""The tracker: one list of every application from every source, kept in tracker.csv.

Whatever you add to your resume for a job also goes on that job's Learn list (see learning.py).

The rule that matters: you cannot mark an entry applied when changes_made is filled and how_i_would_explain_it is
empty. Every resume change must be something you can explain in an interview.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

from .. import storage
from ..config import APPLIED_STATUSES, OUTREACH_STATUSES, TRACKER_SOURCES, TRACKER_STATUSES, file_path
from ..errors import NotFoundError, ValidationError
from . import learning, runner

COLUMNS = ["id", "company", "role", "apply_link", "posted_date", "source", "resume_version", "changes_made",
           "how_i_would_explain_it", "founder_x_handle", "company_email", "applied_date", "outreach_status",
           "followup_date", "status", "notes", "apply_deadline", "interview_date"]
_DATES = ("posted_date", "applied_date", "followup_date", "apply_deadline", "interview_date")


def read(cfg: dict) -> List[dict]:
    _, rows = storage.read_csv(file_path(cfg, "tracker_file"))
    return [{**{column: "" for column in COLUMNS}, **row} for row in rows]


def _save(cfg: dict, rows: List[dict]) -> None:
    storage.write_csv(file_path(cfg, "tracker_file"), COLUMNS, rows)


def _shows_applied(entry: dict) -> bool:
    return entry["status"] in APPLIED_STATUSES or bool(entry["applied_date"])


def rule_problem(entry: dict) -> str:
    """Why this entry breaks the explain-your-changes rule, or empty."""
    if _shows_applied(entry) and entry["changes_made"].strip() and not entry["how_i_would_explain_it"].strip():
        return (f"{entry['company']} - {entry['role']} cannot be marked applied: you noted a resume change but not how "
                "you would explain it. Write how you would explain the change in an interview first.")
    return ""


def _validate(entry: dict, cfg: dict) -> None:
    for column in ("company", "role"):
        if not entry[column].strip():
            raise ValidationError(f"A tracker entry needs a {column}.")
    allowed = {"source": TRACKER_SOURCES, "resume_version": cfg["resume_versions"], "status": TRACKER_STATUSES,
               "outreach_status": OUTREACH_STATUSES}
    for column, values in allowed.items():
        if entry[column] and entry[column] not in values:
            raise ValidationError(f"{column} must be one of {', '.join(values)}, not '{entry[column]}'.")
    if not entry["source"]:
        raise ValidationError(f"A tracker entry needs a source: {', '.join(TRACKER_SOURCES)}.")
    for column in _DATES:
        if entry[column]:
            try:
                date.fromisoformat(entry[column])
            except ValueError:
                raise ValidationError(f"{column} must be a date written as YYYY-MM-DD, not '{entry[column]}'.")
    problem = rule_problem(entry)
    if problem:
        raise ValidationError(problem)


def _settle_dates(entry: dict, changes: dict, before: dict, cfg: dict, today: date) -> None:
    """Keep status, applied_date and followup_date consistent. Follow-up is applied_date + followup_days, unless
    you set it yourself in the same change."""
    if changes.get("status") == "to_apply" and "applied_date" not in changes:
        entry["applied_date"] = ""              # taking back an application
    if entry["applied_date"] and entry["status"] == "to_apply":
        entry["status"] = "applied"
    if entry["status"] in APPLIED_STATUSES and not entry["applied_date"]:
        if before.get("status") in APPLIED_STATUSES:
            raise ValidationError("applied_date cannot be empty while the status says you applied.")
        entry["applied_date"] = today.isoformat()
    if entry["applied_date"] != before.get("applied_date", "") and "followup_date" not in changes:
        entry["followup_date"] = ""
        if entry["applied_date"]:
            try:
                applied = date.fromisoformat(entry["applied_date"])
            except ValueError:
                raise ValidationError(f"applied_date must be a date written as YYYY-MM-DD, not '{entry['applied_date']}'.")
            entry["followup_date"] = (applied + timedelta(days=cfg["followup_days"])).isoformat()


def _check_fields(fields: dict) -> None:
    unknown = [key for key in fields if key not in COLUMNS or key == "id"]
    if unknown:
        raise ValidationError(f"Unknown tracker field: {', '.join(unknown)}.")


def _learn_from_resume_change(cfg: dict, entry: dict) -> None:
    """Put what you added to your resume for this job on its Learn list. Skills your profiles already have are left
    out, since you had them before."""
    from ..vocab import Vocab
    from . import profiles
    loaded, _ = profiles.load(profiles.find_paths(cfg))
    known = [skill for profile in loaded for skill in profile.get("skills", [])]
    learning.add_from_resume(cfg, entry["id"], learning.topics_from_resume_change(entry["changes_made"], known, Vocab.load()))


def add(cfg: dict, today: Optional[date] = None, **fields) -> dict:
    """Add an entry from any source. Returns it, with its id. The same apply link cannot be added twice."""
    _check_fields(fields)
    rows = read(cfg)
    today = today or date.today()
    values = {key: str(value).strip() for key, value in fields.items()}
    entry = {**{column: "" for column in COLUMNS}, "status": "to_apply", "outreach_status": "not_started", **values}
    link = entry["apply_link"].lower()
    if link and any(row["apply_link"].lower() == link for row in rows):
        raise ValidationError(f"This apply link is already in your tracker: {entry['apply_link']}")
    entry["id"] = f"t-{uuid.uuid4().hex[:8]}"
    _settle_dates(entry, values, {}, cfg, today)
    _validate(entry, cfg)
    _save(cfg, rows + [entry])
    _learn_from_resume_change(cfg, entry)
    return entry


def add_from_run(cfg: dict, number: int, today: Optional[date] = None, **fields) -> dict:
    """Add a match from the last run by its number. Skills the posting names that your profile does not have go on
    the job's list of things to learn."""
    last = runner.last_run(cfg)
    match = next((m for m in last.get("matches", []) if m["number"] == number), None)
    if match is None:
        raise NotFoundError(f"There is no match number {number} in the last run ({last.get('date') or 'no run yet'}).")
    from_match = {"company": match["company"], "role": match["role"], "apply_link": match["apply_link"],
                  "posted_date": match["posted_date"], "source": "freshlist"}
    if match["track"] in cfg["resume_versions"]:
        from_match["resume_version"] = match["track"]
    entry = add(cfg, today=today, **{**from_match, **fields})
    missing = match.get("details", {}).get("Missing skills", "")
    learning.add_from_job(cfg, entry["id"], missing.split(","))
    return entry


def update(cfg: dict, entry_id: str, today: Optional[date] = None, **changes) -> dict:
    """Change an entry. All the rules are checked again on the result, and nothing is saved if one fails."""
    _check_fields(changes)
    rows = read(cfg)
    entry = next((row for row in rows if row["id"] == entry_id), None)
    if entry is None:
        raise NotFoundError(f"No tracker entry with id '{entry_id}'.")
    before = dict(entry)
    values = {key: str(value).strip() for key, value in changes.items()}
    entry.update(values)
    _settle_dates(entry, values, before, cfg, today or date.today())
    _validate(entry, cfg)
    _save(cfg, rows)
    if entry["changes_made"] != before["changes_made"]:
        _learn_from_resume_change(cfg, entry)
    return entry


def list_entries(cfg: dict, status: str = "") -> List[dict]:
    return [row for row in read(cfg) if not status or row["status"] == status]


def logged_links(cfg: dict) -> set:
    """Every apply link already in the tracker, in lower case."""
    return {row["apply_link"].lower() for row in read(cfg) if row["apply_link"]}


def problems(cfg: dict) -> List[dict]:
    """Entries breaking the rule because the file was edited by hand: {"id", "problem"}."""
    return [{"id": row["id"], "problem": rule_problem(row)} for row in read(cfg) if rule_problem(row)]


def due(cfg: dict, today: Optional[date] = None) -> List[dict]:
    """Applications whose follow-up date has arrived, oldest first, each with days_overdue."""
    today = today or date.today()
    found = []
    for row in read(cfg):
        try:
            followup = date.fromisoformat(row["followup_date"]) if row["followup_date"] else None
        except ValueError:    # tracker.csv can be edited by hand; one mistyped date must not hide the rest
            continue
        if row["status"] == "applied" and followup and followup <= today:
            found.append({**row, "days_overdue": (today - followup).days})
    return sorted(found, key=lambda row: row["followup_date"])


def export_xlsx(cfg: dict, path: Optional[str] = None, status: str = "") -> str:
    """Write the tracker to an Excel file and return its path. tracker.csv stays the source of truth."""
    try:
        from openpyxl import Workbook
    except ImportError:
        raise ValidationError("Exporting to .xlsx needs openpyxl. Run: pip install openpyxl")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Tracker"
    sheet.append(COLUMNS)
    for row in list_entries(cfg, status):
        sheet.append([row.get(column, "") for column in COLUMNS])
    for cells in sheet.iter_rows():
        for cell in cells:
            cell.data_type = "s"     # text from job pages is never run as an Excel formula
    sheet.freeze_panes = "A2"
    target = Path(path) if path else Path(file_path(cfg, "output_dir")) / "tracker.xlsx"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    workbook.save(temp)
    temp.replace(target)
    return str(target)

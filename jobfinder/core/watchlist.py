"""The watch-list: the fixed list of company career pages kept in companies.csv."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from .. import storage
from ..config import COMPANY_PLATFORMS, COMPANY_STATUSES, PLATFORMS, TIERS, file_path
from ..discover import extract_boards
from ..errors import NotFoundError, StorageError, ValidationError

COLUMNS = ["company", "careers_url", "platform", "tier", "status", "added_date", "last_match_date", "notes"]
MAX_COMPANIES_IN_FILE = 200     # each one means requests to job sites, so a huge file is refused rather than read
_NAME_COLUMNS = {"company", "name", "company name", "company_name", "organisation", "organization"}
_LINK_COLUMNS = {"careers_url", "careers url", "careers_page", "careers page", "career page", "careers link",
                 "careers", "url", "link"}
_WEBSITE_COLUMNS = {"website", "site", "homepage", "domain"}
_BOARD_PAGES = {"greenhouse": "https://job-boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
                "ashby": "https://jobs.ashbyhq.com/{}"}


def detect_platform(url: str) -> Tuple[str, str]:
    """(platform, board) from a careers link. A page on any other system is ("other", "")."""
    boards = extract_boards(url or "")
    return boards[0] if boards else ("other", "")


def board_of(company: dict) -> str:
    """The name a readable platform knows this company by, taken from its careers link."""
    platform, board = detect_platform(company.get("careers_url", ""))
    return board if platform == company.get("platform") else ""


def careers_url_for(platform: str, board: str) -> str:
    return _BOARD_PAGES[platform].format(board)


def read(cfg: dict) -> List[dict]:
    path = file_path(cfg, "companies_file")
    columns, rows = storage.read_csv(path)
    if "board" in columns and "careers_url" not in columns:
        raise StorageError(f"{path} uses the older format (name, platform, board). "
                           "Run 'python -m jobfinder companies migrate' to convert it; a backup is kept.")
    return [{**{column: "" for column in COLUMNS}, **row} for row in rows]


def needs_migration(cfg: dict) -> bool:
    """True when companies.csv still uses the older columns, so migrate has to run before it can be read."""
    columns, _ = storage.read_csv(file_path(cfg, "companies_file"))
    return "board" in columns and "careers_url" not in columns


def _save(cfg: dict, rows: List[dict]) -> None:
    storage.write_csv(file_path(cfg, "companies_file"), COLUMNS, rows)


def _clean_url(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or "." not in parsed.netloc:
        raise ValidationError(f"'{url}' does not look like a careers page link, such as https://jobs.lever.co/acme.")
    return url


def _check_choice(label: str, value: str, allowed: Iterable[str]) -> str:
    if value not in allowed:
        raise ValidationError(f"{label} must be one of {', '.join(allowed)}, not '{value}'.")
    return value


def new_row(url: str, name: str, tier: str, status: str, notes: str, today: date) -> dict:
    url = _clean_url(url)
    platform, board = detect_platform(url)
    if not name:
        name = board.replace("-", " ").replace("_", " ").title() if board else urlparse(url).netloc.replace("www.", "")
    return {"company": name, "careers_url": url, "platform": _check_choice("platform", platform, COMPANY_PLATFORMS),
            "tier": _check_choice("tier", tier or "watch", TIERS),
            "status": _check_choice("status", status or "active", COMPANY_STATUSES),
            "added_date": today.isoformat(), "last_match_date": "", "notes": notes}


def duplicate_of(row: dict, rows: List[dict]) -> Optional[dict]:
    board = board_of(row)
    for other in rows:
        same_board = board and other["platform"] == row["platform"] and board_of(other) == board
        if same_board or other["careers_url"].lower() == row["careers_url"].lower() \
                or other["company"].lower() == row["company"].lower():
            return other
    return None


def add(cfg: dict, url: str, name: str = "", tier: str = "watch", status: str = "active", notes: str = "",
        today: Optional[date] = None) -> dict:
    """Add one company. The platform comes from the link; a page that is not Greenhouse, Lever or Ashby is kept
    as "other", which is listed but cannot be read."""
    rows = read(cfg)
    row = new_row(url, name, tier, status, notes, today or date.today())
    duplicate = duplicate_of(row, rows)
    if duplicate:
        raise ValidationError(f"Already on your watch-list as '{duplicate['company']}'.")
    _save(cfg, rows + [row])
    return row


def add_many(cfg: dict, companies: List[Tuple[str, str]], today: Optional[date] = None) -> dict:
    """Add (name, link) pairs. Returns {"added": rows, "skipped": [{"company", "reason"}]}."""
    rows = read(cfg)
    added, skipped = [], []
    for name, url in companies:
        try:
            row = new_row(url, name, "watch", "active", "", today or date.today())
        except ValidationError as error:
            skipped.append({"company": name, "reason": str(error)})
            continue
        duplicate = duplicate_of(row, rows + added)
        if duplicate:
            skipped.append({"company": name, "reason": f"already on your watch-list as '{duplicate['company']}'"})
        else:
            added.append(row)
    if added:
        _save(cfg, rows + added)
    return {"added": added, "skipped": skipped}


def list_companies(cfg: dict, tier: str = "", status: str = "") -> List[dict]:
    return [row for row in read(cfg) if (not tier or row["tier"] == tier) and (not status or row["status"] == status)]


def _find(rows: List[dict], name: str) -> dict:
    key = (name or "").strip().lower()
    found = [row for row in rows if row["company"].lower() == key] or [row for row in rows if board_of(row) == key]
    if not found:
        raise NotFoundError(f"No company called '{name}' on your watch-list.")
    return found[0]


def _set(cfg: dict, name: str, column: str, value: str) -> dict:
    rows = read(cfg)
    row = _find(rows, name)
    row[column] = value
    _save(cfg, rows)
    return row


def set_tier(cfg: dict, name: str, tier: str) -> dict:
    return _set(cfg, name, "tier", _check_choice("tier", tier, TIERS))


def set_status(cfg: dict, name: str, status: str) -> dict:
    return _set(cfg, name, "status", _check_choice("status", status, COMPANY_STATUSES))


def _row_from_plain(row: dict) -> Tuple[str, str]:
    """(name, link) from a row of any plain list: company or name, and careers_url or url or platform + board."""
    name = row.get("company") or row.get("name") or ""
    url = row.get("careers_url") or row.get("url") or ""
    platform, board = row.get("platform", "").lower(), row.get("board", "")
    if not url and platform in PLATFORMS and board:
        url = careers_url_for(platform, board)
    return name, url


def _first_value(item: dict, columns: set) -> str:
    return next((item[column] for column in item if column in columns and item[column]), "")


def parse_plain(text: str) -> List[dict]:
    """Companies from the text of any plain CSV: with a header naming a company, a careers link or platform and
    board, or a single column of names or links with no header. Nothing is checked or saved here."""
    lines = [line for line in (text or "").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    table = [[cell.strip() for cell in row] for row in csv.reader(lines)]
    if not table:
        raise ValidationError("That file has no companies in it.")
    header = [cell.lower() for cell in table[0]]
    known = _NAME_COLUMNS | _LINK_COLUMNS | _WEBSITE_COLUMNS
    if set(header) & known or {"platform", "board"} <= set(header):
        items = [dict(zip(header, row)) for row in table[1:]]
    elif all(len(row) == 1 for row in table):
        items = [{"url" if "." in row[0] and " " not in row[0] else "name": row[0]} for row in table]
    else:
        raise ValidationError("Freshlist could not find a company name or careers link column. The columns it found "
                              f"are: {', '.join(table[0])}. Name one of them company or careers_url.")
    companies = []
    for item in items:
        link = _first_value(item, _LINK_COLUMNS)
        platform, board = item.get("platform", "").lower(), item.get("board", "")
        if not link and platform in PLATFORMS and board:
            link = careers_url_for(platform, board)
        name, website = _first_value(item, _NAME_COLUMNS), _first_value(item, _WEBSITE_COLUMNS)
        if name or link or website:
            companies.append({"company": name, "careers_url": link, "website": website,
                              "tier": item.get("tier", "").lower(), "status": item.get("status", "").lower(),
                              "notes": item.get("notes", "")})
    if not companies:
        raise ValidationError("That file has no companies in it.")
    if len(companies) > MAX_COMPANIES_IN_FILE:
        raise ValidationError(f"That file has {len(companies)} companies. Freshlist checks at most "
                              f"{MAX_COMPANIES_IN_FILE} at a time, to go easy on job sites. Split it into smaller files.")
    return companies


def parse_links(text: str) -> List[dict]:
    """Companies from pasted text holding Greenhouse, Lever or Ashby links."""
    boards = extract_boards(text or "")
    if not boards:
        raise ValidationError("No Greenhouse, Lever or Ashby links found. Links look like jobs.lever.co/acme, "
                              "boards.greenhouse.io/acme or jobs.ashbyhq.com/acme.")
    return [{"company": "", "careers_url": careers_url_for(platform, board), "website": "", "tier": "", "status": "",
             "notes": ""} for platform, board in boards]


def prepare(cfg: dict, plain: List[dict], today: Optional[date] = None) -> List[dict]:
    """Sort companies from a file before any site is contacted. Each gets a state: "check" (a page Freshlist can
    read), "search" (only a name, so its job page must be looked for), "cannot_read", "listed" or "bad"."""
    known = read(cfg)
    prepared: List[dict] = []
    for item in plain:
        if not item["careers_url"]:
            duplicate = next((row for row in known if row["company"].lower() == item["company"].lower()), None)
            if duplicate and item["company"]:
                prepared.append({**item, "state": "listed", "note": f"Already on your list as '{duplicate['company']}'."})
            else:
                prepared.append({**item, "state": "search", "note": ""})
            continue
        try:
            row = new_row(item["careers_url"], item["company"], item["tier"], item["status"], item["notes"],
                          today or date.today())
        except ValidationError as error:
            prepared.append({**item, "state": "bad", "note": str(error)})
            continue
        duplicate = duplicate_of(row, known)
        if duplicate:
            prepared.append({**row, "state": "listed", "note": f"Already on your list as '{duplicate['company']}'."})
            continue
        known.append(row)
        prepared.append({**row, "state": "check" if board_of(row) else "cannot_read",
                         "note": "" if board_of(row) else "Freshlist can only read Greenhouse, Lever and Ashby pages."})
    return prepared


def add_rows(cfg: dict, wanted: List[dict], today: Optional[date] = None) -> dict:
    """Add companies you picked after a check. Returns {"added": rows, "skipped": [{"company", "reason"}]}."""
    rows = read(cfg)
    added, skipped = [], []
    for item in wanted:
        row = new_row(item["careers_url"], item["company"], item.get("tier", ""), item.get("status", ""),
                      item.get("notes", ""), today or date.today())
        duplicate = duplicate_of(row, rows + added)
        if duplicate:
            skipped.append({"company": row["company"], "reason": f"already on your list as '{duplicate['company']}'"})
        else:
            added.append(row)
    if added:
        _save(cfg, rows + added)
    return {"added": added, "skipped": skipped}


def import_csv(cfg: dict, path: str, today: Optional[date] = None) -> dict:
    """Bulk add from a plain CSV with a header row. Needs a careers_url (or url) column, or platform and board
    columns; company or name, tier, status and notes are used when present. Returns
    {"added": rows, "skipped": [{"company", "reason"}]}. Rows already on the list or not usable are skipped."""
    if not Path(path).is_file():
        raise NotFoundError(f"Could not find the file '{path}'.")
    columns, plain = storage.read_csv(path)
    columns = [column.lower() for column in columns]
    if not ({"careers_url", "url"} & set(columns) or {"platform", "board"} <= set(columns)):
        raise ValidationError(f"{path} needs a header row with a careers_url column (or platform and board).")
    rows = read(cfg)
    added, skipped = [], []
    for raw in plain:
        item = {key.lower(): value for key, value in raw.items()}
        name, url = _row_from_plain(item)
        try:
            row = new_row(url, name, item.get("tier", ""), item.get("status", ""), item.get("notes", ""),
                           today or date.today())
        except ValidationError as error:
            skipped.append({"company": name or url, "reason": str(error)})
            continue
        duplicate = duplicate_of(row, rows + added)
        if duplicate:
            skipped.append({"company": row["company"], "reason": f"already listed as '{duplicate['company']}'"})
        else:
            added.append(row)
    if added:
        _save(cfg, rows + added)
    return {"added": added, "skipped": skipped}


def migrate(cfg: dict, today: Optional[date] = None) -> dict:
    """Convert an older companies.csv (name, platform, board, email, x_handle) to the watch-list columns, after
    copying the old file aside. Emails and handles move to notes. Returns {"migrated", "backup", "companies"}."""
    path = file_path(cfg, "companies_file")
    if not path.exists():
        raise NotFoundError(f"There is no {path} to convert.")
    columns, old = storage.read_csv(path)
    if "careers_url" in columns:
        raise ValidationError(f"{path} already uses the watch-list columns, so nothing was changed.")
    if not {"name", "platform", "board"} <= set(columns):
        raise ValidationError(f"{path} does not have the older columns (name, platform, board), so nothing was changed.")
    rows: List[dict] = []
    for item in old:
        platform, board = item["platform"].lower(), item["board"]
        notes = [f"{label}: {item[key]}" for key, label in (("email", "email"), ("x_handle", "X")) if item.get(key)]
        if platform in PLATFORMS:
            url = careers_url_for(platform, board)
        else:
            url, platform = (board if "." in board else ""), "other"
            notes.append(f"older entry: {item['platform']}/{board}")
        rows.append({"company": item["name"] or board, "careers_url": url, "platform": platform, "tier": "watch",
                     "status": "active", "added_date": (today or date.today()).isoformat(), "last_match_date": "",
                     "notes": "; ".join(notes)})
    saved = storage.backup(path)
    _save(cfg, rows)
    return {"migrated": len(rows), "backup": str(saved), "companies": rows}


def for_run(rows: List[dict]) -> Tuple[List[dict], List[dict]]:
    """(the active companies that can be read, ready for the run; the active ones that cannot, and why)."""
    companies, skipped = [], []
    for row in rows:
        if row["status"] != "active":
            continue
        board = board_of(row)
        if row["platform"] in PLATFORMS and board:
            companies.append({"name": row["company"], "platform": row["platform"], "board": board,
                              "email": "", "x_handle": ""})
        else:
            skipped.append({"company": row["company"], "careers_url": row["careers_url"],
                            "reason": "Freshlist can only read Greenhouse, Lever and Ashby pages"})
    return companies, skipped


def record_matches(cfg: dict, boards: Iterable[Tuple[str, str]], today: date) -> None:
    """Set last_match_date on the companies that matched."""
    wanted = set(boards)
    rows = read(cfg)
    changed = False
    for row in rows:
        if (row["platform"], board_of(row)) in wanted:
            row["last_match_date"] = today.isoformat()
            changed = True
    if changed:
        _save(cfg, rows)

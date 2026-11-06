"""Things to learn before an interview, one list per job in your tracker, kept in learning.csv."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Iterable, List

from .. import storage
from ..config import file_path
from ..errors import NotFoundError, ValidationError

COLUMNS = ["id", "entry_id", "topic", "status", "learn_by", "source"]
STATUSES = ("to_learn", "done")
SOURCES = ("job", "you")    # "job": a skill the posting names that your profile does not have


def read(cfg: dict) -> List[dict]:
    _, rows = storage.read_csv(file_path(cfg, "learning_file"))
    return [{**{column: "" for column in COLUMNS}, **row} for row in rows]


def _save(cfg: dict, rows: List[dict]) -> None:
    storage.write_csv(file_path(cfg, "learning_file"), COLUMNS, rows)


def _check_date(value: str) -> str:
    if value:
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValidationError(f"The date must be written as YYYY-MM-DD, not '{value}'.")
    return value


def _new(entry_id: str, topic: str, learn_by: str, source: str) -> dict:
    return {"id": f"l-{uuid.uuid4().hex[:8]}", "entry_id": entry_id, "topic": topic, "status": "to_learn",
            "learn_by": learn_by, "source": source}


def add(cfg: dict, entry_id: str, topic: str, learn_by: str = "", source: str = "you") -> dict:
    """Add one thing to learn for a tracker entry. The same topic cannot be listed twice for one job."""
    topic, learn_by = (topic or "").strip(), _check_date((learn_by or "").strip())
    if not topic:
        raise ValidationError("Write what you need to learn first.")
    rows = read(cfg)
    if any(row["entry_id"] == entry_id and row["topic"].lower() == topic.lower() for row in rows):
        raise ValidationError(f"'{topic}' is already on the list for this job.")
    item = _new(entry_id, topic, learn_by, source)
    _save(cfg, rows + [item])
    return item


def add_from_job(cfg: dict, entry_id: str, topics: Iterable[str]) -> List[dict]:
    """Add the skills a posting names that your profile does not have, skipping any already listed for this job."""
    rows = read(cfg)
    known = {row["topic"].lower() for row in rows if row["entry_id"] == entry_id}
    added = []
    for topic in (t.strip() for t in topics):
        if topic and topic.lower() not in known:
            known.add(topic.lower())
            added.append(_new(entry_id, topic, "", "job"))
    if added:
        _save(cfg, rows + added)
    return added


def _change(cfg: dict, item_id: str, **changes) -> dict:
    rows = read(cfg)
    item = next((row for row in rows if row["id"] == item_id), None)
    if item is None:
        raise NotFoundError("That item is no longer on your list.")
    item.update(changes)
    _save(cfg, rows)
    return item


def set_done(cfg: dict, item_id: str, done: bool) -> dict:
    return _change(cfg, item_id, status="done" if done else "to_learn")


def set_learn_by(cfg: dict, item_id: str, learn_by: str) -> dict:
    return _change(cfg, item_id, learn_by=_check_date((learn_by or "").strip()))


def remove(cfg: dict, item_id: str) -> dict:
    rows = read(cfg)
    item = next((row for row in rows if row["id"] == item_id), None)
    if item is None:
        raise NotFoundError("That item is no longer on your list.")
    _save(cfg, [row for row in rows if row["id"] != item_id])
    return item

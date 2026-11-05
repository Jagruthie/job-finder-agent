"""The only module that knows how data is kept on disk: CSV and JSON files today, something else later if needed.

Every write goes to a temporary file first and then replaces the real file in one step, so a crash leaves
either the old file or the new one, never half of one.
"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, List, Sequence, Tuple

from .errors import StorageError

_BOM = "utf-8-sig"    # lets Excel show characters such as ₹ correctly, and is dropped again when reading


def write_text(path, text: str, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding=encoding, newline="") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, target.stat().st_mode & 0o777 if target.exists() else 0o644)
        os.replace(temp, target)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


def read_json(path, default: Any = None) -> Any:
    """The file's contents, or `default` if it does not exist yet. A damaged file raises instead of being treated
    as empty, because saving over it would lose your data."""
    file = Path(path)
    if not file.exists():
        return default
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise StorageError(f"{file} is damaged and could not be read ({error}). Fix or remove the file.")


def write_json(path, data: Any) -> None:
    write_text(path, json.dumps(data, indent=1, sort_keys=True))


def read_csv(path) -> Tuple[List[str], List[dict]]:
    """(column names, rows). Blank lines and lines starting with # are skipped. No file means no rows."""
    file = Path(path)
    if not file.exists():
        return [], []
    with open(file, newline="", encoding=_BOM) as handle:
        lines = [line for line in handle if line.strip() and not line.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items() if k and not isinstance(v, list)}
            for row in reader]
    return [(name or "").strip() for name in reader.fieldnames or []], rows


def write_csv(path, columns: Sequence[str], rows: List[dict]) -> None:
    """Columns someone added by hand are kept, after the expected ones."""
    extra = [key for row in rows for key in row if key not in columns]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(columns) + list(dict.fromkeys(extra)), restval="")
    writer.writeheader()
    writer.writerows(rows)
    write_text(path, out.getvalue(), encoding=_BOM)


def backup(path) -> Path:
    """Copy a file next to itself as <name>.bak-<n>, never overwriting an earlier backup."""
    file = Path(path)
    number = 1
    while True:
        target = file.with_name(f"{file.name}.bak-{number}")
        if not target.exists():
            shutil.copy2(file, target)
            return target
        number += 1

"""Backward-compatible signal history storage."""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
from pathlib import Path


LEGACY_FIELDS = ["date", "code", "price", "score", "signal", "confidence", "rsi"]
FIELDS = LEGACY_FIELDS + [
    "session",
    "data_as_of",
    "category",
    "priority",
    "signal_key",
    "source",
    "run_id",
]


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not set(LEGACY_FIELDS).issubset(reader.fieldnames):
            raise ValueError("signals.csv の必須列が不足しています")
        return [{field: row.get(field, "") for field in FIELDS} for row in reader]


def _atomic_write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def migrate_history(path: str | Path) -> int:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        _atomic_write(target, [])
        return 0
    with target.open("r", newline="", encoding="utf-8-sig") as stream:
        header = next(csv.reader(stream), [])
    if header == FIELDS:
        return 0
    rows = _read_rows(target)
    backup = target.with_suffix(target.suffix + ".v1.bak")
    if not backup.exists():
        shutil.copy2(target, backup)
    _atomic_write(target, rows)
    return len(rows)


def append_unique(path: str | Path, rows: list[dict]) -> int:
    target = Path(path)
    migrate_history(target)
    existing = _read_rows(target)
    keys = {(row["run_id"], row["code"], row["category"]) for row in existing}
    additions = []
    for raw in rows:
        row = {field: str(raw.get(field, "")) for field in FIELDS}
        key = (row["run_id"], row["code"], row["category"])
        if key not in keys:
            additions.append(row)
            keys.add(key)
    if additions:
        _atomic_write(target, existing + additions)
    return len(additions)

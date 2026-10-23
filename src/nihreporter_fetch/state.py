"""State for incremental runs (--since-last-run)."""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any

from ._meta import utc_now

log = logging.getLogger(__name__)


def load_state(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def start_from_state(state: dict[str, Any] | None, criteria: dict[str, Any], date_field: str) -> date | None:
    """
    The start date for the next incremental run: the previous run's end date. Starting on that
    day (rather than the day after) overlaps by one day so nothing added late that day is
    missed; write to SQLite or use --merge to de-duplicate.
    """
    if not state:
        return None
    if state.get("criteria") != criteria or state.get("date_field") != date_field:
        log.warning(
            "The state file was written for a different search or date field; continuing from its end date anyway."
        )
    return date.fromisoformat(state["last_end_date"])


def save_state(path: str, criteria: dict[str, Any], date_field: str, end_date: date, total_found: int | None) -> None:
    state = {
        "last_end_date": end_date.isoformat(),
        "criteria": criteria,
        "date_field": date_field,
        "last_total_found": total_found,
        "updated_at": utc_now(),
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)

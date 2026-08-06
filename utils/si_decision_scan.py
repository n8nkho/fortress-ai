"""Shared decision-log scans for SI (wave `results` arrays + flat rows)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def iter_wave_items(row: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield per-symbol entries from a decisions.jsonl wave or flat row."""
    for key in ("results", "wave", "symbols", "decisions"):
        items = row.get(key)
        if isinstance(items, list) and items:
            for it in items:
                if isinstance(it, dict):
                    yield it
            return
    if row.get("symbol") or isinstance(row.get("decision"), dict):
        yield row


def block_reason(item: dict[str, Any]) -> str:
    act = item.get("act") if isinstance(item.get("act"), dict) else {}
    dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
    return str(
        act.get("block_reason")
        or dec.get("reasoning")
        or item.get("block_reason")
        or item.get("reasoning")
        or ""
    )


def item_symbol(item: dict[str, Any]) -> str:
    dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
    return str(item.get("symbol") or dec.get("symbol") or "").strip().upper()


def row_in_session(row: dict[str, Any], today: str) -> bool:
    ts = str(row.get("ts") or "")
    sess = str(row.get("session_date_et") or "")
    # Accept ET date or UTC date string containing session date (near midnight ok).
    return today in ts or sess == today or ts[:10] == today


def iter_session_decisions(
    path: Path,
    *,
    today: str,
    max_bytes: int = 600_000,
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield (wave_row, item) for today's decisions."""
    if not path.is_file():
        return
    try:
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raw = raw[-max_bytes:]
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict) or not row_in_session(row, today):
                continue
            for item in iter_wave_items(row):
                yield row, item
    except Exception:
        return


__all__ = [
    "block_reason",
    "item_symbol",
    "iter_session_decisions",
    "iter_wave_items",
    "row_in_session",
]

"""责任组账册台：读同一本不可再改的旁路账册。"""
from __future__ import annotations

from app.db import get_db
from app.services.owner_group import (
    _SLUG,
    _meta_set,
    dashboard,
    pack_rows,
    record_event,
)


def append_ledger(ref_id: int, note: str, db_path: str | None = None) -> dict:
    """旁记只落事件，不碰权限包与节点责任组。"""
    with get_db(db_path) as conn:
        _meta_set(conn, f"{_SLUG}_note_{int(ref_id)}", note or "改组旁记")
    record_event(f"ref={ref_id} {note}", db_path)
    return {
        "ref_id": ref_id,
        "note": note,
        "panel": dashboard(db_path)["chip"],
        "truth": dashboard(db_path)["truth"],
        "packed": False,
    }


def ledger_rows(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_note_%",),
        ).fetchall()
        return [{"key": r["key"], "value": r["value"]} for r in rows]


def board(db_path: str | None = None) -> dict:
    dash = dashboard(db_path)
    dash["ledger"] = ledger_rows(db_path)
    dash["pack"] = pack_rows(db_path)
    return dash

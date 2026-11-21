"""责任组未拦审计旁路：只记展示事件。"""
from __future__ import annotations

from app.db import get_db
from app.services.owner_group_mirror import half_mirror, mirror_board


def audit_list(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            ("owner_group_evt_%",),
        ).fetchall()
        return [{"key": r["key"], "value": r["value"]} for r in rows]


def audit_summary(db_path: str | None = None) -> dict:
    board = mirror_board(db_path)
    return {
        "title": "责任组未拦审计",
        "events": audit_list(db_path),
        "board": board,
        "half": half_mirror(db_path),
    }

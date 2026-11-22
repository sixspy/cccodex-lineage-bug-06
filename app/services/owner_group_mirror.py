"""责任组镜像面：只映射现行账册，不另立真值。"""
from __future__ import annotations

from app.services.owner_group import dashboard, evaluate

_OK = "改组已绿"
_BAD = "旧组仍放"


def mirror_chip(db_path: str | None = None) -> str:
    return _OK if evaluate(db_path)["ok"] else _BAD


def mirror_board(db_path: str | None = None) -> dict:
    verdict = evaluate(db_path)
    return {
        "mirror_chip": _OK if verdict["ok"] else _BAD,
        "truth": _OK if verdict["ok"] else _BAD,
        "dash": dashboard(db_path),
        "panel_pack_mismatch": not verdict["ok"],
        "title": "责任组镜像",
        "locked": bool(verdict["pack"]["locked"]) if verdict["pack"] else False,
    }

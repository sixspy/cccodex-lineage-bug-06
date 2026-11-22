"""责任组切换：一次写入不可再改的旁路账册，展示与权限同源。

只有一种正确性形态：改组时把节点与组指纹锁进旁路账册，账册只增不改；
展示芯片、覆盖进度、清洗入库闸口都从同一本账册读出，不做双写两轨。
"""
from __future__ import annotations

import hashlib
import json

from app.db import get_db
from app.services.errors import ServiceError

_SLUG = "owner_group"
_OK = "改组已绿"
_BAD = "旧组仍放"
_TITLE = "改组未落"

_DEFAULT_FROM = "采集组"
_DEFAULT_TO = "仓建组"

_GROUP_SEED = (
    ("采集组", "G-01"),
    ("仓建组", "G-02"),
    ("分析组", "G-03"),
    ("值班组", "G-04"),
)


def _sha(obj) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _perm_fp(group_name: str, group_no: str) -> str:
    return _sha({"group": group_name, "no": group_no})[:16]


def _meta_get(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _meta_set(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (key, value),
    )


# ---------------------------------------------------------------- fingerprints
def node_fingerprint(conn, ids: list[int] | None = None) -> str:
    if ids is None:
        rows = conn.execute(
            "SELECT id, node_code, owner_team FROM lineage_nodes ORDER BY id"
        ).fetchall()
    else:
        ids = [int(i) for i in ids]
        if not ids:
            return _sha([])
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            "SELECT id, node_code, owner_team FROM lineage_nodes "
            f"WHERE id IN ({marks}) ORDER BY id",
            ids,
        ).fetchall()
    return _sha([[int(r["id"]), r["node_code"], r["owner_team"]] for r in rows])


def edge_fingerprint(conn) -> str:
    rows = conn.execute(
        "SELECT id, edge_code, upstream_id, downstream_id, transform_kind, status "
        "FROM lineage_edges ORDER BY id"
    ).fetchall()
    return _sha(
        [
            [
                int(r["id"]),
                r["edge_code"],
                int(r["upstream_id"]),
                int(r["downstream_id"]),
                r["transform_kind"],
                r["status"],
            ]
            for r in rows
        ]
    )


def group_fingerprint(conn) -> str:
    rows = conn.execute(
        "SELECT group_name, group_no, fingerprint, active "
        "FROM owner_group_permissions ORDER BY group_name"
    ).fetchall()
    return _sha(
        [
            [r["group_name"], r["group_no"], r["fingerprint"], int(r["active"])]
            for r in rows
        ]
    )


# --------------------------------------------------------------------- seeding
def ensure_owner_group_seed(conn) -> None:
    """幂等补齐权限册；已有账册时一只手指都不碰。"""
    for name, no in _GROUP_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO owner_group_permissions"
            "(group_name, group_no, fingerprint, active) VALUES (?,?,?,0)",
            (name, no, _perm_fp(name, no)),
        )
    passed = conn.execute("SELECT id FROM owner_group_packs LIMIT 1").fetchone()
    initialized = _meta_get(conn, f"{_SLUG}_initialized") == "1"
    if passed or initialized:
        return
    conn.execute(
        "UPDATE owner_group_permissions SET active=1 WHERE group_name=?",
        (_DEFAULT_FROM,),
    )
    _meta_set(conn, f"{_SLUG}_initialized", "1")


def seed_skew(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        ensure_owner_group_seed(conn)


# ------------------------------------------------------------------- read side
def _active_conn(conn) -> dict | None:
    rows = conn.execute(
        "SELECT group_name, group_no, fingerprint, active FROM owner_group_permissions "
        "WHERE active=1 ORDER BY group_name"
    ).fetchall()
    if len(rows) != 1:
        return None
    return dict(rows[0])


def _latest_pack_conn(conn) -> dict | None:
    row = conn.execute(
        "SELECT * FROM owner_group_packs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def evaluate_conn(conn) -> dict:
    """单一判读：账册、组指纹、现行权限、被锁节点逐项对齐。"""
    pack = _latest_pack_conn(conn)
    active = _active_conn(conn)
    reasons: list[str] = []
    if not pack:
        reasons.append("权限包未落")
        return {
            "ok": False,
            "state": "未落",
            "reasons": reasons,
            "group_no": "",
            "group_name": active["group_name"] if active else "",
            "pack": None,
            "active": active,
            "node_fp_match": False,
            "edge_fp_match": False,
            "group_fp": group_fingerprint(conn),
        }

    live_group_fp = group_fingerprint(conn)
    group_fp_match = live_group_fp == pack["group_fingerprint"]
    if not group_fp_match:
        reasons.append("组指纹与现行权限拧花")

    if not active:
        reasons.append("现行权限缺失")
    else:
        if active["group_name"] != pack["to_group"]:
            reasons.append("现行权限未切到新组")
        if active["group_no"] != pack["group_no"]:
            reasons.append("组号不符")

    locked = conn.execute(
        "SELECT pn.node_id, pn.node_code, pn.new_owner_team, n.owner_team "
        "FROM owner_group_pack_nodes pn "
        "LEFT JOIN lineage_nodes n ON n.id=pn.node_id "
        "WHERE pn.pack_id=? ORDER BY pn.node_id",
        (pack["id"],),
    ).fetchall()
    live_ids: list[int] = []
    node_ok = True
    for row in locked:
        live_ids.append(int(row["node_id"]))
        if row["owner_team"] is None or row["owner_team"] != row["new_owner_team"]:
            node_ok = False
    if not node_ok:
        reasons.append("节点责任组被改动")
    live_node_fp = node_fingerprint(conn, live_ids)
    node_fp_match = live_node_fp == pack["node_fingerprint"] and node_ok
    if not node_fp_match and "节点责任组被改动" not in reasons:
        reasons.append("节点责任组指纹不符")

    edge_fp_match = edge_fingerprint(conn) == pack["edge_fingerprint"]

    return {
        "ok": not reasons,
        "state": "已落" if not reasons else "拧花",
        "reasons": reasons,
        "group_no": pack["group_no"],
        "group_name": pack["to_group"],
        "pack": pack,
        "active": active,
        "node_fp_match": node_fp_match,
        "edge_fp_match": edge_fp_match,
        "group_fp_match": group_fp_match,
        "group_fp": live_group_fp,
    }


def evaluate(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        return evaluate_conn(conn)


def panel_chip(db_path: str | None = None) -> str:
    return _OK if evaluate(db_path)["ok"] else _BAD


def truth_flag(db_path: str | None = None) -> str:
    return _OK if evaluate(db_path)["ok"] else _BAD


def active_group(db_path: str | None = None) -> dict | None:
    with get_db(db_path) as conn:
        return _active_conn(conn)


def latest_pack(db_path: str | None = None) -> dict | None:
    with get_db(db_path) as conn:
        return _latest_pack_conn(conn)


def pack_rows(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT pn.pack_id, pn.node_id, pn.node_code, pn.old_owner_team, "
            "pn.new_owner_team FROM owner_group_pack_nodes pn "
            "JOIN owner_group_packs p ON p.id=pn.pack_id ORDER BY pn.node_id"
        ).fetchall()
        return [dict(r) for r in rows]


def gate_allows_pipeline(db_path: str | None = None) -> bool:
    return evaluate(db_path)["ok"]


def assert_cleaning_ingest_allowed(db_path: str | None = None) -> None:
    """清洗入库前校验：账册落点与现行权限必须同源。"""
    verdict = evaluate(db_path)
    if not verdict["ok"]:
        reason = verdict["reasons"][0] if verdict["reasons"] else "权限包未落"
        raise ServiceError(f"清洗入库已拦：{reason}", 409)


# ------------------------------------------------------------------ write side
def _short(exc: Exception) -> str:
    msg = str(exc) or "未知错误"
    return msg.splitlines()[0][:60]


def commit_switch(
    from_group: str = "",
    to_group: str = "",
    db_path: str | None = None,
    group_no: str = "",
) -> dict:
    """一次事务写完全部落点；失败短句报错，账册不可再改。"""
    from_group = (from_group or "").strip() or _DEFAULT_FROM
    to_group = (to_group or "").strip() or _DEFAULT_TO
    if not to_group:
        raise ServiceError("目标组必填")
    if from_group == to_group:
        raise ServiceError("新旧组不能相同")

    from app.db import connect

    conn = connect(db_path)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT id FROM owner_group_packs LIMIT 1").fetchone():
            raise ServiceError("改组账册已锁定，不可再改", 409)

        src = conn.execute(
            "SELECT * FROM owner_group_permissions WHERE group_name=?",
            (from_group,),
        ).fetchone()
        if not src:
            raise ServiceError("旧组未登记", 404)
        if int(src["active"]) != 1:
            raise ServiceError("旧组无现行权限", 409)
        dst = conn.execute(
            "SELECT * FROM owner_group_permissions WHERE group_name=?",
            (to_group,),
        ).fetchone()
        if not dst:
            raise ServiceError("目标组未登记", 404)
        group_no = (group_no or dst["group_no"] or "").strip()
        if not group_no:
            raise ServiceError("组号未定")

        nodes = conn.execute(
            "SELECT id, node_code, owner_team FROM lineage_nodes ORDER BY id"
        ).fetchall()
        affected = conn.execute(
            "SELECT id FROM lineage_nodes WHERE owner_team=?", (from_group,)
        ).fetchall()
        if not affected:
            raise ServiceError("无责任组节点可切", 409)

        cur = conn.execute(
            "UPDATE lineage_nodes SET owner_team=? WHERE owner_team=?",
            (to_group, from_group),
        )
        if int(cur.rowcount) != len(affected):
            raise ServiceError("节点责任组已被改动", 409)

        conn.execute(
            "UPDATE owner_group_permissions SET active=0, updated_at=datetime('now') "
            "WHERE group_name=?",
            (from_group,),
        )
        conn.execute(
            "UPDATE owner_group_permissions SET active=1, fingerprint=?, "
            "updated_at=datetime('now') WHERE group_name=?",
            (_perm_fp(to_group, group_no), to_group),
        )
        if conn.execute(
            "SELECT COUNT(*) AS c FROM owner_group_permissions WHERE active=1"
        ).fetchone()["c"] != 1:
            raise ServiceError("现行权限不唯一", 409)

        node_fp = node_fingerprint(conn)
        group_fp = group_fingerprint(conn)
        edge_fp = edge_fingerprint(conn)
        pack_fp = _sha(
            {
                "from": from_group,
                "to": to_group,
                "no": group_no,
                "node": node_fp,
                "group": group_fp,
                "edge": edge_fp,
            }
        )
        pack_key = f"{from_group}->{to_group}#{group_no}"
        try:
            cur = conn.execute(
                "INSERT INTO owner_group_packs"
                "(pack_key, from_group, to_group, group_no, node_fingerprint, "
                " group_fingerprint, edge_fingerprint, pack_fingerprint, "
                " node_count, edge_count, locked) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                (
                    pack_key,
                    from_group,
                    to_group,
                    group_no,
                    node_fp,
                    group_fp,
                    edge_fp,
                    pack_fp,
                    len(nodes),
                    int(
                        conn.execute(
                            "SELECT COUNT(*) AS c FROM lineage_edges"
                        ).fetchone()["c"]
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise ServiceError(f"改组写入失败：{_short(exc)}", 409) from exc
        pack_id = int(cur.lastrowid)
        for row in nodes:
            new_team = to_group if row["owner_team"] == from_group else row["owner_team"]
            conn.execute(
                "INSERT INTO owner_group_pack_nodes"
                "(pack_id, node_id, node_code, old_owner_team, new_owner_team) "
                "VALUES (?,?,?,?,?)",
                (pack_id, row["id"], row["node_code"], row["owner_team"], new_team),
            )
        conn.execute("COMMIT")
        return {
            "ok": True,
            "pack_id": pack_id,
            "pack_key": pack_key,
            "pack_fingerprint": pack_fp,
            "from_group": from_group,
            "to_group": to_group,
            "group_no": group_no,
            "node_count": len(nodes),
            "affected_count": len(affected),
            "locked": True,
            "chip": _OK,
            "truth": _OK,
            "message": "改组已落，权限包已锁定",
        }
    except ServiceError:
        try:
            conn.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception as exc:  # noqa: BLE001
        try:
            conn.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        raise ServiceError(f"改组写入失败：{_short(exc)}", 409) from exc
    finally:
        conn.close()


def commit_action(payload: dict | None = None, db_path: str | None = None) -> dict:
    payload = payload or {}
    return commit_switch(
        str(payload.get("from_group") or ""),
        str(payload.get("to_group") or ""),
        db_path=db_path,
        group_no=str(payload.get("group_no") or ""),
    )


def half_fix_display(db_path: str | None = None) -> dict:
    """只擦亮组名皮面，验收必然失败；不写任何权限包。"""
    raise ServiceError("仅改展示不算改组，权限包未落", 409)


def reconcile_display(db_path: str | None = None) -> dict:
    return half_fix_display(db_path)


def record_event(note: str, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        seq = (
            int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM meta WHERE key LIKE ?",
                    (f"{_SLUG}_evt_%",),
                ).fetchone()["c"]
            )
            + 1
        )
        _meta_set(conn, f"{_SLUG}_evt_{seq}", note or "改组旁记")
    return dashboard(db_path)


def list_events(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_evt_%",),
        ).fetchall()
        return [{"key": r["key"], "note": r["value"]} for r in rows]


def snapshot(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        verdict = evaluate_conn(conn)
        pack = verdict["pack"]
        return {
            "title": _TITLE,
            "panel_chip": _OK if verdict["ok"] else _BAD,
            "truth": _OK if verdict["ok"] else _BAD,
            "pack_count": 1 if pack else 0,
            "aligned": verdict["ok"],
            "group_no": verdict["group_no"],
            "group_name": verdict["group_name"],
            "state": verdict["state"],
            "reasons": verdict["reasons"],
            "pack_fingerprint": pack["pack_fingerprint"] if pack else "",
            "locked": bool(pack["locked"]) if pack else False,
            "gate_bypass": False,
            "edge_fp_match": verdict["edge_fp_match"],
        }


def dashboard(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        verdict = evaluate_conn(conn)
        pack = verdict["pack"]
        locked_nodes = conn.execute(
            "SELECT COUNT(*) AS c FROM owner_group_pack_nodes pn "
            "JOIN owner_group_packs p ON p.id=pn.pack_id"
        ).fetchone()["c"]
        return {
            "title": "责任组切换",
            "chip": _OK if verdict["ok"] else _BAD,
            "truth": _OK if verdict["ok"] else _BAD,
            "pack_count": 1 if pack else 0,
            "durable_count": int(locked_nodes),
            "rows": pack_rows(db_path) if pack else [],
            "panel_pack_mismatch": not verdict["ok"],
            "ok_chip": _OK,
            "bad_chip": _BAD,
            "label": "责任组切换",
            "group_no": verdict["group_no"],
            "group_name": verdict["group_name"],
            "state": verdict["state"],
            "reasons": verdict["reasons"],
            "pack_fingerprint": pack["pack_fingerprint"] if pack else "",
            "locked": bool(pack["locked"]) if pack else False,
            "node_fp_match": verdict["node_fp_match"],
            "edge_fp_match": verdict["edge_fp_match"],
        }


def summary(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        verdict = evaluate_conn(conn)
        active = verdict["active"]
        return {
            "ok": verdict["ok"],
            "state": verdict["state"],
            "reasons": verdict["reasons"],
            "group_no": verdict["group_no"],
            "group_name": verdict["group_name"],
            "active_name": active["group_name"] if active else "",
            "panel_chip": _OK if verdict["ok"] else _BAD,
        }


def require_pack_or_raise(db_path: str | None = None) -> None:
    assert_cleaning_ingest_allowed(db_path)

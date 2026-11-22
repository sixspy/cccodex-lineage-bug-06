import threading

import pytest

from app.db import get_db
from app.seed import seed_if_needed
from app.services import coverage, edges, impact, nodes
from app.services import owner_group as og
from app.services.errors import ServiceError


def _switch(db_path, **kw):
    return og.commit_switch("采集组", "仓建组", db_path=db_path, **kw)


def test_seed_is_blocked_before_switch(db_path):
    verdict = og.evaluate(db_path)
    assert not verdict["ok"]
    assert og.gate_allows_pipeline(db_path) is False
    assert og.panel_chip(db_path) == og._BAD


def test_switch_writes_durable_pack_once(db_path):
    res = _switch(db_path)
    assert res["ok"] and res["locked"]
    assert res["group_no"] == "G-02"
    verdict = og.evaluate(db_path)
    assert verdict["ok"] and verdict["group_no"] == "G-02"
    assert og.active_group(db_path)["group_name"] == "仓建组"
    # node owners moved, ledger inherited the switch timestamp fingerprints
    moved = [n for n in nodes.list_nodes(db_path=db_path) if n["owner_team"] == "仓建组"]
    assert len(moved) == 4  # 2 switched + 2 already in 仓建组
    assert res["affected_count"] == 2
    pack = og.latest_pack(db_path)
    assert pack["locked"] == 1 and pack["node_fingerprint"]
    assert len(og.pack_rows(db_path)) == res["node_count"]


def test_ledger_is_immutable(db_path):
    _switch(db_path)
    with pytest.raises(ServiceError, match="锁定"):
        _switch(db_path)
    with get_db(db_path) as conn:
        with pytest.raises(Exception):
            conn.execute("UPDATE owner_group_packs SET group_no='G-99'")
    with get_db(db_path) as conn:
        with pytest.raises(Exception):
            conn.execute("DELETE FROM owner_group_pack_nodes")


def test_display_only_fix_is_rejected(db_path):
    with pytest.raises(ServiceError):
        og.half_fix_display(db_path)
    with get_db(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM owner_group_packs").fetchone()["c"] == 0
    assert not og.evaluate(db_path)["ok"]


def test_node_tamper_blocks_gate_and_coverage(db_path):
    _switch(db_path)
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE lineage_nodes SET owner_team='采集组' WHERE node_code='N-ODS-ORD'"
        )
    verdict = og.evaluate(db_path)
    assert not verdict["ok"]
    assert "节点责任组被改动" in verdict["reasons"]
    assert og.gate_allows_pipeline(db_path) is False
    assert coverage.latest_chip(db_path) == "有缺口"
    rep = coverage.run_coverage(db_path=db_path)
    assert rep["group_ok"] is False and rep["result_chip"] == "有缺口"


def test_group_fingerprint_shear_blocks_cleaning_ingest(db_path):
    _switch(db_path)
    items = {n["node_code"]: int(n["id"]) for n in nodes.list_nodes(db_path=db_path)}
    draft = edges.create_edge(
        "E-CLEAN-1", items["N-ADS-DASH"], items["N-ODS-ORD"], "清洗", db_path=db_path
    )
    assert edges.publish_edge(int(draft["id"]), db_path=db_path)["status"] == "已发布"

    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE owner_group_permissions SET fingerprint='deadbeef' WHERE group_name='仓建组'"
        )
    verdict = og.evaluate(db_path)
    assert not verdict["ok"]
    assert "组指纹与现行权限拧花" in verdict["reasons"]

    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE lineage_edges SET status='草稿' WHERE id=?", (draft["id"],)
        )
    with pytest.raises(ServiceError, match="清洗入库已拦"):
        edges.publish_edge(int(draft["id"]), db_path=db_path)


def test_concurrent_switch_writes_pack_once(db_path):
    results = []

    def worker():
        try:
            results.append(_switch(db_path))
        except ServiceError as exc:
            results.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = [r for r in results if isinstance(r, dict)]
    errs = [r for r in results if isinstance(r, ServiceError)]
    assert len(ok) == 1, results
    assert len(errs) == 3
    with get_db(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM owner_group_packs").fetchone()["c"] == 1
    assert og.evaluate(db_path)["ok"]


def test_coverage_reads_group_number(db_path):
    _switch(db_path)
    rep = coverage.run_coverage(db_path=db_path)
    assert rep["group_no"] == "G-02"
    assert rep["group_name"] == "仓建组"
    assert rep["group_state"] == "已落"
    listed = coverage.list_reports(db_path=db_path)[0]
    assert listed["group_no"] == "G-02"
    assert coverage.latest_chip(db_path) in ("已齐套", "有缺口")


def test_impact_does_not_write_pack(db_path):
    root = next(n for n in nodes.list_nodes(db_path=db_path) if n["node_code"] == "N-DWD-ORD")
    job = impact.start_impact(int(root["id"]), db_path=db_path)
    assert job["status"] == "成功"
    assert job["result_chip"] == "旧组仍放"
    with get_db(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM owner_group_packs").fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) c FROM owner_group_pack_nodes"
        ).fetchone()["c"] == 0
    assert og.active_group(db_path)["group_name"] == "采集组"


def test_reset_seed_restores_unswitched_state(tmp_path):
    from app.seed import reset_seed

    path = str(tmp_path / "reset.db")
    seed_if_needed(path)
    _switch(path)
    reset_seed(path)
    verdict = og.evaluate(path)
    assert not verdict["ok"]
    assert og.active_group(path)["group_name"] == "采集组"

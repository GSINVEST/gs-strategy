"""dry run 快照：分類（fail closed）、形狀與隱私、暫停與告警、空帳本。"""
import datetime as dt
import json
from pathlib import Path

from strategies._common.dryrun import snapshot
from strategies._common.dryrun.ledger import DryLedger
from tests.test_dryrun_ops import _miss, _ok, _run

NOW = dt.datetime(2026, 10, 30, 8, 0, tzinfo=dt.timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "strategy-live-snapshot.example.json"


def _strategy(root: Path, sid: str, tags):
    d = root / sid
    d.mkdir(parents=True)
    body = f"id: {sid}\n" + ("tags:\n" + "".join(f"- {t}\n" for t in tags) if tags is not None else "")
    (d / "manifest.yaml").write_text(body, encoding="utf-8")


def test_classify(tmp_path):
    _strategy(tmp_path, "a", ["paper", "Forge"])
    _strategy(tmp_path, "b", ["mint"])
    _strategy(tmp_path, "c", ["paper"])
    _strategy(tmp_path, "d", None)                      # 沒有 tags 欄
    _strategy(tmp_path, "e", ["forge", "mint"])
    assert snapshot.classify(tmp_path, "a") == ["forge"]
    assert snapshot.classify(tmp_path, "b") == ["mint"]
    assert snapshot.classify(tmp_path, "c") == ["open"]
    assert snapshot.classify(tmp_path, "d") == ["unknown"]
    assert snapshot.classify(tmp_path, "e") == ["forge", "mint"]
    assert snapshot.classify(tmp_path, "nope") == ["unknown"]      # 找不到 manifest


def test_classify_inline_list_and_path_traversal(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "manifest.yaml").write_text("id: x\ntags: [paper, 'mint']\n", encoding="utf-8")
    assert snapshot.classify(tmp_path, "x") == ["mint"]
    assert snapshot.classify(tmp_path, "../x") == ["unknown"]      # strategy_id 不得跳出目錄


def test_snapshot_shape_and_privacy(tmp_path):
    led = tmp_path / "dry_ledger.sqlite"
    L = DryLedger(led)
    rid = _run(L, "vgrsi_tx")
    _ok(L, rid, "2026-10-28")
    _ok(L, rid, "2026-10-29")
    L.close()
    _strategy(tmp_path / "s", "vgrsi_tx", ["paper"])
    snap = snapshot.build_snapshot(led, tmp_path / "s", now=NOW)
    assert snap["schema"] == "strategy-live-snapshot/1"
    assert snap["generated_at"] == "2026-10-30T08:00:00+00:00"
    r = snap["runs"][0]
    assert r["strategy_id"] == "vgrsi_tx" and r["classification"] == ["open"]
    assert r["status"] == "active" and r["last_session"] == "2026-10-29" and r["days"] == 2
    assert set(r["equity"]) == {"optimistic", "base", "pessimistic"}
    assert r["equity"]["base"][0] == ["2026-10-28", 5000000.0]
    text = json.dumps(snap)
    for banned in ("fill", "position_json", "sqlite", "strategy.py", str(tmp_path)):
        assert banned not in text


def test_paused_and_alerts(tmp_path):
    led = tmp_path / "dry_ledger.sqlite"
    L = DryLedger(led)
    rid = _run(L, "vgrsi_tx")
    for s in ("2026-10-27", "2026-10-28", "2026-10-29"):
        _miss(L, rid, s)
    L.append_event(rid, "t", "PAUSED", "x")
    L.close()
    _strategy(tmp_path / "s", "vgrsi_tx", ["paper"])
    snap = snapshot.build_snapshot(led, tmp_path / "s", now=NOW, alerts=[(2, "vgrsi_tx: 已暫停")])
    assert snap["runs"][0]["status"] == "paused" and snap["runs"][0]["trailing_missing"] == 3
    assert snap["alerts"] == [{"level": "CRIT", "strategy_id": "vgrsi_tx", "message": "vgrsi_tx: 已暫停"}]


def test_ok_level_alerts_are_dropped(tmp_path):
    led = tmp_path / "dry_ledger.sqlite"
    DryLedger(led).close()
    snap = snapshot.build_snapshot(led, tmp_path, now=NOW, alerts=[(0, "vgrsi_tx: 正常")])
    assert snap["alerts"] == []


def test_unclassified_adds_warning_alert(tmp_path):
    led = tmp_path / "dry_ledger.sqlite"
    L = DryLedger(led)
    _run(L, "vgrsi_tx")
    L.close()
    _strategy(tmp_path / "s", "vgrsi_tx", None)
    snap = snapshot.build_snapshot(led, tmp_path / "s", now=NOW)
    assert snap["runs"][0]["classification"] == ["unknown"]
    assert any(a["level"] == "WARN" and "tags" in a["message"] for a in snap["alerts"])


def test_empty_ledger(tmp_path):
    led = tmp_path / "dry_ledger.sqlite"
    DryLedger(led).close()
    snap = snapshot.build_snapshot(led, tmp_path, now=NOW)
    assert snap["runs"] == [] and snap["alerts"] == []


def test_example_fixture_matches_generator(tmp_path):
    """契約 fixture（genesis-deck 的 gateway 測試會複製同一份）必須就是產生器現在的輸出。"""
    led = tmp_path / "dry_ledger.sqlite"
    L = DryLedger(led)
    rid = _run(L, "vgrsi_tx")
    _ok(L, rid, "2026-10-28")
    _ok(L, rid, "2026-10-29")
    rid2 = _run(L, "secret_demo")
    _ok(L, rid2, "2026-10-29")
    L.close()
    _strategy(tmp_path / "s", "vgrsi_tx", ["paper"])
    _strategy(tmp_path / "s", "secret_demo", ["forge"])
    snap = snapshot.build_snapshot(led, tmp_path / "s", now=NOW, alerts=[(1, "vgrsi_tx: 最近連續缺漏 1 日")])
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == snap

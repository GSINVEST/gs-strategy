"""D3 與實單路徑的硬隔離（規格 §10 第 1–8 項）。每項至少一個失敗案例。"""
import dataclasses
import socket
import sqlite3
import sys
from pathlib import Path

import pytest

from strategies._common.dryrun import guards, types
from strategies._common.dryrun.ledger import DryLedger

PKG = Path(guards.__file__).resolve().parent


# --- #1 憑證自檢 ----------------------------------------------------------
@pytest.mark.parametrize("name", ["SHIOAJI_API_KEY", "SINOPAC_CA_PATH", "FUBON_CERT_PASS",
                                  "IBKR_TOKEN", "BROKER_SECRET", "PERSON_ID", "CA_PASSWD"])
def test_fake_broker_credential_env_refuses_start(name):
    with pytest.raises(guards.SelfCheckError) as ei:
        guards.check_no_credentials(env={name: "fake-secret-value"}, home=None)
    assert name in str(ei.value)
    assert "fake-secret-value" not in str(ei.value)     # 絕不印出值


def test_data_vendor_key_is_allowed():
    guards.check_no_credentials(env={"TEJAPI_KEY": "x", "PATH": "/bin"}, home=None)


def test_credential_file_in_home_refuses_start(tmp_path):
    (tmp_path / ".shioaji").mkdir()
    with pytest.raises(guards.SelfCheckError):
        guards.check_no_credentials(env={}, home=tmp_path)
    other = tmp_path / "h2"
    other.mkdir()
    (other / "Sinopac.pfx").write_bytes(b"\0")
    with pytest.raises(guards.SelfCheckError):
        guards.check_no_credentials(env={}, home=other)


def test_clean_home_passes(tmp_path):
    guards.check_no_credentials(env={}, home=tmp_path)


# --- #2 禁止 import 券商／實單模組 ----------------------------------------
def test_dryrun_package_itself_imports_no_broker_modules():
    assert guards.scan_forbidden_imports(sorted(PKG.glob("*.py"))) == []


@pytest.mark.parametrize("src", ["import shioaji", "from shioaji import Shioaji",
                                 "import gis_trading_broker.core", "from st_pm import x",
                                 "__import__('ib_insync')",
                                 "import importlib; importlib.import_module('fubon_neo')"])
def test_static_scan_catches_forbidden_imports(tmp_path, src):
    f = tmp_path / "bad.py"
    f.write_text(src + "\n", encoding="utf-8")
    hits = guards.scan_forbidden_imports([f])
    assert hits and hits[0][0] == f


def test_runtime_import_guard_blocks_even_uninstalled_module():
    with guards.ImportGuard():
        with pytest.raises(ImportError, match="dry"):
            __import__("shioaji")
    # 離開後恢復：沒裝 shioaji 時是一般 ModuleNotFoundError，而不是我們的攔截
    assert guards.ImportGuard not in [type(f) for f in sys.meta_path]


def test_runtime_import_guard_fails_closed_if_already_loaded(monkeypatch):
    monkeypatch.setitem(sys.modules, "shioaji", object())
    with pytest.raises(guards.SelfCheckError):
        guards.ImportGuard().__enter__()


# --- #3 網路出口白名單 -----------------------------------------------------
def test_egress_blocks_non_allowlisted_host_and_ip():
    with guards.EgressGuard(allowed_hosts=("api.tej.com.tw",)):
        with pytest.raises(guards.EgressBlocked):
            socket.getaddrinfo("api.sinopac.com.tw", 443)
        with pytest.raises(guards.EgressBlocked):
            socket.socket().connect(("203.0.113.9", 80))
        with pytest.raises(guards.EgressBlocked):
            socket.create_connection(("203.0.113.9", 80), timeout=1)


def test_egress_allows_loopback_and_restores():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    orig = socket.socket.connect
    with guards.EgressGuard(allowed_hosts=()):
        c = socket.socket()
        c.connect(("127.0.0.1", port))
        c.close()
    srv.close()
    assert socket.socket.connect is orig


# --- #4 帳本 mode='DRY' 約束與兩側拒絕 -------------------------------------
def test_ledger_rejects_non_dry_mode(tmp_path):
    led = DryLedger(tmp_path / "dry_ledger.sqlite")
    with pytest.raises(sqlite3.IntegrityError):
        led._conn.execute(
            "INSERT INTO dry_runs(mode,strategy_id,strategy_sha256,start_date,benchmark_status,created_ts)"
            " VALUES('LIVE','s','h','2026-01-01','x','t')")


def test_live_side_refuses_dry_rows():
    with pytest.raises(guards.LiveIsolationError):
        guards.assert_no_dry_rows([{"mode": "LIVE"}, {"mode": "DRY"}])
    guards.assert_no_dry_rows([{"mode": "LIVE"}])


# --- #5 帳本對外唯讀 -------------------------------------------------------
def test_readonly_connection_cannot_write(tmp_path):
    p = tmp_path / "dry_ledger.sqlite"
    DryLedger(p).close()
    ro = guards.open_readonly(p)
    assert ro.execute("SELECT count(*) FROM dry_runs").fetchone()[0] == 0
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM dry_runs")
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO dry_events(run_id,ts,kind,detail) VALUES(1,'t','k','d')")


def test_no_order_generating_api_in_package():
    names = set()
    for m in ("guards", "types", "ledger", "executor", "costs"):
        mod = __import__(f"strategies._common.dryrun.{m}", fromlist=["x"])
        names |= {n.lower() for n in dir(mod)}
    assert not [n for n in names if any(w in n for w in
                ("place_order", "submit_order", "send_order", "to_order", "as_order", "live_order"))]


# --- #6 SimulatedOrder 與 Order 不同型別、無轉換 ---------------------------
def test_simulated_order_has_no_conversion_to_live_order():
    assert not hasattr(types, "Order")
    assert dataclasses.is_dataclass(types.SimulatedOrder)
    assert types.SimulatedOrder.__bases__ == (object,)
    forbidden = [a for a in dir(types.SimulatedOrder)
                 if a.lower().startswith(("to_", "as_")) and "dict" not in a.lower()]
    assert forbidden == []
    o = types.SimulatedOrder(strategy_id="s", session="2026-01-02", symbol="TX", side=1, qty=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        o.qty = 5


# --- #7 狀態機沒有 dryrun_active → live -----------------------------------
def test_lifecycle_has_no_live_transition():
    assert "live" not in types.LIFECYCLE_STATES
    assert not [t for t in types.LIFECYCLE_TRANSITIONS if "live" in t[0] or "live" in t[1]]
    assert ("promoted", "dryrun_active") in types.LIFECYCLE_TRANSITIONS


# --- #8 命名與 live 設定檔自檢 ---------------------------------------------
def test_layout_requires_dry_in_names(tmp_path):
    ok = tmp_path / "dryrun"
    ok.mkdir()
    guards.check_run_layout(ok, ok / "dry_ledger.sqlite")
    with pytest.raises(guards.SelfCheckError):
        guards.check_run_layout(tmp_path / "run", ok / "dry_ledger.sqlite")
    with pytest.raises(guards.SelfCheckError):
        guards.check_run_layout(ok, ok / "ledger.sqlite")


@pytest.mark.parametrize("fname", ["live.yaml", "live_config.json", "broker.yml", "order.cfg", "a.pfx"])
def test_live_config_in_dry_dir_refuses(tmp_path, fname):
    d = tmp_path / "dryrun"
    d.mkdir()
    (d / fname).write_text("x")
    with pytest.raises(guards.SelfCheckError):
        guards.check_run_layout(d, d / "dry_ledger.sqlite")


def test_startup_selfcheck_composes_all(tmp_path):
    d = tmp_path / "dryrun"
    d.mkdir()
    guards.run_startup_selfcheck(env={}, home=tmp_path, run_dir=d, ledger_path=d / "dry_ledger.sqlite")
    with pytest.raises(guards.SelfCheckError):
        guards.run_startup_selfcheck(env={"SHIOAJI_X": "1"}, home=tmp_path, run_dir=d,
                                     ledger_path=d / "dry_ledger.sqlite")

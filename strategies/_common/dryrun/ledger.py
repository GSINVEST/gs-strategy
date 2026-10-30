"""DRY 帳本：獨立 SQLite 檔，單向追加（trigger 擋 UPDATE／DELETE），每列 mode='DRY'。

不放在 backtests.sqlite 或任何實單資料庫裡（規格 §10 第 4 項）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS dry_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL DEFAULT 'DRY' CHECK (mode = 'DRY'),
  strategy_id TEXT NOT NULL,
  lineage_id TEXT, branch TEXT,
  strategy_sha256 TEXT NOT NULL,
  framework_version TEXT, cost_model_version TEXT, cost_params_json TEXT,
  promoted_data_snapshot TEXT,
  start_date TEXT NOT NULL,
  capital_base REAL,
  benchmark_status TEXT NOT NULL, benchmark_pack TEXT,
  created_ts TEXT NOT NULL,
  UNIQUE (strategy_id, strategy_sha256)
);
CREATE TABLE IF NOT EXISTS dry_daily (
  run_id INTEGER NOT NULL, session TEXT NOT NULL,
  scenario TEXT NOT NULL CHECK (scenario IN ('optimistic','base','pessimistic')),
  mode TEXT NOT NULL DEFAULT 'DRY' CHECK (mode = 'DRY'),
  status TEXT NOT NULL CHECK (status IN ('OK','MISSING')),
  equity REAL, daily_pnl REAL, position_json TEXT,
  cum_spread_cost_ntd REAL, cum_impact_cost_ntd REAL, cum_commission_ntd REAL,
  data_snapshot TEXT NOT NULL, strategy_sha256 TEXT NOT NULL,
  flags TEXT NOT NULL DEFAULT '', reason TEXT, ts TEXT NOT NULL,
  PRIMARY KEY (run_id, session, scenario)
);
CREATE TABLE IF NOT EXISTS dry_fills (
  fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL, session TEXT NOT NULL, scenario TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'DRY' CHECK (mode = 'DRY'),
  symbol TEXT, root_symbol TEXT, side INTEGER, requested_qty INTEGER, filled_qty INTEGER,
  ref_price REAL, fill_price REAL, multiplier REAL,
  spread_cost_pts REAL, impact_cost_pts REAL,
  spread_cost_ntd REAL, impact_cost_ntd REAL, commission_ntd REAL,
  reason TEXT, flags TEXT NOT NULL DEFAULT '',
  data_snapshot TEXT NOT NULL, strategy_sha256 TEXT NOT NULL, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dry_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  mode TEXT NOT NULL DEFAULT 'DRY' CHECK (mode = 'DRY'),
  ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT
);
"""
TABLES = ("dry_runs", "dry_daily", "dry_fills", "dry_events")
SCENARIO_ORDER = ("optimistic", "base", "pessimistic")


def _triggers() -> str:
    out = []
    for t in TABLES:
        for op in ("UPDATE", "DELETE"):
            out.append(f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
                       f"BEGIN SELECT RAISE(ABORT, 'dry ledger is append-only'); END;")
    return "\n".join(out)


class DryLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA + _triggers())

    def close(self) -> None:
        self._conn.close()

    # --- runs -----------------------------------------------------------
    def find_run(self, strategy_id: str, sha: Optional[str] = None):
        if sha is not None:
            return self._conn.execute(
                "SELECT * FROM dry_runs WHERE strategy_id=? AND strategy_sha256=?",
                (strategy_id, sha)).fetchone()
        return self._conn.execute(
            "SELECT * FROM dry_runs WHERE strategy_id=? ORDER BY run_id DESC LIMIT 1",
            (strategy_id,)).fetchone()

    def start_run(self, *, strategy_id, lineage_id, branch, strategy_sha256, framework_version,
                  cost_model_version, cost_params: dict, promoted_data_snapshot, start_date,
                  capital_base, benchmark_status, benchmark_pack, ts) -> int:
        existing = self.find_run(strategy_id, strategy_sha256)
        if existing is not None:
            return existing["run_id"]
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO dry_runs(strategy_id,lineage_id,branch,strategy_sha256,framework_version,"
                "cost_model_version,cost_params_json,promoted_data_snapshot,start_date,capital_base,"
                "benchmark_status,benchmark_pack,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (strategy_id, lineage_id, branch, strategy_sha256, framework_version,
                 cost_model_version, json.dumps(cost_params, sort_keys=True), promoted_data_snapshot,
                 start_date, capital_base, benchmark_status, benchmark_pack, ts))
        return cur.lastrowid

    # --- events / pause -------------------------------------------------------
    def append_event(self, run_id: int, ts: str, kind: str, detail: str) -> None:
        with self._conn:
            self._conn.execute("INSERT INTO dry_events(run_id,ts,kind,detail) VALUES(?,?,?,?)",
                               (run_id, ts, kind, detail))

    def is_paused(self, run_id: int) -> bool:
        row = self._conn.execute(
            "SELECT kind FROM dry_events WHERE run_id=? AND kind IN ('PAUSED','RESUMED') "
            "ORDER BY event_id DESC LIMIT 1", (run_id,)).fetchone()
        return bool(row and row["kind"] == "PAUSED")

    # --- daily ------------------------------------------------------------------
    def recorded_sessions(self, run_id: int) -> set:
        return {r[0] for r in self._conn.execute(
            "SELECT DISTINCT session FROM dry_daily WHERE run_id=?", (run_id,))}

    def ok_equity(self, run_id: int, scenario: str) -> dict:
        return {r["session"]: r["equity"] for r in self._conn.execute(
            "SELECT session, equity FROM dry_daily WHERE run_id=? AND scenario=? AND status='OK'",
            (run_id, scenario))}

    def resume_watermark(self, run_id: int) -> str:
        """最後一次 RESUMED 當下已記錄的最後一個交易日；之後才算新的缺漏（否則暫停無法恢復）。"""
        row = self._conn.execute(
            "SELECT detail FROM dry_events WHERE run_id=? AND kind='RESUMED' "
            "ORDER BY event_id DESC LIMIT 1", (run_id,)).fetchone()
        if row and row["detail"] and row["detail"].startswith("watermark="):
            return row["detail"].split(";")[0][len("watermark="):]
        return ""

    def last_recorded_session(self, run_id: int) -> str:
        row = self._conn.execute("SELECT max(session) AS s FROM dry_daily WHERE run_id=?",
                                 (run_id,)).fetchone()
        return row["s"] or ""

    def trailing_missing(self, run_id: int) -> int:
        mark = self.resume_watermark(run_id)
        n = 0
        for r in self._conn.execute(
                "SELECT status, session FROM dry_daily WHERE run_id=? AND scenario='base' "
                "ORDER BY session DESC", (run_id,)):
            if r["status"] != "MISSING" or r["session"] <= mark:
                break
            n += 1
        return n

    def has_missing(self, run_id: int) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM dry_daily WHERE run_id=? AND status='MISSING' LIMIT 1",
            (run_id,)).fetchone() is not None

    def append_missing(self, run_id: int, session: str, *, data_snapshot: str, strategy_sha256: str,
                       reason: str, ts: str) -> None:
        with self._conn:
            for sc in SCENARIO_ORDER:
                self._conn.execute(
                    "INSERT INTO dry_daily(run_id,session,scenario,status,data_snapshot,"
                    "strategy_sha256,reason,ts) VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, session, sc, "MISSING", data_snapshot, strategy_sha256, reason, ts))

    def append_day(self, run_id: int, session: str, per_scenario: dict, fills: list, *,
                   data_snapshot: str, strategy_sha256: str, ts: str) -> None:
        """一個交易日三情境的 daily + fills，同一個 transaction。"""
        with self._conn:
            for sc in SCENARIO_ORDER:
                d = per_scenario[sc]
                self._conn.execute(
                    "INSERT INTO dry_daily(run_id,session,scenario,status,equity,daily_pnl,position_json,"
                    "cum_spread_cost_ntd,cum_impact_cost_ntd,cum_commission_ntd,data_snapshot,"
                    "strategy_sha256,flags,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, session, sc, "OK", d["equity"], d["daily_pnl"], d["position_json"],
                     d["cum_spread"], d["cum_impact"], d["cum_commission"], data_snapshot,
                     strategy_sha256, ",".join(d["flags"]), ts))
            for f in fills:
                self._conn.execute(
                    "INSERT INTO dry_fills(run_id,session,scenario,symbol,root_symbol,side,requested_qty,"
                    "filled_qty,ref_price,fill_price,multiplier,spread_cost_pts,impact_cost_pts,"
                    "spread_cost_ntd,impact_cost_ntd,commission_ntd,reason,flags,data_snapshot,"
                    "strategy_sha256,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, session, f["scenario"], f["symbol"], f["root_symbol"], f["side"],
                     f["requested_qty"], f["filled_qty"], f["ref_price"], f["fill_price"],
                     f["multiplier"], f["spread_pts"], f["impact_pts"], f["spread_ntd"],
                     f["impact_ntd"], f["commission_ntd"], f["reason"], ",".join(f["flags"]),
                     data_snapshot, strategy_sha256, ts))

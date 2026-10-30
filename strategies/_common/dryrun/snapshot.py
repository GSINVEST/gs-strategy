"""dry run 快照：把帳本摘要成給 gateway 的固定格式 JSON。只用標準函式庫、唯讀開帳本、不連網。

    python -m strategies._common.dryrun.snapshot --ledger F --strategies-dir D [--out F]

不含成交明細、委託、策略程式碼與任何路徑。機密分類（forge／mint）由 manifest tags 決定，
讀不到或沒有 tags 一律回 ["unknown"]（下游視為機密，fail closed）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from .ops import LEVEL, _ro, _trailing_missing, check_health

SCHEMA = "strategy-live-snapshot/1"
SECRET_TAGS = ("forge", "mint")
SCENARIOS = ("optimistic", "base", "pessimistic")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _unquote(t: str) -> str:
    return t.strip().strip("'\"")


def _tags(manifest: Path):
    """極簡讀 tags：不引入 yaml 依賴（本模組要能在主機系統 python 上跑）。回 None 表示沒有 tags 欄。"""
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    out, inside = [], False
    for ln in lines:
        if ln.startswith("tags:"):
            inside = True
            rest = ln[5:].strip()
            if rest.startswith("["):
                return [_unquote(t) for t in rest.strip("[]").split(",") if t.strip()]
            continue
        if inside:
            s = ln.strip()
            if s.startswith("- "):
                out.append(_unquote(s[2:]))
            elif s and not ln.startswith((" ", "-")):
                break
    return out if inside else None


def classify(strategies_dir: Path, strategy_id: str) -> list:
    if not _ID_RE.match(strategy_id or ""):
        return ["unknown"]
    tags = _tags(Path(strategies_dir) / strategy_id / "manifest.yaml")
    if tags is None:
        return ["unknown"]
    low = {t.lower() for t in tags}
    hit = [t for t in SECRET_TAGS if t in low]
    return hit or ["open"]


def build_snapshot(ledger: Path, strategies_dir: Path, *, now: dt.datetime, alerts=None) -> dict:
    conn = _ro(ledger)
    runs, extra = [], []
    try:
        for r in conn.execute("SELECT * FROM dry_runs ORDER BY run_id").fetchall():
            rid, sid = r["run_id"], r["strategy_id"]
            ev = conn.execute("SELECT kind FROM dry_events WHERE run_id=? AND kind IN ('PAUSED','RESUMED') "
                              "ORDER BY event_id DESC LIMIT 1", (rid,)).fetchone()
            equity = {sc: [[x["session"], x["equity"]] for x in conn.execute(
                "SELECT session, equity FROM dry_daily WHERE run_id=? AND scenario=? AND status='OK' "
                "ORDER BY session", (rid, sc))] for sc in SCENARIOS}
            last = conn.execute("SELECT max(session) AS s FROM dry_daily WHERE run_id=?", (rid,)).fetchone()["s"]
            cost = conn.execute(
                "SELECT COALESCE(SUM(spread_cost_ntd),0) a, COALESCE(SUM(impact_cost_ntd),0) b, "
                "COALESCE(SUM(commission_ntd),0) c FROM dry_fills WHERE run_id=? AND scenario='base'",
                (rid,)).fetchone()
            cls = classify(strategies_dir, sid)
            if cls == ["unknown"]:
                extra.append({"level": "WARN", "strategy_id": sid,
                              "message": f"{sid}: manifest 沒有分類 tags，已視為機密；請補上 tags"})
            runs.append({
                "strategy_id": sid, "lineage_id": r["lineage_id"], "branch": r["branch"],
                "sha8": (r["strategy_sha256"] or "")[:8], "classification": cls,
                "start_date": r["start_date"],
                "status": "paused" if ev and ev["kind"] == "PAUSED" else "active",
                "benchmark_status": r["benchmark_status"], "last_session": last or "",
                "trailing_missing": _trailing_missing(conn, rid),
                "days": len(equity["base"]), "equity": equity,
                "cost_cum": {"spread": cost["a"], "impact": cost["b"], "commission": cost["c"]}})
    finally:
        conn.close()
    al = [{"level": LEVEL[lv], "strategy_id": (m.split(":")[0] if ":" in m else None), "message": m}
          for lv, m in (alerts or []) if lv > 0]
    return {"schema": SCHEMA, "generated_at": now.isoformat(), "runs": runs, "alerts": al + extra}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dryrun.snapshot")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--strategies-dir", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    snap = build_snapshot(Path(a.ledger), Path(a.strategies_dir), now=dt.datetime.now(dt.timezone.utc),
                          alerts=check_health(Path(a.ledger)))
    text = json.dumps(snap, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

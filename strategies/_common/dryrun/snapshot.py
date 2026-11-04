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

from . import regime
from .d1view import LABEL as OBSERVING_LABEL, d1_summary, latest_screened_for
from .ops import LEVEL, _ro, _trailing_missing, check_health

SCHEMA = "strategy-live-snapshot/1"
# /2 = /1 的所有欄位 + 每個 run 的 tier／label／d1／history。gateway 的 validate 目前逐欄位嚴格比對且只收 /1，
# 所以預設仍輸出 /1（不含觀察層），須 gateway 先接受 /2 才能加 --with-observing。
SCHEMA_V2 = "strategy-live-snapshot/2"
# /3 = /2 + 觀察層 history.regime_slices（樣本內回補依 regime 切片）。須 gateway 先接受 /3；只有給 regime 標籤才會輸出 /3。
SCHEMA_V3 = "strategy-live-snapshot/3"
HISTORY_BASIS = "in_sample_backfill"
HISTORY_LABEL = "樣本內回補（非前向）"
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


def _history(conn, rid: int):
    """樣本內回補的序列；與前向 equity 分開，沒有就回 None。"""
    try:
        rows = conn.execute("SELECT scenario, session, equity FROM dry_history WHERE run_id=? "
                            "ORDER BY session", (rid,)).fetchall()
    except Exception:       # 舊帳本沒有 dry_history
        return None
    if not rows:
        return None
    eq = {sc: [[x["session"], x["equity"]] for x in rows if x["scenario"] == sc] for sc in SCENARIOS}
    days = [p[0] for p in eq["base"]]
    return {"basis": HISTORY_BASIS, "label": HISTORY_LABEL,
            "start": days[0] if days else "", "end": days[-1] if days else "", "equity": eq}


def build_snapshot(ledger: Path, strategies_dir: Path, *, now: dt.datetime, alerts=None,
                   include_observing: bool = False, registry: Path | None = None,
                   regime_labels: dict | None = None) -> dict:
    """include_observing=False（預設）時輸出與觀察層出現前逐位元組相同的 /1，observing 的 run 一律略過。

    regime_labels（regime.label_market 的結果）有給時，observing 的 history 多一個 regime_slices，schema 升 /3；
    沒給就維持 /2，輸出不變。regime 切片是樣本內描述，只切基準情境。"""
    if regime_labels is not None and not include_observing:
        raise ValueError("regime_labels 只用在觀察層（需要 include_observing=True）")
    conn = _ro(ledger)
    runs, extra = [], []
    try:
        has_tier = "tier" in {c[1] for c in conn.execute("PRAGMA table_info(dry_runs)")}
        for r in conn.execute("SELECT * FROM dry_runs ORDER BY run_id").fetchall():
            tier = r["tier"] if has_tier else "promoted"
            if tier != "promoted" and not include_observing:
                continue
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
            if include_observing:
                run = runs[-1]
                run["tier"] = tier
                run["label"] = OBSERVING_LABEL if tier == "observing" else None
                d1 = None
                if tier == "observing":
                    d1 = json.loads(r["d1_json"]) if r["d1_json"] else None
                    if registry is not None:     # 重篩後以 registry 最新的同版本判定為準
                        live = latest_screened_for(registry, sid, r["strategy_sha256"])
                        if live is not None:
                            d1 = d1_summary(live)
                run["d1"] = d1
                run["history"] = _history(conn, rid) if tier == "observing" else None
                if regime_labels is not None and run["history"] is not None:
                    base_eq = run["history"]["equity"]["base"]
                    run["history"]["regime_slices"] = {**regime.slice_equity(base_eq, regime_labels),
                                                       "monthly": regime.monthly_strip(base_eq, regime_labels)}
    finally:
        conn.close()
    al = [{"level": LEVEL[lv], "strategy_id": (m.split(":")[0] if ":" in m else None), "message": m}
          for lv, m in (alerts or []) if lv > 0]
    return {"schema": SCHEMA_V3 if regime_labels is not None else SCHEMA_V2 if include_observing else SCHEMA, "generated_at": now.isoformat(), "runs": runs, "alerts": al + extra}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dryrun.snapshot")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--strategies-dir", required=True)
    ap.add_argument("--out")
    ap.add_argument("--with-observing", action="store_true",
                    help="輸出 /2 並含觀察層（gateway 須先接受 /2，否則推送會被拒）")
    ap.add_argument("--regime-labels", help="regime 標籤 JSON（scripts/regime_labels.py 產生）；給了就輸出 /3 並隱含 --with-observing")
    ap.add_argument("--registry", help="重篩後以 registry 最新判定刷新 D1 失敗原因（唯讀）")
    a = ap.parse_args(argv)
    labels = None
    if a.regime_labels:
        blob = json.loads(Path(a.regime_labels).read_text(encoding="utf-8"))
        if blob.get("rules_version") != regime.RULES_VERSION:
            ap.error(f"regime 標籤規則版本 {blob.get('rules_version')!r} ≠ {regime.RULES_VERSION!r}，請重新產生")
        labels = blob["labels"]
    snap = build_snapshot(Path(a.ledger), Path(a.strategies_dir), now=dt.datetime.now(dt.timezone.utc),
                          alerts=check_health(Path(a.ledger)), include_observing=a.with_observing or labels is not None,
                          registry=Path(a.registry) if a.registry else None, regime_labels=labels)
    text = json.dumps(snap, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

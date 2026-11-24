"""dry run 快照：把帳本摘要成給 gateway 的固定格式 JSON。只用標準函式庫、唯讀開帳本、不連網。

    python -m strategies._common.dryrun.snapshot --ledger F --strategies-dir D [--out F]

不含成交明細、委託、策略程式碼與任何路徑。機密分類（forge／mint）由 manifest tags 決定，
讀不到或沒有 tags 一律回 ["unknown"]（下游視為機密，fail closed）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
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


# 外部觀察層 run（研究端提供、不在本帳本內）：只收 /2 以上的完整 run 欄位、只收 observing、
# 且分類必須含機密標籤（fail closed：不可把外部資料標成 open 公開）。
_RUN_KEYS = {"strategy_id", "lineage_id", "branch", "sha8", "classification", "start_date", "status",
             "benchmark_status", "last_session", "trailing_missing", "days", "equity", "cost_cum",
             "tier", "label", "d1", "history"}
_SECRET_OR_UNKNOWN = {"forge", "mint", "unknown"}


def _extra_require(ok):
    if not ok:
        raise ValueError("extra-runs 欄位型別、值域或大小不合")


def _extra_text(value, limit, nullable=False):
    _extra_require((nullable and value is None) or (isinstance(value, str) and len(value) <= limit))


def _extra_number(value, integer=False):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    _extra_require(finite)
    if integer:
        _extra_require(type(value) is int and value >= 0)


def _extra_keys(value, keys):
    _extra_require(isinstance(value, dict) and set(value) == set(keys))


def _extra_series(eq):
    _extra_keys(eq, SCENARIOS)
    for pts in eq.values():
        _extra_require(isinstance(pts, list) and len(pts) <= 20000)
        for point in pts:
            _extra_require(isinstance(point, list) and len(point) == 2)
            _extra_text(point[0], 10)
            _extra_number(point[1])


def _extra_regime(rs):
    _extra_require(isinstance(rs, dict))
    _extra_keys(rs, {"rules_version", "basis", "unlabeled_days", "dims"} | ({"monthly"} if "monthly" in rs else set()))
    _extra_text(rs["rules_version"], 32)
    _extra_require(rs["basis"] == HISTORY_BASIS)
    _extra_number(rs["unlabeled_days"], True)
    labels = {"trend": ("bull", "range", "bear"), "vol": ("low", "mid", "high")}
    _extra_keys(rs["dims"], labels)
    for dim, rows in rs["dims"].items():
        _extra_require(isinstance(rows, list) and len(rows) <= 3)
        for row in rows:
            _extra_keys(row, {"label", "days", "episodes", "ret", "sharpe", "maxdd"})
            _extra_require(row["label"] in labels[dim])
            for key in ("days", "episodes"):
                _extra_number(row[key], True)
            for key in ("ret", "maxdd"):
                _extra_number(row[key])
            if row["sharpe"] is not None:
                _extra_number(row["sharpe"])
    if "monthly" in rs and rs["monthly"] is not None:
        _extra_require(isinstance(rs["monthly"], list) and len(rs["monthly"]) <= 120)
        for row in rs["monthly"]:
            _extra_keys(row, {"month", "days", "trend", "vol", "ret", "sharpe12"})
            _extra_require(isinstance(row["month"], str) and re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", row["month"]) is not None)
            _extra_number(row["days"], True)
            _extra_number(row["ret"])
            if row["sharpe12"] is not None:
                _extra_number(row["sharpe12"])
            for dim in labels:
                _extra_require(row[dim] is None or row[dim] in labels[dim])


def _validate_extra_run(r):
    for key, limit in (("strategy_id", 64), ("sha8", 16), ("start_date", 10), ("last_session", 10), ("benchmark_status", 64), ("label", 100)):
        _extra_text(r[key], limit)
    for key in ("lineage_id", "branch"):
        _extra_text(r[key], 128, True)
    _extra_require(bool(r["label"]) and r["status"] in ("active", "paused"))
    for key in ("days", "trailing_missing"):
        _extra_number(r[key])
    _extra_series(r["equity"])
    _extra_keys(r["cost_cum"], {"spread", "impact", "commission"})
    for value in r["cost_cum"].values():
        _extra_number(value)
    d1 = r["d1"]
    _extra_keys(d1, {"verdict", "reasons", "pending", "screened_at", "rules_version"})
    _extra_require(d1["verdict"] in ("blocked", "conditional"))
    for key in ("reasons", "pending"):
        _extra_require(isinstance(d1[key], list) and len(d1[key]) <= 50)
        for text in d1[key]:
            _extra_text(text, 200)
    _extra_text(d1["screened_at"], 64)
    _extra_text(d1["rules_version"], 32)
    hist = r["history"]
    if hist is not None:
        _extra_require(isinstance(hist, dict))
        _extra_keys(hist, {"basis", "label", "start", "end", "equity"} | ({"regime_slices"} if "regime_slices" in hist else set()))
        _extra_require(hist["basis"] == HISTORY_BASIS)
        for key, limit in (("label", 100), ("start", 10), ("end", 10)):
            _extra_text(hist[key], limit)
        _extra_series(hist["equity"])
        if hist.get("regime_slices") is not None:
            _extra_regime(hist["regime_slices"])


def load_extra_snapshot(path: Path, existing_ids) -> tuple[list, list]:
    """同檔 runs 與 alerts 原子驗證；錯誤文字不回顯外部內容。"""
    try:
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise ValueError("外部檔無法讀取或 JSON 不合格") from None
    runs = blob.get("runs") if isinstance(blob, dict) else None
    if not isinstance(runs, list) or not runs:
        raise ValueError("extra-runs 檔需含非空的 runs 陣列")
    _extra_require(len(runs) <= 100)
    seen = set(existing_ids)
    for r in runs:
        if not isinstance(r, dict) or set(r) != _RUN_KEYS:
            raise ValueError("extra-runs 的 run 欄位需恰為完整 /2 欄位")
        sid = r["strategy_id"]
        if not isinstance(sid, str) or not _ID_RE.fullmatch(sid):
            raise ValueError("extra-runs 的 strategy_id 格式不合")
        if sid in seen:
            raise ValueError("extra-runs 的 strategy_id 重複或與帳本衝突")
        seen.add(sid)
        if r["tier"] != "observing":
            raise ValueError("extra-runs 只收 observing")
        cls = r["classification"]
        if not isinstance(cls, list) or not cls or any(not isinstance(c, str) or c not in _SECRET_OR_UNKNOWN for c in cls):
            raise ValueError("extra-runs 的 classification 必須全為機密或 unknown（不可標 open）")
        _validate_extra_run(r)
    alerts = blob.get("alerts", [])
    if not isinstance(alerts, list):
        raise ValueError("extra-runs 的 alerts 必須是陣列")
    local_ids = {r["strategy_id"] for r in runs}
    for alert in alerts:
        if not isinstance(alert, dict) or set(alert) != {"strategy_id", "level", "message"}:
            raise ValueError("extra-runs 的 alert 欄位不合")
        if not isinstance(alert["strategy_id"], str) or alert["strategy_id"] not in local_ids:
            raise ValueError("extra-runs 的 alert 必須對應同檔 run")
        if alert["level"] not in ("WARN", "CRIT"):
            raise ValueError("extra-runs 的 alert 等級不合")
        if not isinstance(alert["message"], str) or len(alert["message"]) > 300:
            raise ValueError("extra-runs 的 alert 訊息長度不合")
    return runs, alerts


def load_extra_runs(path: Path, existing_ids) -> list:
    """相容既有只取 runs 的呼叫端；仍驗證同檔 alerts。"""
    return load_extra_snapshot(path, existing_ids)[0]


def load_extra_runs_dir(path: Path, existing_ids, *, base_snapshot=None) -> tuple[list, list]:
    """依檔名排序；每檔驗證通過才附加，失敗不占用 id。"""
    runs, alerts = [], []
    seen = set(existing_ids)
    try:
        if not Path(path).is_dir():
            raise OSError
        paths = sorted(Path(path).glob("*.json"), key=lambda p: p.name)
    except OSError:
        print("[WARN] extra run 資料夾無法讀取，略過外部檔", file=sys.stderr)
        return runs, alerts
    for ordinal, entry in enumerate(paths, 1):
        try:
            new_runs, new_alerts = load_extra_snapshot(entry, seen)
            _extra_require(len(seen) + len(new_runs) <= 100)
            base = base_snapshot or {"schema": SCHEMA_V3, "generated_at": "2000-01-01T00:00:00+00:00", "runs": [], "alerts": []}
            trial = {**base, "runs": base["runs"] + runs + new_runs,
                     "alerts": base["alerts"] + (alerts + new_alerts)[:max(0, 200 - len(base["alerts"]))]}
            _extra_require(len(json.dumps(trial, ensure_ascii=False).encode("utf-8")) <= 2 * 1024 * 1024)
        except (ValueError, OSError):
            # 檔名與例外內容可能含未核定資訊；只記排序序號與固定原因。
            print(f"[WARN] extra run 檔 #{ordinal} 不合格：驗證失敗，已略過", file=sys.stderr)
            continue
        runs.extend(new_runs)
        alerts.extend(new_alerts)
        seen.update(r["strategy_id"] for r in new_runs)
    return runs, alerts


def append_extra_snapshot(snap: dict, runs: list, alerts: list) -> None:
    """保留帳本告警優先，外部告警只取剩餘額度。"""
    if len(snap["alerts"]) > 200:
        print("[WARN] 帳本 alerts 超過快照 200 筆上限，已截斷", file=sys.stderr)
        snap["alerts"] = snap["alerts"][:200]
    room = max(0, 200 - len(snap["alerts"]))
    if len(alerts) > room:
        print("[WARN] 外部 alerts 超過快照 200 筆上限，已截斷", file=sys.stderr)
    candidate = {**snap, "runs": snap["runs"] + runs, "alerts": snap["alerts"] + alerts[:room]}
    _extra_require(len(candidate["runs"]) <= 100)
    _extra_require(len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= 2 * 1024 * 1024)
    snap["runs"].extend(runs)
    snap["alerts"].extend(alerts[:room])
    if any(r.get("history") and "regime_slices" in r["history"] for r in runs):
        snap["schema"] = SCHEMA_V3


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
    ap.add_argument("--extra-runs", help="外部觀察層 run 檔（snapshot 形狀 JSON 的 runs）；附加在帳本 run 之後，隱含 --with-observing；"
                                         "不合格就整份拒絕（結束碼 2），不改動帳本 run")
    ap.add_argument("--extra-runs-dir", help="逐檔讀取資料夾內 *.json；不合格只跳過該檔；優先於 --extra-runs")
    a = ap.parse_args(argv)
    if a.extra_runs_dir and a.extra_runs:
        print("[WARN] 同時設定外部資料夾與單檔，資料夾優先、忽略單檔", file=sys.stderr)
    labels = None
    if a.regime_labels:
        blob = json.loads(Path(a.regime_labels).read_text(encoding="utf-8"))
        if blob.get("rules_version") != regime.RULES_VERSION:
            ap.error(f"regime 標籤規則版本 {blob.get('rules_version')!r} ≠ {regime.RULES_VERSION!r}，請重新產生")
        labels = blob["labels"]
    snap = build_snapshot(Path(a.ledger), Path(a.strategies_dir), now=dt.datetime.now(dt.timezone.utc),
                          alerts=check_health(Path(a.ledger)), include_observing=a.with_observing or labels is not None or bool(a.extra_runs or a.extra_runs_dir),
                          registry=Path(a.registry) if a.registry else None, regime_labels=labels)
    if a.extra_runs_dir:
        append_extra_snapshot(snap, *load_extra_runs_dir(Path(a.extra_runs_dir), [r["strategy_id"] for r in snap["runs"]], base_snapshot=snap))
    elif a.extra_runs:
        try:
            append_extra_snapshot(snap, *load_extra_snapshot(Path(a.extra_runs), [r["strategy_id"] for r in snap["runs"]]))
        except (ValueError, OSError) as e:
            print(f"[ERR] --extra-runs 不合格：{e}", file=sys.stderr)
            return 2
    text = json.dumps(snap, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""dry run 營運工具：健康檢查（告警）與帳本備份。只用標準函式庫，在主機上跑（不進容器、不連網）。

    python -m strategies._common.dryrun.ops health --ledger F [--alert-log F]
    python -m strategies._common.dryrun.ops backup --ledger F --dest DIR [--keep 14]

health 結束碼：0 正常、1 警告、2 嚴重。有警告以上時：寫進 --alert-log，並（若設了環境變數
DRYRUN_ALERT_CMD）把訊息從 stdin 餵給該指令，由你接 Slack／mail 等通道。這裡刻意不內建任何
對外連線。兩者都只以唯讀方式開帳本，不會改動帳本。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

OK, WARN, CRIT = 0, 1, 2
LEVEL = {OK: "OK", WARN: "WARN", CRIT: "CRIT"}
PAUSE_AFTER = 3          # 與 executor 的暫停門檻一致
STALE_DAYS = 5           # 最後一個已記錄交易日距今超過幾個日曆日算沒在跑（涵蓋連假）
BACKUP_PREFIX = "dry_ledger-"


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _resume_mark(conn, run_id: int) -> str:
    row = conn.execute("SELECT detail FROM dry_events WHERE run_id=? AND kind='RESUMED' "
                       "ORDER BY event_id DESC LIMIT 1", (run_id,)).fetchone()
    if row and row["detail"] and row["detail"].startswith("watermark="):
        return row["detail"].split(";")[0][len("watermark="):]
    return ""


def _trailing_missing(conn, run_id: int) -> int:
    mark = _resume_mark(conn, run_id)
    n = 0
    for r in conn.execute("SELECT status, session FROM dry_daily WHERE run_id=? AND scenario='base' "
                          "ORDER BY session DESC", (run_id,)):
        if r["status"] != "MISSING" or r["session"] <= mark:
            break
        n += 1
    return n


def check_health(ledger: Path, *, today: dt.date | None = None,
                 stale_days: int = STALE_DAYS) -> list[tuple[int, str]]:
    """回傳 [(等級, 訊息)]；空清單不會出現，至少有一筆 OK 或問題。"""
    today = today or dt.date.today()
    if not Path(ledger).exists():
        return [(CRIT, f"帳本不存在：{ledger}（排程可能從未成功執行過）")]
    conn = _ro(ledger)
    try:
        runs = conn.execute("SELECT run_id, strategy_id FROM dry_runs ORDER BY run_id").fetchall()
        if not runs:
            return [(WARN, "帳本內沒有任何 dry run（尚未 start 或沒有策略被晉升）")]
        out: list[tuple[int, str]] = []
        for r in runs:
            rid, sid = r["run_id"], r["strategy_id"]
            ev = conn.execute("SELECT kind FROM dry_events WHERE run_id=? AND kind IN ('PAUSED','RESUMED') "
                              "ORDER BY event_id DESC LIMIT 1", (rid,)).fetchone()
            if ev and ev["kind"] == "PAUSED":
                out.append((CRIT, f"{sid}: 已暫停（連續缺漏），需人工檢查資料後 resume"))
                continue
            miss = _trailing_missing(conn, rid)
            if miss >= PAUSE_AFTER:
                out.append((CRIT, f"{sid}: 連續缺漏 {miss} 日"))
            elif miss >= 1:
                out.append((WARN, f"{sid}: 最近連續缺漏 {miss} 日（滿 {PAUSE_AFTER} 日會暫停）"))
            last = conn.execute("SELECT max(session) AS s FROM dry_daily WHERE run_id=?", (rid,)).fetchone()["s"]
            if not last:
                out.append((WARN, f"{sid}: 已 start 但還沒有任何交易日紀錄"))
                continue
            age = (today - dt.date.fromisoformat(last)).days
            if age > stale_days:
                out.append((CRIT, f"{sid}: 最後紀錄 {last}，已 {age} 日沒有新資料（排程沒在跑？）"))
            if not any(lv for lv, m in out if m.startswith(sid + ":")):
                out.append((OK, f"{sid}: 正常，最後紀錄 {last}"))
        return out
    finally:
        conn.close()


def _notify(text: str) -> None:
    cmd = os.environ.get("DRYRUN_ALERT_CMD", "").strip()
    if not cmd:
        return
    try:
        subprocess.run(shlex.split(cmd), input=text, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:   # 通知失敗不能讓健康檢查本身崩掉
        print(f"[ops] DRYRUN_ALERT_CMD 失敗：{e}", file=sys.stderr)


def cmd_health(args) -> int:
    results = check_health(Path(args.ledger))
    worst = max(lv for lv, _ in results)
    lines = [f"[{LEVEL[lv]}] {msg}" for lv, msg in results]
    print("\n".join(lines))
    if worst >= WARN:
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        text = "\n".join(f"{stamp} {l}" for l in lines if not l.startswith("[OK]")) + "\n"
        if args.alert_log:
            p = Path(args.alert_log)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(text)
        _notify(text)
    return worst


def backup_ledger(ledger: Path, dest: Path, *, keep: int = 14, now: dt.datetime | None = None) -> Path:
    """用 sqlite 的線上備份 API 複製（帳本正被寫入也一致），驗證後才輪替舊備份。"""
    ledger, dest = Path(ledger), Path(dest)
    if not ledger.exists():
        raise FileNotFoundError(ledger)
    dest.mkdir(parents=True, exist_ok=True)
    now = now or dt.datetime.now()
    target = dest / f"{BACKUP_PREFIX}{now:%Y%m%d-%H%M%S}.sqlite"
    if target.exists():
        raise FileExistsError(target)
    src = _ro(ledger)
    try:
        tmp = target.with_suffix(".partial")
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("備份檔 integrity_check 未通過")
            for t in ("dry_runs", "dry_daily", "dry_fills", "dry_events"):
                a = src.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                b = dst.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                if a != b:
                    raise RuntimeError(f"備份列數不符：{t} {a} != {b}")
        except Exception:
            dst.close()
            tmp.unlink(missing_ok=True)
            raise
        dst.close()
        tmp.replace(target)
    finally:
        src.close()
    olds = sorted(dest.glob(f"{BACKUP_PREFIX}*.sqlite"))   # 只輪替符合命名的備份檔，不碰其他檔案
    for old in olds[:-keep] if keep > 0 else []:
        old.unlink()
    return target


def cmd_backup(args) -> int:
    try:
        t = backup_ledger(Path(args.ledger), Path(args.dest), keep=args.keep)
    except Exception as e:
        print(f"[CRIT] 帳本備份失敗：{e}")
        _notify(f"帳本備份失敗：{e}")
        return CRIT
    print(f"備份完成：{t}")
    return OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dryrun.ops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("health")
    h.add_argument("--ledger", required=True)
    h.add_argument("--alert-log")
    b = sub.add_parser("backup")
    b.add_argument("--ledger", required=True)
    b.add_argument("--dest", required=True)
    b.add_argument("--keep", type=int, default=14)
    args = ap.parse_args(argv)
    return cmd_health(args) if args.cmd == "health" else cmd_backup(args)


if __name__ == "__main__":
    sys.exit(main())

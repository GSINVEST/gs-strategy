"""產生 regime 標籤檔（給 dryrun.snapshot --regime-labels 用）。只用標準函式庫：cron 主機的 python3 沒有 pandas。

    python3 scripts/regime_labels.py --out regime_labels.json [--start 2016-01-04] [--end YYYY-MM-DD]

市場序列用台指期近月連續（tx_continuous_d，2016-01-04 起）收盤；規則見 strategies/_common/dryrun/regime.py。
連線方式與 strategies/_common/qd.py 相同：QD_API_BASE（預設 http://quantdata.lan/api/v1）；
該名稱在 WSL 內解析不到時改走 QD_HOST_IP（預設 192.168.11.90）並帶 Host 標頭。伺服器每筆查詢 30 秒上限，所以依年度分段。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategies._common.dryrun import regime  # noqa: E402  標準函式庫模組

DEFAULT_BASE = "http://quantdata.lan/api/v1"
DEFAULT_HOST_IP = "192.168.11.90"


def year_ranges(start: str, end: str):
    a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    out = []
    for y in range(a.year, b.year + 1):
        lo = max(a, dt.date(y, 1, 1))
        hi = min(b, dt.date(y, 12, 31))
        out.append((lo.isoformat(), hi.isoformat()))
    return out


def parse_closes(data) -> list:
    """/sql 回應 → [(YYYY-MM-DD, close)] 升冪；日期可能是 RFC 1123 或 ISO，close 空值或 ≤ 0 的列丟掉。"""
    cols = data.get("columns")
    if cols != ["trading_date", "close"]:
        raise ValueError(f"回應欄位不是 trading_date, close：{cols!r}")
    out = {}
    for d, c in data.get("rows", []):
        if c is None or not float(c) > 0:
            continue
        try:
            day = parsedate_to_datetime(d).date() if "," in str(d) else dt.date.fromisoformat(str(d)[:10])
        except (TypeError, ValueError):
            continue
        out[day.isoformat()] = float(c)
    return sorted(out.items())


def _endpoint():
    base = os.environ.get("QD_API_BASE", DEFAULT_BASE).rstrip("/")
    host = urlsplit(base).hostname or ""
    try:
        socket.getaddrinfo(host, 80)
        return f"{base}/sql", {}
    except socket.gaierror:
        return base.replace(host, os.environ.get("QD_HOST_IP", DEFAULT_HOST_IP), 1) + "/sql", {"Host": host}


def fetch_closes(start: str, end: str, *, retries: int = 2, timeout: int = 120) -> list:
    url, extra = _endpoint()
    rows = []
    for lo, hi in year_ranges(start, end):
        q = ("SELECT trading_date, close FROM tx_continuous_d "
             f"WHERE trading_date BETWEEN '{lo}' AND '{hi}' ORDER BY trading_date")
        req = urllib.request.Request(url, data=json.dumps({"sql": q}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **extra})
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    rows += parse_closes(json.loads(r.read().decode("utf-8")))
                break
            except (urllib.error.URLError, OSError, ValueError):
                if attempt == retries:
                    raise
                time.sleep(1.5 * (attempt + 1))
    return sorted(dict(rows).items())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="regime_labels")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--end", default=dt.date.today().isoformat())
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")       # Windows 中文版終端預設 cp950
    closes = fetch_closes(a.start, a.end)
    if len(closes) < 300:
        print(f"只讀到 {len(closes)} 個交易日，不足以產生標籤；不寫檔", file=sys.stderr)
        return 1
    labels = regime.label_market(closes)
    Path(a.out).write_text(json.dumps({"rules_version": regime.RULES_VERSION, "source": "tx_continuous_d",
                                       "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                       "labels": labels}, ensure_ascii=False), encoding="utf-8")
    print(f"{len(closes)} 個交易日、{len(labels)} 個有標籤 → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

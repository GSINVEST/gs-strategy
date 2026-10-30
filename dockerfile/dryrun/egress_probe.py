"""在 dry run 容器內執行的隔離探針。任何一項不符預期就 exit 1。

用法（由 verify-egress.sh 呼叫）：
  docker compose run --rm --entrypoint python dryrun /app/dockerfile/dryrun/egress_probe.py
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sys
from pathlib import Path

PROXY = ("egress-proxy", 3128)
ALLOWED = "api.tej.com.tw"
DENIED = ["example.com", "www.sinopac.com", "www.fubon.com", "www.interactivebrokers.com",
          "api.binance.com"]
results: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail})


def direct_blocked(target: str) -> None:
    try:
        socket.create_connection((target, 443), timeout=5).close()
        check(f"direct {target}:443 blocked", False, "connected")
    except OSError as e:
        check(f"direct {target}:443 blocked", True, type(e).__name__)


def via_proxy(host: str) -> tuple:
    c = http.client.HTTPConnection(*PROXY, timeout=15)
    c.set_tunnel(host, 443)
    try:
        c.connect()
        return True, "tunnel established"
    except OSError as e:
        return False, str(e)[:80]
    finally:
        c.close()


def writable(path: str) -> bool:
    try:
        p = Path(path) / ".probe"
        p.write_text("x")
        p.unlink()
        return True
    except OSError:
        return False


# 1) 沒有直連外網的路（連白名單主機也必須經代理）
for t in ("1.1.1.1", "8.8.8.8", ALLOWED, "www.sinopac.com"):
    direct_blocked(t)

# 2) 代理：白名單通、其餘（含券商）被拒
try:
    socket.create_connection(PROXY, timeout=5).close()
    check("egress-proxy reachable", True)
except OSError as e:
    check("egress-proxy reachable", False, str(e)[:80])
ok, d = via_proxy(ALLOWED)
check(f"proxy -> {ALLOWED} allowed", ok, d)
for h in DENIED:
    ok, d = via_proxy(h)
    # 必須是代理明確拒絕（403），不是「代理沒起來所以連不上」這種假陽性
    check(f"proxy -> {h} denied by proxy (403)", (not ok) and "403" in d, d)

# 3) 沒有券商憑證、沒有主機檔案
sys.path.insert(0, "/app")
try:
    from strategies._common.dryrun.guards import check_no_credentials
    check_no_credentials(env=os.environ, home=Path.home())
    check("no broker credentials in env/home", True)
except Exception as e:  # SelfCheckError 或 import 失敗都算不通過
    check("no broker credentials in env/home", False, type(e).__name__)
check("host drive not mounted (/mnt/c)", not Path("/mnt/c").exists())
check("running as non-root", os.getuid() != 0, f"uid={os.getuid()}")

# 4) 檔案系統：程式碼與 bundle 唯讀，只有帳本目錄可寫
check("/app read-only", not writable("/app"))
check("/zipline/data read-only", not writable("/zipline/data"))
check("/dryrun-ledger writable (ledger only)", writable("/dryrun-ledger"))

print(json.dumps(results, ensure_ascii=False, indent=1))
bad = [r for r in results if not r["ok"]]
print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
sys.exit(1 if bad else 0)

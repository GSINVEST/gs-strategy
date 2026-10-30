"""與實單路徑的硬隔離（規格 §10 第 1–5、8 項）。原則：靠「不存在通路」，不靠旗標。

這裡的網路與 import 攔截是行程內的第二道防線；真正的出口封鎖要靠容器／網路策略
（§10 第 3 項），本模組不宣稱能取代它。
"""
from __future__ import annotations

import ast
import fnmatch
import ipaddress
import os
import re
import socket
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence


class SelfCheckError(RuntimeError):
    """啟動自檢失敗：拒絕啟動。"""


class EgressBlocked(OSError):
    pass


class LiveIsolationError(RuntimeError):
    pass


# --- #1 憑證 ---------------------------------------------------------------
ALLOWED_ENV = frozenset({"TEJAPI_KEY"})        # 資料供應商金鑰，不是券商憑證
_CRED_ENV = re.compile(
    r"SHIOAJI|SINOPAC|FUBON|YUANTA|KGI|CAPITAL_?API|IBKR|IB_GATEWAY|ALPACA|BINANCE|CCXT|"
    r"BROKER|PERSON_ID|CA_PASSWD|CA_PATH|API_SECRET|SECRET_KEY", re.I)
_CRED_PATHS = (".shioaji", ".config/shioaji", "Sinopac.pfx", ".fubon", ".ibkr", ".config/ib",
               ".config/fubon")


def check_no_credentials(*, env: Mapping[str, str], home: Optional[Path]) -> None:
    """環境變數名稱與家目錄的已知憑證路徑出現即拒啟動。錯誤訊息只含名稱，絕不含值。"""
    hits = [k for k in env if k not in ALLOWED_ENV and _CRED_ENV.search(k)]
    if home is not None:
        home = Path(home)
        hits += [str(home / p) for p in _CRED_PATHS if (home / p).exists()]
        hits += [str(p) for p in home.glob("*.pfx")]
    if hits:
        raise SelfCheckError("dry run 行程不得持有券商憑證：" + ", ".join(sorted(set(hits))))


# --- #2 import ---------------------------------------------------------------
FORBIDDEN_IMPORT_ROOTS = frozenset({
    "shioaji", "fubon_neo", "ib_insync", "ib_async", "ibapi", "alpaca", "alpaca_trade_api",
    "ccxt", "binance", "gis_trading_broker", "gis_broker", "st_pm", "stpm", "its",
    "gs_trading_portal",
})


def _root(name: str) -> str:
    return name.split(".")[0]


def scan_forbidden_imports(paths: Iterable[Path]) -> list:
    """靜態掃描：回傳 [(path, lineno, module)]。含 __import__ 與 importlib.import_module 的字面值。"""
    hits = []
    for p in map(Path, paths):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                if name in ("__import__", "import_module"):
                    mods = [node.args[0].value]
            for m in mods:
                if _root(m) in FORBIDDEN_IMPORT_ROOTS:
                    hits.append((p, node.lineno, m))
    return hits


class ImportGuard:
    """執行期 import 攔截（sys.meta_path）。進入時若禁用模組已載入則 fail closed。"""

    def find_spec(self, fullname, path=None, target=None):
        if _root(fullname) in FORBIDDEN_IMPORT_ROOTS:
            raise ImportError(f"import of {fullname!r} is blocked in a dry run process")
        return None

    def __enter__(self):
        loaded = sorted(m for m in sys.modules if _root(m) in FORBIDDEN_IMPORT_ROOTS)
        if loaded:
            raise SelfCheckError("dry run 行程已載入禁用模組：" + ", ".join(loaded))
        sys.meta_path.insert(0, self)
        return self

    def __exit__(self, *exc):
        if self in sys.meta_path:
            sys.meta_path.remove(self)
        return False


# --- #3 網路出口 ---------------------------------------------------------------
DEFAULT_ALLOWED_HOSTS = ("api.tej.com.tw",)   # zipline-tej import 時需連 TEJ 取交易日曆


def _is_loopback(host) -> bool:
    if host in ("localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class EgressGuard:
    """只放行 loopback 與白名單主機（及其解析出的 IP）；其他連線一律 EgressBlocked。"""

    def __init__(self, allowed_hosts: Sequence[str] = DEFAULT_ALLOWED_HOSTS):
        self.allowed_hosts = tuple(allowed_hosts)
        self._ips: set = set()

    def __enter__(self):
        guard = self
        self._orig_gai = socket.getaddrinfo
        self._had = {n: n in socket.socket.__dict__ for n in ("connect", "connect_ex")}
        self._orig = {n: getattr(socket.socket, n) for n in self._had}

        def gai(host, *a, **k):
            if host is None or _is_loopback(host) or host in guard.allowed_hosts:
                res = guard._orig_gai(host, *a, **k)
                if host in guard.allowed_hosts:
                    guard._ips.update(r[4][0] for r in res)
                return res
            raise EgressBlocked(f"egress to {host!r} is not allowlisted in a dry run process")

        def wrap(name):
            real = self._orig[name]

            def f(sock, address, *a, **k):
                if isinstance(address, tuple) and not (
                        _is_loopback(address[0]) or address[0] in guard._ips):
                    raise EgressBlocked(f"egress to {address[0]!r} is not allowlisted")
                return real(sock, address, *a, **k)
            return f

        socket.getaddrinfo = gai
        for n in self._had:
            setattr(socket.socket, n, wrap(n))
        return self

    def __exit__(self, *exc):
        socket.getaddrinfo = self._orig_gai
        for n, had in self._had.items():
            if had:
                setattr(socket.socket, n, self._orig[n])
            else:
                delattr(socket.socket, n)
        return False


# --- #4 / #5 帳本 ---------------------------------------------------------------
def assert_no_dry_rows(rows: Iterable[Mapping]) -> None:
    """實單側讀資料時呼叫：看到 mode='DRY' 的列一律拒絕。"""
    for r in rows:
        if r.get("mode") == "DRY":
            raise LiveIsolationError("實單路徑讀到 mode='DRY' 的列，拒絕")


def open_readonly(path: Path) -> sqlite3.Connection:
    """對外（例如 gateway 白名單查詢）只能用這個開帳本：唯讀連線。"""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


# --- #8 命名與 live 設定檔 ---------------------------------------------------------
_LIVE_CONFIG_GLOBS = ("live*.y*ml", "live*.json", "live*.toml", "broker*", "order*.cfg",
                      "*.pfx", ".env.live")


def check_run_layout(run_dir: Path, ledger_path: Path) -> None:
    run_dir, ledger_path = Path(run_dir), Path(ledger_path)
    if "dry" not in run_dir.name.lower():
        raise SelfCheckError(f"dry run 目錄名稱必須含 'dry'：{run_dir.name}")
    if "dry" not in ledger_path.name.lower():
        raise SelfCheckError(f"dry run 帳本檔名必須含 'dry'：{ledger_path.name}")
    bad = [str(p) for p in run_dir.rglob("*") if p.is_file()
           and any(fnmatch.fnmatch(p.name.lower(), g) for g in _LIVE_CONFIG_GLOBS)]
    if bad:
        raise SelfCheckError("dry run 目錄出現實單設定檔：" + ", ".join(sorted(bad)))


def run_startup_selfcheck(*, env: Mapping[str, str], home: Optional[Path],
                          run_dir: Path, ledger_path: Path) -> None:
    check_no_credentials(env=env, home=home)
    pkg = Path(__file__).resolve().parent
    hits = scan_forbidden_imports(sorted(pkg.glob("*.py")))
    if hits:
        raise SelfCheckError("dryrun 套件含禁用 import：" + "; ".join(f"{p.name}:{n} {m}" for p, n, m in hits))
    check_run_layout(run_dir, ledger_path)


def default_selfcheck(run_dir: Path, ledger_path: Path):
    def _check():
        run_startup_selfcheck(env=os.environ, home=Path.home(), run_dir=run_dir,
                              ledger_path=ledger_path)
    return _check

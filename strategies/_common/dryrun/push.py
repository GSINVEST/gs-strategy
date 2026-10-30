"""把快照用 HTTPS PUT 推到 gateway。金鑰與網址在主機的 ~/.config/gs-dryrun/，不進 repo、不印出。

未設定時只警告、結束碼 0（排程在 gateway 端點上線前也不會失敗）；被拒或網路錯誤結束碼 2（走既有告警）。
輸出只含狀態碼與例外型別，不含金鑰、網址或回應內容。
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONF = Path.home() / ".config" / "gs-dryrun"


def push_snapshot(url: str, key: str, body: bytes, *, timeout: float = 30) -> int:
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        # Cloudflare 會擋 Python-urllib 預設的 User-Agent（403），要自報名稱
        "User-Agent": "gs-dryrun-push/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _read(env: str, default: Path):
    p = Path(os.environ.get(env, str(default)))
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dryrun.push")
    ap.add_argument("--file", required=True)
    a = ap.parse_args(argv)
    url = _read("DRYRUN_PUSH_URL_FILE", CONF / "push_url")
    key = _read("DRYRUN_PUSH_KEY_FILE", CONF / "push_key")
    if not (url and key):
        print("[WARN] 快照推送未設定（缺 push_url 或 push_key），略過")
        return 0
    try:
        code = push_snapshot(url, key, Path(a.file).read_bytes())
    except OSError as e:
        print(f"[CRIT] 快照推送失敗：{type(e).__name__}")
        return 2
    if code // 100 != 2:
        print(f"[CRIT] 快照推送被拒：HTTP {code}")
        return 2
    print(f"快照已推送：HTTP {code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

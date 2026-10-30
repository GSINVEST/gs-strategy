#!/usr/bin/env python3
"""伺服器端 pre-receive：對 refs/heads/main 強制事前登記紀律。

對應 docs/spec/host-migration-gsinvest.md §5.2。已實作：規則 1（append-only）、
2（登記檔 validate）、3（author == 推送者）、5（禁 force push／刪分支）。
尚未實作：規則 4（作者進行中登記 <= 3）與 §5.3 簽核檢查——見 README「待做」。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

PINNED = Path(os.environ.get("GS_HOOKS_REPO", "/opt/gs-hooks/repo"))
PREREG = "experiments/preregistration/"
ZERO = "0" * 40
MAIN = "refs/heads/main"


def git(*a: str) -> str:
    r = subprocess.run(["git", *a], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout


def deny(msg: str) -> None:
    print(f"[pre-receive] 拒絕：{msg}\n參見 docs/spec/host-migration-gsinvest.md §5.2", file=sys.stderr)
    sys.exit(1)


def changed_prereg_files(old: str, new: str) -> list[str]:
    files = []
    for line in git("diff", "--name-status", "-M", f"{old}..{new}").splitlines():
        parts = line.split("\t")
        path = parts[-1]
        if (path.startswith(PREREG) and path.endswith(".yaml")
                and not path.endswith((".signoff.yaml", ".closure.yaml", "TEMPLATE.yaml"))
                and parts[0][0] in "AM"):
            files.append(path)
    return files


def main() -> int:
    pusher = os.environ.get("GITEA_PUSHER_EMAIL", "").strip().lower()
    for line in sys.stdin.read().splitlines():
        old, new, ref = line.split()
        if ref != MAIN:
            continue
        if new == ZERO:
            deny("禁止刪除 main")
        if old != ZERO and subprocess.run(["git", "merge-base", "--is-ancestor", old, new]).returncode:
            deny("禁止 force push main（new 不是 old 的後代）")
        rng = f"{old}..{new}" if old != ZERO else new
        r = subprocess.run([sys.executable, str(PINNED / "scripts/check_append_only.py"),
                            "--rev-range", rng], capture_output=True, text=True, encoding="utf-8")
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            deny("違反「只准新增、不准改寫」")
        if old == ZERO:
            continue
        with tempfile.TemporaryDirectory() as td:
            for f in changed_prereg_files(old, new):
                body = git("show", f"{new}:{f}")
                p = Path(td) / Path(f).name
                p.write_text(body, encoding="utf-8")
                author = str((yaml.safe_load(body) or {}).get("author", "")).strip().lower()
                if not pusher or author != pusher:
                    deny(f"{p.name} 的 author（{author or '空'}）必須等於推送者（server 認定）")
                v = subprocess.run([sys.executable, str(PINNED / "scripts/check_preregistration.py"),
                                    str(p)], capture_output=True, text=True, encoding="utf-8",
                                   cwd=str(PINNED))
                if v.returncode:
                    print(v.stderr or v.stdout, file=sys.stderr)
                    deny(f"{p.name} 未通過登記檔驗證")
    return 0


if __name__ == "__main__":
    sys.exit(main())

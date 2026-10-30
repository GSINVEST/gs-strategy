#!/usr/bin/env python3
"""伺服器端 pre-receive：對 refs/heads/main 強制事前登記紀律。

對應 docs/spec/host-migration-gsinvest.md §5.2、§5.3。檢查：
  1. append-only（登記／簽核／結案檔只准新增）
  2. 新增或變更的登記檔通過 validate；非 open_mining 時 planned_trials <= 10
  3. 登記檔 author == 推送者（server 認定的 email）
  4. 同一作者「進行中」登記（含尚未簽核者）<= 3
  5. 禁止 force push／刪除 main
  S. 簽核檔：signer ∈ signers、signer == 推送者、signer != author、sha256 相符、同一登記僅一份
  C. 結案檔：closed_by == 推送者，且推送者是該登記的 author 或 signer

名單檔 GS_ROLES_FILE（預設 /data/config/roles.yaml，不進 repo）：`signers: [email, ...]`。
檔案不存在或沒有 signers 時，簽核一律拒絕（fail closed）。

已知限制：帳本不在 Forgejo 上，hook 看不到「已用次數」，所以規則 4 的「進行中」＝尚未結案
（有 closure 檔才算結案），已跑完配額但未結案的登記仍會佔名額，需補一份 closure 釋放。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

PINNED = Path(os.environ.get("GS_HOOKS_REPO", "/opt/gs-hooks/repo"))
ROLES = Path(os.environ.get("GS_ROLES_FILE", "/data/config/roles.yaml"))
PREREG = "experiments/preregistration/"
ZERO = "0" * 40
MAIN = "refs/heads/main"
MAX_ACTIVE = 3
MAX_PLANNED = 10
NON_REG = ("TEMPLATE.yaml",)


def git(*a: str) -> str:
    r = subprocess.run(["git", *a], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout


def git_bytes(*a: str) -> bytes:
    r = subprocess.run(["git", *a], capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip())
    return r.stdout


def deny(msg: str) -> None:
    print(f"[pre-receive] 拒絕：{msg}\n參見 docs/spec/host-migration-gsinvest.md §5.2／§5.3", file=sys.stderr)
    sys.exit(1)


def kind(path: str) -> str | None:
    """registration / signoff / closure；非登記區的檔案回 None。"""
    if not path.startswith(PREREG) or "/" in path[len(PREREG):] or not path.endswith(".yaml"):
        return None
    name = path[len(PREREG):]
    if name in NON_REG:
        return None
    if name.endswith(".signoff.yaml"):
        return "signoff"
    if name.endswith(".closure.yaml"):
        return "closure"
    return "registration"


def stem(path: str) -> str:
    n = Path(path).name
    for suf in (".signoff.yaml", ".closure.yaml", ".yaml"):
        if n.endswith(suf):
            return n[: -len(suf)]
    return n


def load_yaml(rev: str, path: str) -> dict:
    return yaml.safe_load(git("show", f"{rev}:{path}")) or {}


def changed(old: str, new: str) -> list[tuple[str, str]]:
    out = []
    for line in git("diff", "--name-status", "-M", f"{old}..{new}").splitlines():
        parts = line.split("\t")
        out.append((parts[0][0], parts[-1]))
    return out


def tree_prereg_files(rev: str) -> list[str]:
    return [p for p in git("ls-tree", "-r", "--name-only", rev, PREREG).splitlines() if kind(p)]


def signers() -> set[str]:
    try:
        doc = yaml.safe_load(ROLES.read_text(encoding="utf-8")) or {}
    except OSError:
        return set()
    return {str(e).strip().lower() for e in (doc.get("signers") or [])}


def author_of(doc: dict) -> str:
    return str(doc.get("author", "")).strip().lower()


def check_registration(new: str, path: str, pusher: str) -> None:
    doc = load_yaml(new, path)
    name = Path(path).name
    if not pusher or author_of(doc) != pusher:
        deny(f"{name} 的 author（{author_of(doc) or '空'}）必須等於推送者（server 認定）")
    family = ((doc.get("stat_plan") or {}).get("family") or "")
    planned = (doc.get("thresholds") or {}).get("planned_trials")
    if family != "open_mining" and isinstance(planned, (int, float)) and planned > MAX_PLANNED:
        deny(f"{name} 的 planned_trials={planned} 超過 {MAX_PLANNED}（除非 family 為 open_mining）")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(git("show", f"{new}:{path}"), encoding="utf-8")
        v = subprocess.run([sys.executable, str(PINNED / "scripts/check_preregistration.py"), str(p)],
                           capture_output=True, text=True, encoding="utf-8", cwd=str(PINNED))
    if v.returncode:
        print(v.stderr or v.stdout, file=sys.stderr)
        deny(f"{name} 未通過登記檔驗證")


def check_active_quota(new: str, author: str) -> None:
    files = tree_prereg_files(new)
    closed = {stem(p) for p in files if kind(p) == "closure"}
    active = [stem(p) for p in files
              if kind(p) == "registration" and stem(p) not in closed
              and author_of(load_yaml(new, p)) == author]
    if len(active) > MAX_ACTIVE:
        deny(f"{author} 進行中的登記共 {len(active)} 份（上限 {MAX_ACTIVE}）：{', '.join(sorted(active))}。"
             "請先為已完成的登記新增 closure 檔")


def check_signoff(new: str, path: str, pusher: str) -> None:
    name = Path(path).name
    doc = load_yaml(new, path)
    reg_path = f"{PREREG}{stem(path)}.yaml"
    try:
        reg_bytes = git_bytes("show", f"{new}:{reg_path}")
    except RuntimeError:
        deny(f"{name} 對應的登記檔 {Path(reg_path).name} 不存在")
    reg = yaml.safe_load(reg_bytes) or {}
    sg = signers()
    signer = str(doc.get("signer", "")).strip().lower()
    if not sg:
        deny("伺服器沒有設定 signers 名單（GS_ROLES_FILE），簽核一律拒絕")
    if doc.get("schema") != "preregistration-signoff/1":
        deny(f"{name} 的 schema 必須是 preregistration-signoff/1")
    if str(doc.get("prereg_id", "")) != stem(path):
        deny(f"{name} 的 prereg_id 必須等於檔名（{stem(path)}）")
    if signer not in sg:
        deny(f"簽核人 {signer or '空'} 不在 signers 名單")
    if signer != pusher:
        deny(f"簽核人（{signer}）必須等於推送者（{pusher or '未知'}）")
    if signer == author_of(reg):
        deny("簽核人不得是該登記的 author（不能自己簽自己）")
    want = hashlib.sha256(reg_bytes).hexdigest()
    if str(doc.get("prereg_sha256", "")).strip().lower() != want:
        deny(f"{name} 的 prereg_sha256 與登記檔不符（應為 {want}）")


def check_closure(new: str, path: str, pusher: str) -> None:
    name = Path(path).name
    doc = load_yaml(new, path)
    reg_path = f"{PREREG}{stem(path)}.yaml"
    try:
        reg = load_yaml(new, reg_path)
    except RuntimeError:
        deny(f"{name} 對應的登記檔不存在")
    closer = str(doc.get("closed_by", "")).strip().lower()
    if closer != pusher:
        deny(f"{name} 的 closed_by 必須等於推送者")
    if pusher != author_of(reg) and pusher not in signers():
        deny("只有該登記的 author 或 signer 可以結案")
    if not str(doc.get("reason", "")).strip():
        deny(f"{name} 的 reason 不可為空")


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
            continue  # 首次匯入整段歷史：只做 append-only
        new_regs = []
        for status, path in changed(old, new):
            k = kind(path)
            if k is None or status == "D":
                continue
            if k == "registration":
                check_registration(new, path, pusher)
                new_regs.append(path)
            elif k == "signoff":
                check_signoff(new, path, pusher)
            elif k == "closure":
                check_closure(new, path, pusher)
        if new_regs:
            check_active_quota(new, pusher)
        # 同一登記僅一份簽核：簽核檔名固定為 <stem>.signoff.yaml，天然唯一；再防「另名」簽核
        files = tree_prereg_files(new)
        seen: dict[str, str] = {}
        for p in files:
            if kind(p) == "signoff":
                pid = str(load_yaml(new, p).get("prereg_id", ""))
                if pid in seen and seen[pid] != p:
                    deny(f"登記 {pid} 有多份簽核檔：{seen[pid]}、{p}")
                seen[pid] = p
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""把 registry 的 screened 事件摘要成「為什麼沒過 D1」。只用標準函式庫（快照在主機系統 python 上跑）。

唯讀：只解析傳進來的事件 dict 或讀 JSONL，不寫 registry、不 import promotion 套件。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

OBSERVABLE_VERDICTS = ("blocked", "conditional")
LABEL = "未通過 D1／僅供觀察"
_THRESH = re.compile(r"^([A-Za-z]+)\s*([<>])\s*([0-9.]+)$")


def _reason(check: dict) -> str:
    name, value, thr = check.get("name", "?"), check.get("value"), check.get("threshold", "")
    m = _THRESH.match(thr or "")
    if m and isinstance(value, (int, float)):
        label, op, bound = m.groups()
        # 門檻「須大於」bound 而未達 → "DSR 0.94 < 0.95"；「須小於」bound 而未達 → "PBO 0.60 ≥ 0.5"
        return f"{label} {value:.2f} {'<' if op == '>' else '≥'} {bound}"
    if isinstance(value, (int, float)):
        return f"{name} {value:.4g}（門檻 {thr}）"
    return f"{name} 未達門檻（{thr}）"


def d1_summary(screened: dict) -> dict:
    """screened 事件 → {verdict, reasons, pending, screened_at, rules_version}。

    reasons＝status 為 blocked 的檢查（真正的失敗）；pending＝unavailable／provisional（尚未完成，不算失敗）。
    """
    rep = screened.get("report") or {}
    checks = rep.get("checks") or []
    return {
        "verdict": rep.get("verdict"),
        "reasons": [_reason(c) for c in checks if c.get("status") == "blocked"],
        "pending": [c.get("name") for c in checks if c.get("status") in ("unavailable", "provisional")],
        "screened_at": screened.get("ts"),
        "rules_version": rep.get("rules_version"),
    }


def latest_screened_for(registry: Path, strategy_id: str, sha: str) -> Optional[dict]:
    """registry 中此 strategy_id 的最新事件，若它是同 sha 的 observable screened 就回傳，否則 None。"""
    try:
        lines = Path(registry).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    last = None
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("strategy_id") == strategy_id and "event" in e:
            last = e
    if (last and last["event"] == "screened" and last.get("strategy_sha256") == sha
            and (last.get("report") or {}).get("verdict") in OBSERVABLE_VERDICTS):
        return last
    return None

"""runner 的守門：strategies/_submitted/ 底下的策略只能經核准路徑執行。

只管佇列的策略；其他路徑的研究用回測行為不變（那屬 diagnostic/screening，不在佇列範圍）。
純函式、不 import zipline，讓 runner 與測試都能用。
"""
from __future__ import annotations

import json
from pathlib import Path


def _submitted_sid(strategy_path: Path) -> tuple[Path, str] | None:
    """若路徑是 <root>/strategies/_submitted/<sid>/<file>，回傳 (root, sid)。"""
    parts = strategy_path.resolve().parts
    for i in range(len(parts) - 2):
        if parts[i] == "strategies" and parts[i + 1] == "_submitted":
            root = Path(*parts[:i])
            return root, parts[i + 2]
    return None


def require_approved_for_submitted(strategy_path: str | Path,
                                   approved_trial_id: str | None) -> None:
    hit = _submitted_sid(Path(strategy_path))
    if hit is None:
        return
    root, sid = hit
    if approved_trial_id != f"t-sub-{sid}":
        raise PermissionError(
            f"{sid} 是提交佇列的策略，只能經 `python -m strategies._common.submission "
            "approve` 執行（需要人核准並計入 trial）。")
    ledger = root / "log" / "trials.jsonl"
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("trial_id") == approved_trial_id and rec.get("approval"):
                return
    raise PermissionError(f"ledger 上找不到 {approved_trial_id} 的核准記錄，拒絕執行。")

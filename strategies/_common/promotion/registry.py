"""晉升 registry：append-only JSONL。只追加、不改、不刪。"""
from __future__ import annotations

import hashlib
import json
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .schema import GateReport

REPO = Path(__file__).resolve().parents[3]
REGISTRY = REPO / "log" / "promotions.jsonl"
PROMOTERS_ENV = "GS_PROMOTERS"


class PromotionError(Exception):
    pass


def strategy_sha256(strategy_dir: Path) -> str:
    h = hashlib.sha256()
    for name in ("strategy.py", "config.yaml"):
        data = (Path(strategy_dir) / name).read_bytes()
        h.update(name.encode("utf-8") + b"\0" + data + b"\0")
    return h.hexdigest()


def _now(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def _append(registry: Path, record: dict[str, Any]) -> None:
    registry.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with registry.open("a", encoding="utf-8") as fh:
        fh.write(line)          # 單次 write，一行一筆


def _read(registry: Path) -> list[dict[str, Any]]:
    """讀 registry。壞行（JSON 損毀、非物件）略過並發出警告，不讓整份記錄讀不出來。"""
    if not registry.is_file():
        return []
    out = []
    for lineno, line in enumerate(registry.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            rec = None
        if not isinstance(rec, dict) or "event" not in rec:
            warnings.warn(f"{registry}:{lineno} 不是有效的 registry 事件，已略過", stacklevel=2)
            continue
        out.append(rec)
    return out


def is_promoted(strategy_id: str, sha: str, registry: Path = REGISTRY) -> bool:
    """此版本（sha）目前是否處於已晉升狀態。

    同 sha 後來又被 screened 且判定 blocked，先前的 promoted 即失效。
    """
    state = False
    for e in _read(Path(registry)):
        if e.get("strategy_id") != strategy_id or e.get("strategy_sha256") != sha:
            continue
        if e["event"] == "promoted":
            state = True
        elif e["event"] == "screened" and e.get("report", {}).get("verdict") == "blocked":
            state = False
    return state


def latest(strategy_id: str, registry: Path = REGISTRY) -> dict[str, Any] | None:
    for rec in reversed(_read(Path(registry))):
        if rec.get("strategy_id") == strategy_id:
            return rec
    return None


def record_screening(
    report: GateReport,
    *,
    strategy_dir: Path,
    lineage_id: str,
    branch: str,
    framework_version: str,
    data_snapshot: str,
    cost_model_version: str,
    tags: Sequence[str] = (),
    registry: Path = REGISTRY,
    now: datetime | None = None,
) -> dict[str, Any]:
    sha = strategy_sha256(strategy_dir)
    prior = [e for e in _read(Path(registry))
             if e["event"] == "screened" and e.get("strategy_id") == report.strategy_id
             and e.get("strategy_sha256") == sha]
    rec = {
        "event": "screened",
        "ts": _now(now),
        # 同一版本被反覆篩選時留下痕跡：換輸入重跑直到過關不計入 trial，至少要看得見。
        "screening_count": len(prior) + 1,
        "previous_verdict": prior[-1]["report"]["verdict"] if prior else None,
        "strategy_id": report.strategy_id,
        "lineage_id": lineage_id,
        "branch": branch,
        "strategy_sha256": sha,
        "framework_version": framework_version,
        "data_snapshot": data_snapshot,
        "cost_model_version": cost_model_version,
        "tags": list(tags),
        "report": report.to_dict(),
    }
    _append(Path(registry), rec)
    return rec


def _promoters() -> list[str]:
    raw = os.environ.get(PROMOTERS_ENV, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def promote(
    strategy_id: str,
    *,
    strategy_dir: Path,
    by: str,
    note: str = "",
    registry: Path = REGISTRY,
    now: datetime | None = None,
) -> dict[str, Any]:
    allowed = _promoters()
    if not allowed:
        raise PromotionError(f"環境變數 {PROMOTERS_ENV} 未設定：拒絕晉升（fail closed）")
    if by not in allowed:
        raise PromotionError(f"{by!r} 不在 {PROMOTERS_ENV} 允許名單內")

    events = [e for e in _read(Path(registry)) if e.get("strategy_id") == strategy_id]
    screened = next((e for e in reversed(events) if e["event"] == "screened"), None)
    if screened is None:
        raise PromotionError(f"{strategy_id}: 找不到 screened 記錄，不能晉升")

    sha = strategy_sha256(strategy_dir)
    if sha != screened["strategy_sha256"]:
        raise PromotionError(f"{strategy_id}: 篩選後策略內容已變更，須重新篩選")
    if is_promoted(strategy_id, sha, registry):
        raise PromotionError(f"{strategy_id}: 此版本已晉升")

    verdict = screened["report"]["verdict"]
    if verdict == "blocked":
        raise PromotionError(f"{strategy_id}: 閘門判定 blocked，不得晉升（沒有人工覆寫）")
    if verdict == "conditional" and not note.strip():
        raise PromotionError(f"{strategy_id}: 閘門判定 conditional，必須附人工簽註 note")
    if screened.get("screening_count", 1) > 1 and not note.strip():
        raise PromotionError(
            f"{strategy_id}: 此版本已被篩選 {screened['screening_count']} 次"
            f"（前次判定 {screened.get('previous_verdict')}），必須附簽註說明為何重篩")

    rec = {
        "event": "promoted",
        "ts": _now(now),
        "strategy_id": strategy_id,
        "lineage_id": screened["lineage_id"],
        "branch": screened["branch"],
        "strategy_sha256": sha,
        "framework_version": screened["framework_version"],
        "data_snapshot": screened["data_snapshot"],
        "cost_model_version": screened["cost_model_version"],
        "tags": screened["tags"],
        "by": by,
        "note": note,
        "verdict": verdict,
    }
    _append(Path(registry), rec)
    return rec

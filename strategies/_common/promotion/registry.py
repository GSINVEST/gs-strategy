"""晉升 registry：append-only JSONL。只追加、不改、不刪。"""
from __future__ import annotations

import hashlib
import json
import os
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
    if not registry.is_file():
        return []
    out = []
    for line in registry.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


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
    rec = {
        "event": "screened",
        "ts": _now(now),
        "strategy_id": report.strategy_id,
        "lineage_id": lineage_id,
        "branch": branch,
        "strategy_sha256": strategy_sha256(strategy_dir),
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
    if any(e["event"] == "promoted" and e["strategy_sha256"] == sha for e in events):
        raise PromotionError(f"{strategy_id}: 此版本已晉升")

    verdict = screened["report"]["verdict"]
    if verdict == "blocked":
        raise PromotionError(f"{strategy_id}: 閘門判定 blocked，不得晉升（沒有人工覆寫）")
    if verdict == "conditional" and not note.strip():
        raise PromotionError(f"{strategy_id}: 閘門判定 conditional，必須附人工簽註 note")

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

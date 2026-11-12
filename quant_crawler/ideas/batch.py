"""S5 的閘門：批次上限、不看績效的選取、試驗數登記。

為什麼要卡：Deflated Sharpe 門檻隨試驗數 N 上升，且套用到所有既有策略。
登記是 **只追加**：ledger 與 preregistration 既有內容一律不動（scripts/check_append_only.py）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from quant_crawler.ideas.card import IdeaCard, save_card, set_status

MAX_BATCH = 20
SELECTION_RULE = "status==impl and verdict==pass_with_bounds; order by sha256(card.id); take first N (<=20); 不看績效"


def select_batch(cards: list[IdeaCard], limit: int = MAX_BATCH) -> list[IdeaCard]:
    if limit > MAX_BATCH:
        raise ValueError(f"每批上限 {MAX_BATCH}，收到 {limit}")
    eligible = [c for c in cards if c.status == "impl" and c.verdict == "pass_with_bounds"]
    eligible.sort(key=lambda c: hashlib.sha256(c.id.encode()).hexdigest())
    return eligible[:limit]


def _trial(card: IdeaCard, parent: str, now: str) -> dict:
    return {
        "schema": "research-trial/v1", "trial_id": f"t-idea-{card.id}", "parent_trial_id": parent,
        "repo": "gs-strategy", "level": "direction", "hypothesis": card.hypothesis[:200],
        "acceptance_criterion": "tquant_future 回測＋既有 validation／deflate（見 preregistration）",
        "config_ref": f"strategies/{card.id}/manifest.yaml", "state": "registered",
        "started_at": now, "finished_at": None, "disposition": "registered",
        "source_doc": f"ideas/{card.id}.md",
        "stat_decision": {"ruleset_version": "1.1", "purpose": "selection", "delta_n": 1,
                          "no_statistical_claim": False, "basis": "idea-funnel batch registration"},
    }


def _prereg(batch_id: str, n: int, now: str) -> dict:
    return {
        "schema": "preregistration/1", "id": batch_id,
        "title": f"idea 漏斗批次 {batch_id}：{n} 個單一期貨 FSM 策略的回測登記",
        "author": "", "registered_at": now, "supersedes": None, "supersedes_reason": None,
        "hypothesis": "", "economic_mechanism": "",       # 由人逐批填寫；空白時 check_preregistration 會擋
        "sources": [],
        "universe": {"instruments": ["TX", "MTX"], "bundle": "tquant_future", "filters": ""},
        "sample": {"start": "", "end": "", "contains_full_bear_market": None, "holdout": ""},
        "stat_plan": {"estimand": "sharpe", "design": "one_sample", "overlap": "overlapping",
                      "holding_periods": None, "cluster_by": None, "unit": "period",
                      "family": "bounded_multiple", "n_trials": n, "purpose": "selection"},
        "thresholds": {"primary_metric": "", "pass_condition": "", "planned_trials": n},
        "p_value_definition": None, "online_fdr": {"enabled": False, "alpha": 0.05, "exhausted_floor": None},
        "falsification": [],
        "costs": {"per_contract": {"TX": 200, "MTX": 100}, "slippage_points": 6.0, "tax_note": "",
                  "stress_test": "成本乘二"},
        "known_limitations": ["Gslang v0.1 未模擬換月價差、保證金、期交稅（見 IR not_modeled）"],
    }


def register_batch(cards: list[IdeaCard], root: Path, now: str, ledger: Path, prereg_dir: Path,
                   batch_id: str | None = None, cards_dir: Path | None = None) -> dict:
    if len(cards) > MAX_BATCH:
        raise ValueError(f"每批上限 {MAX_BATCH}，收到 {len(cards)}")
    if not cards:
        raise ValueError("空批次")
    batch_id = batch_id or f"idea-batch-{now[:10]}"
    root.mkdir(parents=True, exist_ok=True)
    bfile = root / f"{batch_id}.json"
    pfile = prereg_dir / f"{batch_id}.yaml"
    if bfile.exists() or pfile.exists():
        raise FileExistsError(f"{batch_id} 已登記；登記只追加、不覆蓋")
    prereg_dir.mkdir(parents=True, exist_ok=True)
    info = {"batch_id": batch_id, "registered_at": now, "n_trials": len(cards),
            "selection_rule": SELECTION_RULE, "members": [c.id for c in cards]}
    root_rec = {"schema": "research-trial/v1", "trial_id": f"t-{batch_id}", "parent_trial_id": None,
                "repo": "gs-strategy", "level": "direction",
                "hypothesis": f"{batch_id}：{len(cards)} 個 idea 漏斗策略回測登記",
                "acceptance_criterion": "見 preregistration", "config_ref": None, "state": "registered",
                "started_at": now, "finished_at": None, "disposition": "registered",
                "source_doc": f"experiments/preregistration/{batch_id}.yaml",
                "stat_decision": {"ruleset_version": "1.1", "purpose": "selection", "delta_n": 0,
                                  "no_statistical_claim": True, "basis": "batch root"}}
    lines = [root_rec] + [_trial(c, root_rec["trial_id"], now) for c in cards]
    with ledger.open("a", encoding="utf-8", newline="\n") as f:        # 只追加
        for r in lines:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    bfile.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    pfile.write_text(yaml.safe_dump(_prereg(batch_id, len(cards), now), allow_unicode=True, sort_keys=False),
                     encoding="utf-8", newline="\n")
    for c in cards:
        set_status(c, "bt_registered")
        c.batch_id = batch_id
        if cards_dir is not None:
            save_card(c, cards_dir)
    return info

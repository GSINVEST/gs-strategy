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
    if limit < 1:
        raise ValueError(f"批次大小至少為 1，收到 {limit}")
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
        # holding_periods 刻意為 None：overlap=overlapping 需要持有期，沒有合法且不捏造的值，
        # 須由人於簽核時填寫（否則 check_preregistration 以「推導不出處方」擋下）。
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


def _ledger_trial_ids(ledger: Path) -> set[str]:
    """既有 ledger 的所有 trial_id。壞行略過、不崩潰（只為了判重，不是稽核）。"""
    if not ledger.exists():
        return set()
    ids: set[str] = set()
    for line in ledger.read_bytes().decode("utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("trial_id"), str):
            ids.add(rec["trial_id"])
    return ids


def _append_lines(ledger: Path, records: list[dict]) -> None:
    """只追加。既有檔案結尾若缺換行，先補一個，免得第一行新紀錄黏在舊行後面。"""
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode("utf-8")
    prefix = b""
    if ledger.exists() and ledger.stat().st_size > 0:
        with ledger.open("rb") as f:
            f.seek(-1, 2)
            if f.read(1) != b"\n":
                prefix = b"\n"
    with ledger.open("ab") as f:
        f.write(prefix + payload)


def register_batch(cards: list[IdeaCard], root: Path, now: str, ledger: Path, prereg_dir: Path,
                   batch_id: str | None = None, cards_dir: Path | None = None) -> dict:
    """登記一批 idea 卡的回測試驗。

    任何寫入之前會先驗證全部條件（卡片合格、id 不重複、ledger 內沒有這些卡或這個批次的
    trial_id、批次檔與 prereg 檔不存在），任一不符即丟例外，磁碟與卡片都不會被改動。
    寫入順序：批次檔 → prereg 草稿 → ledger 追加（最不可逆，放最後）；ledger 追加失敗時
    會刪除前兩個檔案。卡片狀態（bt_registered、batch_id、save_card）只在 ledger 成功後才轉移。

    預設 batch_id 以日期為單位（``idea-batch-<now[:10]>``）；同一天的第二批必須明確傳入
    batch_id，否則會因檔案已存在而被拒絕。
    """
    if not cards:
        raise ValueError("空批次")
    if len(cards) > MAX_BATCH:
        raise ValueError(f"每批上限 {MAX_BATCH}，收到 {len(cards)}")
    ids = [c.id for c in cards]
    if len(set(ids)) != len(ids):
        raise ValueError(f"批次內有重複卡片：{sorted({i for i in ids if ids.count(i) > 1})}")
    for c in cards:
        if c.status != "impl" or c.verdict != "pass_with_bounds":
            raise ValueError(
                f"{c.id} 不合格（需 status=impl 且 verdict=pass_with_bounds）："
                f"status={c.status!r} verdict={c.verdict!r}")

    batch_id = batch_id or f"idea-batch-{now[:10]}"
    bfile = root / f"{batch_id}.json"
    pfile = prereg_dir / f"{batch_id}.yaml"
    for d in (root, prereg_dir):
        if d.exists() and not d.is_dir():
            raise NotADirectoryError(f"{d} 已存在且不是目錄")
    if bfile.exists() or pfile.exists():
        raise FileExistsError(f"{batch_id} 已登記；登記只追加、不覆蓋")

    taken = _ledger_trial_ids(ledger)
    if f"t-{batch_id}" in taken:
        raise FileExistsError(f"{batch_id} 的批次根紀錄已在 ledger 內")
    clashes = [c.id for c in cards if f"t-idea-{c.id}" in taken]
    if clashes:
        raise ValueError(f"以下卡片已登記過，不得重複計入 N：{clashes}")

    # 全部驗證通過後才建立目錄（目錄本身是唯一可能殘留的副作用，且是空目錄）。
    root.mkdir(parents=True, exist_ok=True)
    prereg_dir.mkdir(parents=True, exist_ok=True)

    info = {"batch_id": batch_id, "registered_at": now, "n_trials": len(cards),
            "selection_rule": SELECTION_RULE, "members": ids}
    root_rec = {"schema": "research-trial/v1", "trial_id": f"t-{batch_id}", "parent_trial_id": None,
                "repo": "gs-strategy", "level": "direction",
                "hypothesis": f"{batch_id}：{len(cards)} 個 idea 漏斗策略回測登記",
                "acceptance_criterion": "見 preregistration", "config_ref": None, "state": "registered",
                "started_at": now, "finished_at": None, "disposition": "registered",
                "source_doc": f"experiments/preregistration/{batch_id}.yaml",
                "stat_decision": {"ruleset_version": "1.1", "purpose": "selection", "delta_n": 0,
                                  "no_statistical_claim": True, "basis": "batch root"}}
    records = [root_rec] + [_trial(c, root_rec["trial_id"], now) for c in cards]
    try:
        bfile.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        pfile.write_text(yaml.safe_dump(_prereg(batch_id, len(cards), now), allow_unicode=True,
                                        sort_keys=False),
                         encoding="utf-8", newline="\n")
        _append_lines(ledger, records)          # 最不可逆的一步，放最後
    except BaseException:
        for f in (bfile, pfile):
            f.unlink(missing_ok=True)
        raise

    for c in cards:
        set_status(c, "bt_registered")
        c.batch_id = batch_id
        if cards_dir is not None:
            save_card(c, cards_dir)
    return info

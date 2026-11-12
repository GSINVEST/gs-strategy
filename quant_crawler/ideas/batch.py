"""S5 的閘門：批次上限、不看績效的選取、試驗數登記。

為什麼要卡：Deflated Sharpe 門檻隨試驗數 N 上升，且套用到所有既有策略。
登記是 **只追加**：ledger 與 preregistration 既有內容一律不動（scripts/check_append_only.py）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

from quant_crawler.ideas.card import _ID_RE, IdeaCard, save_card, set_status

MAX_BATCH = 20
SELECTION_RULE = "status==impl and verdict==pass_with_bounds; order by sha256(card.id); take first N (<=20); 不看績效"


_BATCH_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_locks_guard = threading.Lock()
_locks: dict = {}


@contextmanager
def _exclusive(path: Path):
    """與 strategies/_common/graph/ledger.py 同一套鎖協定：對 `<ledger>.lock` 取 OS 層 advisory lock。

    不直接 import graph.ledger（會牽動整個 graph 套件與其相依），所以複製同一協定：同樣的鎖檔路徑
    （path.with_suffix(suffix + '.lock')）、flock／msvcrt 同一個位元組範圍，圖引擎與本模組因此互斥。
    行程崩潰時 OS 會釋放鎖，不會留下過期鎖。
    """
    key = str(path.resolve())
    with _locks_guard:
        local = _locks.setdefault(key, threading.RLock())
    with local:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(path.suffix + ".lock").open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.01)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def check_batch_id(batch_id: str) -> None:
    """批次 id 須合法，且不得與卡片 trial_id 命名空間（t-idea-<card_id>）撞名：
    去掉 `idea-` 前綴後若符合 card id 規則（例如 `idea-bar`），其批次根紀錄 `t-idea-bar` 會與卡 `bar` 的 trial 相同。"""
    if not isinstance(batch_id, str) or not _BATCH_ID_RE.match(batch_id):
        raise ValueError(f"batch_id 必須符合 {_BATCH_ID_RE.pattern}：{batch_id!r}")
    if batch_id.startswith("idea-") and _ID_RE.match(batch_id[len("idea-"):]):
        raise ValueError(f"batch_id {batch_id!r} 會與卡片 {batch_id[len('idea-'):]!r} 的 trial_id "
                         f"t-{batch_id} 撞名；請換名稱（預設的 idea-batch-<日期> 不受影響）")


def select_batch(cards: list[IdeaCard], limit: int = MAX_BATCH, exclude_ids=()) -> list[IdeaCard]:
    if limit > MAX_BATCH:
        raise ValueError(f"每批上限 {MAX_BATCH}，收到 {limit}")
    if limit < 1:
        raise ValueError(f"批次大小至少為 1，收到 {limit}")
    skip = set(exclude_ids)
    eligible = [c for c in cards if c.status == "impl" and c.verdict == "pass_with_bounds" and c.id not in skip]
    eligible.sort(key=lambda c: hashlib.sha256(c.id.encode()).hexdigest())
    return eligible[:limit]


def _trial(card: IdeaCard, parent: str, now: str, batch_id: str = "", n_running: int | None = None,
           config_sha256: str | None = None) -> dict:
    """登記時的 trial。沿用 graph/ledger.py 的 pending 慣例：N 先計入（delta_n=1），但檢定處方要等回測
    補完成行才推得出來，所以 rule_id=None、inference_available=False，decision_path 只含已宣告事實，
    **不捏造 n_eff**（因此 audit_ledger 會對這些行回報「缺 n_eff」，直到完成行補上為止）。"""
    path = {"estimand": "sharpe", "design": "one_sample", "overlap": "overlapping",
            "family": "bounded_multiple", "purpose": "selection"}
    if n_running is not None:
        path["n_trials"] = n_running
    rec = {
        "schema": "research-trial/v1", "trial_id": f"t-idea-{card.id}", "parent_trial_id": parent,
        "repo": "gs-strategy", "level": "direction", "hypothesis": card.hypothesis[:200],
        "acceptance_criterion": "tquant_future 回測＋既有 validation／deflate（見 preregistration）",
        "config_ref": f"ideas/_work/{card.id}/bundle/config.yaml", "state": "registered",
        "started_at": now, "finished_at": None, "disposition": "registered", "purpose": "selection",
        "source_doc": f"ideas/{card.id}.md",
        "stat_decision": {"ruleset_version": "1.1", "purpose": "selection", "delta_n": 1,
                          "status": "pending", "rule_id": None, "inference_available": False,
                          "decision_path": path, "provenance": {"status": "registered_before_backtest"},
                          "preregistration_id": batch_id,
                          "no_statistical_claim": False, "basis": "idea-funnel batch registration"},
    }
    if config_sha256:
        rec["config_sha256"] = config_sha256
    return rec


def _prereg(batch_id: str, n: int, now: str, start: str = "", end: str = "") -> dict:
    return {
        "schema": "preregistration/1", "id": batch_id,
        "title": f"idea 漏斗批次 {batch_id}：{n} 個單一期貨 FSM 策略的回測登記",
        "author": "", "registered_at": now, "supersedes": None, "supersedes_reason": None,
        "hypothesis": "", "economic_mechanism": "",       # 由人逐批填寫；空白時 check_preregistration 會擋
        "sources": [],
        "universe": {"instruments": ["TX", "MTX"], "bundle": "tquant_future", "filters": ""},
        "sample": {"start": start, "end": end, "contains_full_bear_market": None, "holdout": ""},
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


def ledger_idea_card_ids(ledger: Path) -> set[str]:
    """ledger 內所有已登記的 idea 卡 id（trial_id 形如 t-idea-<card_id>）。"""
    ids = {t[len("t-idea-"):] for t in _ledger_trial_ids(ledger) if t.startswith("t-idea-")}
    return {i for i in ids if _ID_RE.match(i)}      # 預設批次根紀錄 t-idea-batch-<日期> 含連字號，不是卡片 id


def _selection_count(ledger: Path) -> int:
    """ledger 內 selection 母體 N：同 trial_id 取最後一行的 delta_n 加總（與 audit_ledger 同口徑）。"""
    if not ledger.exists():
        return 0
    latest: dict = {}
    for line in ledger.read_bytes().decode("utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("trial_id"):
            latest[rec["trial_id"]] = rec
    n = 0
    for rec in latest.values():
        sd = rec.get("stat_decision")
        if isinstance(sd, dict):
            try:
                n += int(sd.get("delta_n", 0))
            except (TypeError, ValueError):
                pass
    return n


def register_batch(cards: list[IdeaCard], root: Path, now: str, ledger: Path, prereg_dir: Path,
                   batch_id: str | None = None, cards_dir: Path | None = None,
                   config_shas: dict | None = None, sample: tuple[str, str] | None = None) -> dict:
    """登記一批 idea 卡的回測試驗。

    任何寫入之前會先驗證全部條件（卡片合格、id 不重複、ledger 內沒有這些卡或這個批次的
    trial_id、批次檔與 prereg 檔不存在），任一不符即丟例外，磁碟與卡片都不會被改動。
    「ledger 判重＋三個寫入」整段包在 ledger 的排他鎖內（與 graph/ledger.py 同協定），
    避免兩個行程同時登記同一批。
    寫入順序：批次檔 → prereg 草稿 → ledger 追加（最不可逆，放最後）；ledger 追加失敗時
    會刪除前兩個檔案。卡片狀態（bt_registered、batch_id、save_card）只在 ledger 成功後才轉移。

    config_shas：{card_id: staging bundle sha256}，記入各 trial 的 config_sha256（入庫時比對）。
    sample：(start, end) 寫入 prereg 草稿的 sample，staging 的回測起訖同源。

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

    batch_id = f"idea-batch-{now[:10]}" if batch_id is None else batch_id
    check_batch_id(batch_id)
    config_shas = config_shas or {}
    bfile = root / f"{batch_id}.json"
    pfile = prereg_dir / f"{batch_id}.yaml"
    for d in (root, prereg_dir):
        if d.exists() and not d.is_dir():
            raise NotADirectoryError(f"{d} 已存在且不是目錄")
    start, end = sample or ("", "")

    with _exclusive(ledger):
        if bfile.exists() or pfile.exists():
            raise FileExistsError(f"{batch_id} 已登記；登記只追加、不覆蓋")
        taken = _ledger_trial_ids(ledger)
        if f"t-{batch_id}" in taken:
            raise FileExistsError(f"{batch_id} 的批次根紀錄已在 ledger 內")
        clashes = [c.id for c in cards if f"t-idea-{c.id}" in taken]
        if clashes:
            raise ValueError(f"以下卡片已登記過，不得重複計入 N：{clashes}")
        n_before = _selection_count(ledger)

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
        records = [root_rec] + [
            _trial(c, root_rec["trial_id"], now, batch_id, n_before + i + 1, config_shas.get(c.id))
            for i, c in enumerate(cards)]
        try:
            bfile.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            pfile.write_text(yaml.safe_dump(_prereg(batch_id, len(cards), now, start, end),
                                            allow_unicode=True, sort_keys=False),
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

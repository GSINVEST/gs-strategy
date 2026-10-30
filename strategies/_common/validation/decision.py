"""從宣告的資料性質推導檢定處方——檢定不是被選出來的，是被推導出來的。

為什麼要有這個模組
------------------
《策略研究統計檢定規範》是散文。散文能寫清楚「重疊持有期一律 Newey-West」，
但擋不住一件事：研究者（或 agent）先看到結果，再回頭挑一個讓結果顯著的檢定。
那不是不守規範，那是**在規範允許的範圍內選擇**——因為散文沒有規定「面對這組
資料性質，你只能用哪一個檢定」。

本模組把那個選擇拿掉。呼叫端宣告的是**事實**（資料型態、幾組、重疊否、
Jarque-Bera 過不過、Ljung-Box 過不過、ledger 上的 N 是多少），拿回來的是
**處方**（用哪個檢定、標準誤怎麼修、門檻多少、最少要幾個有效樣本）。
處方是事實的函數，沒有自由度。

這帶來三件散文做不到的事：

1. **要換檢定，就得改一個事實。** 而事實是可以被獨立稽核的——JB 的 p 值、
   Ljung-Box 的 p 值、ledger 的行數，每一個都能被第三者重算。
2. **判決可回溯。** trial 記錄裡存下 ``decision_path``（那組事實）與
   ``rule_id``（推導結果）。稽核者重跑 :func:`resolve` 就能確認當時用的檢定
   確實是規則集規定的那一個，見 :func:`audit_record`。
3. **不確定時拒答，而不是套預設。** 見下。

fail-closed 的邊界劃在哪裡
--------------------------
拒答的成本是研究者要多跑一個前置檢定；套預設的成本是「沒檢查」自動得到最寬鬆
的待遇。所以規則是：**只有當預設方向是保守的（會讓門檻更嚴、結論更難成立），
才允許預設。**

* ``normal`` 缺值 → 併入 ``no``。規範 §零 已明訂預設立場是「台股日報酬非常態」，
  走無母數是保守方向，所以這個預設是規範的一部分，不是省事。
* ``autocorr`` 缺值 → **拒答**。iid 是唯一會讓標準誤變小的選項；預設 iid 等於
  獎勵沒跑 Ljung-Box 的人。
* ``n_eff`` 缺值 → **拒答**。它沒有保守方向可走：猜大會讓最小樣本閘門形同虛設，
  猜小會把真結論錯殺成「樣本不足」。

規則集本身在 ``rulesets/stat-ruleset-<version>.yaml``。改規則集等同修訂規範，
version 要跟著跳，而**既有 trial 記錄裡的 ruleset 版本不得回頭改寫**——舊判決
依舊版規則成立，這是可回溯性的前提。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

RULESET_DIR = Path(__file__).with_name("rulesets")
DEFAULT_RULESET_VERSION = "1.1"


class DecisionError(Exception):
    """本模組所有錯誤的共同基底。"""


class UnderdeterminedError(DecisionError):
    """事實不足以推導處方——拒答，不套預設。

    這不是失敗，是規則集在說「你還少跑一個前置檢定」。訊息裡一律附上
    「該去跑什麼」，因為只講「缺 autocorr」對呼叫端沒有可執行的下一步。
    """


class RulesetError(DecisionError):
    """規則集本身壞掉：沒有規則命中、或不只一條命中。

    這兩種都刻意不用「最具體者勝」之類的排序去掩蓋。規則集應該是一個真正的
    分割；有洞或有重疊是規則集的 bug，靜默挑一條會讓那個 bug 永遠不被發現。
    """


@lru_cache(maxsize=4)
def load_ruleset(version: str = DEFAULT_RULESET_VERSION) -> dict:
    path = RULESET_DIR / f"stat-ruleset-{version}.yaml"
    if not path.is_file():
        raise RulesetError(f"找不到規則集：{path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or data.get("schema") != "stat-ruleset/1":
        raise RulesetError(f"{path} 不是 stat-ruleset/1")
    return data


# --------------------------------------------------------------------------
# 事實
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Facts:
    """推導處方所需的全部事實。

    每個欄位對應規則集 ``axes`` 的一個軸。刻意用 dataclass 而不是 dict，
    是為了讓「少宣告一個事實」在 import 時就變成 TypeError，而不是在
    resolve 裡才變成一個可以被 ``.get()`` 吞掉的 None。
    """

    estimand: str
    design: str
    overlap: str
    family: str
    purpose: str
    n_eff: int | None = None
    autocorr: str = "unknown"
    normal: str = "unknown"
    n_trials: int | None = None
    holding_periods: int | None = None
    cluster_by: str | None = None
    unit: str | None = None          # trade / event / period —— 決定哪道 gate 適用
    preregistered: bool = True

    def as_path(self) -> dict:
        """decision_path：寫進 trial 記錄、供日後重算的那組事實。"""
        return {k: v for k, v in asdict(self).items() if v is not None}


# --------------------------------------------------------------------------
# 處方
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Prescription:
    rule_id: str
    ruleset_version: str
    base_id: str
    se_id: str
    threshold_id: str
    primary_test: str
    alternative_test: str | None
    tie_break: str
    se_correction: str
    se_lag: int | None
    interval: str | None
    bootstrap: dict | None
    threshold: str
    multiplicity_method: str
    online_fdr_required: bool
    n_trials_used: int
    delta_n: int
    may_be_selected: bool
    max_verdict: str
    min_n_eff: int | None
    gate_status: str
    gate_notes: tuple[str, ...]
    must_report: tuple[str, ...]
    forbids: tuple[str, ...]
    preconditions: tuple[str, ...]
    notes: tuple[str, ...]
    citations: tuple[str, ...]

    def to_record(self) -> dict:
        """處方的可序列化形式，供 trial 記錄的 ``stat_decision`` 區塊使用。"""
        return asdict(self)


# --------------------------------------------------------------------------
# 槽位解析
# --------------------------------------------------------------------------

def _matches(when: Mapping[str, Any], facts: Facts) -> bool:
    """``when`` 的每個約束都要滿足才算命中。

    值可以是純量或 list（任一即可）；``any`` 是萬用字元。數值型的
    ``<axis>_min`` / ``<axis>_max`` 用來表達 n_trials 這種區間約束。
    """
    for key, want in when.items():
        if want == "any":
            continue
        if key.endswith("_min") or key.endswith("_max"):
            axis = key.rsplit("_", 1)[0]
            got = getattr(facts, axis, None)
            if got is None:
                return False
            if key.endswith("_min") and got < want:
                return False
            if key.endswith("_max") and got > want:
                return False
            continue
        got = getattr(facts, key, None)
        if isinstance(want, list):
            if got not in want:
                return False
        elif got != want:
            return False
    return True


def _pick_one(rules: Sequence[Mapping], facts: Facts, slot: str) -> Mapping:
    hits = [r for r in rules if _matches(r.get("when", {}), facts)]
    if not hits:
        raise RulesetError(
            f"{slot} 槽位沒有規則命中：{facts.as_path()}。"
            f"規則集有洞——請補規則，不要在呼叫端硬塞一個檢定。"
        )
    if len(hits) > 1:
        ids = ", ".join(r.get("id", "?") for r in hits)
        raise RulesetError(
            f"{slot} 槽位有 {len(hits)} 條規則同時命中（{ids}）：{facts.as_path()}。"
            f"規則集不是一個分割——請把 when 條件改到互斥，不要靠排序挑一條。"
        )
    return hits[0]


def _validate_axes(ruleset: dict, facts: Facts) -> None:
    axes = ruleset["axes"]
    for name, spec in axes.items():
        value = getattr(facts, name, None)
        if value is None:
            if spec.get("required"):
                doc = str(spec.get("doc") or "").strip().splitlines()
                hint = f"（{doc[0]}）" if doc else ""
                raise UnderdeterminedError(
                    f"缺必要事實 `{name}`{hint}。" + _remedy(name)
                )
            continue
        allowed = spec.get("values")
        if isinstance(allowed, dict):
            allowed = list(allowed)
        if isinstance(allowed, list) and value not in allowed:
            raise DecisionError(
                f"`{name}` 的值 {value!r} 不在規則集允許的 {allowed} 之內。"
            )


_REMEDY = {
    "n_eff": (
        " 請先算有效獨立觀測數：重疊取樣、同日事件群聚、同一場崩盤的多個超越值"
        "都要先 decluster，不得以資料列數代入。"
    ),
    "autocorr": (
        " 請先跑 Ljung-Box（tradability.ljung_box_returns）並把結論宣告進來。"
        "此處不預設 iid——那會讓沒檢查的人自動得到最寬鬆的標準誤。"
    ),
    "overlap": " 請宣告取樣是否重疊；重疊時一併給 holding_periods。",
    "family": " 請讀 trial ledger 決定這是第幾次檢定，不得自估。",
    "purpose": " 請宣告 screening / diagnostic / selection——它決定這次進不進 N。",
}


def _remedy(name: str) -> str:
    return _REMEDY.get(name, "")


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def resolve(facts: Facts, version: str = DEFAULT_RULESET_VERSION) -> Prescription:
    """由事實推導檢定處方。這個函式沒有自由度：同樣的事實必然得到同樣的處方。"""
    rs = load_ruleset(version)
    _validate_axes(rs, facts)

    notes: list[str] = []
    cites: list[str] = []

    # --- base：估計量 × 設計 -> 檢定家族 -----------------------------------
    base = _pick_one(rs["base"], facts, "base")
    if base.get("cite"):
        cites.append(str(base["cite"]))

    # --- normal：決定主檢定是參數還是無母數 --------------------------------
    # 規範 §零 預設立場：以無母數為主檢定，t 並列供對照，不一致時以無母數為準。
    normal = facts.normal
    if normal == "unknown":
        normal = "no"
        notes.append(
            "normal 未宣告，依規範 §零 預設立場併入非常態（保守方向）；"
            "若要走參數主檢定，請先跑 Jarque-Bera 並宣告 normal=yes。"
        )
    parametric = base.get("parametric")
    nonparametric = base.get("nonparametric")
    if normal == "no" and nonparametric:
        primary, alternative = nonparametric, parametric
        tie_break = "兩者不一致時以無母數為準（規範 §零）"
    else:
        primary, alternative = parametric, nonparametric
        tie_break = (
            "兩者不一致時以無母數為準（規範 §零）" if nonparametric
            else "本估計量無無母數並列版本：" + str(base.get("nonparametric_absent_because", "")).strip()
        )
    if primary is None:
        primary = nonparametric
        alternative = None
        notes.append(str(base.get("parametric_absent_because", "")).strip())

    # 條件式改走無母數（例：卡方格子期望值 < 5 -> Fisher；n < 2000 -> Shapiro）
    if base.get("switch_to_nonparametric_when"):
        notes.append(
            f"條件式切換：{base['switch_to_nonparametric_when']} 時改用 {nonparametric}。"
        )

    # --- se：重疊 × 自相關 -> 標準誤修正 -----------------------------------
    se = _pick_one(rs["se_policy"], facts, "se_policy")
    if se["correction"] == "REFUSE":
        raise UnderdeterminedError(str(se["why"]).strip())
    need = se.get("requires_fact")
    if need and getattr(facts, need, None) is None:
        raise UnderdeterminedError(
            f"標準誤規則 {se['id']} 需要 `{need}`，但未宣告。{_remedy(need)}"
        )
    se_lag = _se_lag(se, facts)
    if se.get("note"):
        notes.append(str(se["note"]).strip())

    # --- threshold：family × n_trials -> 門檻 ------------------------------
    mult = rs["multiplicity"]
    facts_for_threshold = facts
    if not facts.preregistered:
        # 規範 §一 關卡零：沒有事前登記的顯著結果不是無效，是降級。
        pen = mult["no_preregistration_penalty"]
        facts_for_threshold = _replace(facts, family="open_mining")
        notes.append(
            f"未事前登記 -> family 降級為 open_mining，標記「{pen['label']}」。"
            f"{pen['additional']}"
        )
    if facts_for_threshold.family == "bounded_multiple" and facts.n_trials is None:
        # 這一條要先於 _pick_one：bounded_multiple 的兩條門檻規則都以 n_trials
        # 分界，缺值會變成「零條命中」而報成規則集有洞，把使用者指向錯的地方。
        raise UnderdeterminedError(
            "family=bounded_multiple 必須宣告 n_trials——門檻要嘛是 0.05/N（N<=5），"
            "要嘛是 |t|>=3（N>=6），沒有 N 就決定不了是哪一個。"
            " 請讀 trial ledger 取實際嘗試過的組數，包含跑壞的、放棄的、"
            "以及結果難看沒寫進報告的那些。"
        )
    thr = _pick_one(mult["thresholds"], facts_for_threshold, "threshold")
    n_trials = facts.n_trials if facts.n_trials is not None else 1
    threshold_text = str(thr["rule"])
    if "n_trials" in threshold_text:
        threshold_text = f"{threshold_text}  =>  p < {0.05 / n_trials:.5g}"
    if thr.get("cite"):
        cites.append(str(thr["cite"]))
    online_fdr_required = "online_fdr" in (thr.get("also_required") or ())
    if thr.get("also_required"):
        notes.append(f"另須執行：{', '.join(thr['also_required'])}")
    if online_fdr_required:
        # open_mining 的 |t|>=3 是一個固定門檻，但連續挖掘的母體是**線上**成長的
        # ——固定門檻無法表達「你已經燒掉多少搜尋預算」。所以這一支還要走 ADDIS：
        # 實際門檻取兩者中較嚴的那個。
        notes.append(
            "連續挖掘型還須跑線上 FDR：把每個 selection trial 的 p 值依時序投進 "
            "validation.online_fdr.AddisBudget（或 budget_from_ledger 重播 ledger），"
            "實際門檻取 |t|>=3 與該次 alpha_t 中較嚴者。alpha_t 衰減到你設的 floor "
            "以下即為搜尋預算耗盡，應停機而非繼續加變體。"
            "p 值的定義必須在 pre-registration 裡凍結——把 anytime-valid 直接套在 "
            "Sharpe 或策略搜尋上的論文查無，這是自己接線不是照抄。"
        )

    # --- N 記帳：purpose 決定進不進母體 ------------------------------------
    acct = mult["n_accounting"][facts.purpose]
    if facts.purpose != "selection":
        # 非 selection 仍會解析出一個門檻（規則集的 threshold 槽位不看 purpose），
        # 但那個門檻在這裡不是判決閘門。明講出來，否則報告上並排的「|t| >= 2」
        # 很容易被讀成「這個候選過關了」。
        notes.append(
            f"purpose={facts.purpose}：本次不計入 N（ΔN=0），門檻僅供參考，"
            f"判決上限為「{acct['max_verdict']}」，且不得被選為最終策略。"
        )

    # --- gates：最小有效樣本 ------------------------------------------------
    min_n, gate_status, gate_notes = _apply_gates(rs["gates"], facts)

    # --- 合成 ---------------------------------------------------------------
    slots = (version, base["id"], se["id"], thr["id"], normal, facts.purpose)
    rid = _rule_id(slots)

    return Prescription(
        rule_id=rid,
        ruleset_version=version,
        base_id=base["id"],
        se_id=se["id"],
        threshold_id=thr["id"],
        primary_test=str(primary),
        alternative_test=str(alternative) if alternative else None,
        tie_break=tie_break,
        se_correction=str(se["correction"]),
        se_lag=se_lag,
        interval=base.get("interval"),
        bootstrap=rs["bootstrap_spec"] if _uses_bootstrap(base) else None,
        threshold=threshold_text,
        multiplicity_method=thr["id"],
        online_fdr_required=online_fdr_required,
        n_trials_used=n_trials,
        delta_n=int(acct["delta_n"]),
        may_be_selected=bool(acct["may_be_selected"]),
        max_verdict=str(acct["max_verdict"]),
        min_n_eff=min_n,
        gate_status=gate_status,
        gate_notes=tuple(gate_notes),
        must_report=tuple(base.get("must_report", ())) + tuple(rs["disclosure_required"]),
        forbids=tuple(
            f"{f['test']}：{f['why']}" for f in base.get("forbids", ())
        ) + tuple(
            f"{f['method']}：{f['why']}" for f in rs["bootstrap_spec"].get("forbidden", ())
            if _uses_bootstrap(base)
        ),
        preconditions=tuple(base.get("preconditions", ())),
        notes=tuple(n for n in notes if n),
        citations=tuple(cites),
    )


def _replace(facts: Facts, **kw) -> Facts:
    data = asdict(facts)
    data.update(kw)
    return Facts(**data)


def _uses_bootstrap(base: Mapping) -> bool:
    interval = base.get("interval") or ""
    return "bootstrap" in str(interval).lower() or "蒙地卡羅" in str(interval)


def _se_lag(se: Mapping, facts: Facts) -> int | None:
    rule = se.get("lag_rule")
    if not rule:
        return None
    if rule == "holding_periods - 1":
        return max(int(facts.holding_periods) - 1, 0)
    if rule.startswith("floor(4"):
        # Newey-West 的標準經驗法則 floor(4 * (n/100)^(2/9))
        if facts.n_eff is None:
            return None
        return int(math.floor(4 * (facts.n_eff / 100) ** (2 / 9)))
    return None


def _apply_gates(gates: Sequence[Mapping], facts: Facts) -> tuple[int | None, str, list[str]]:
    """最小樣本閘門。

    未達門檻回傳的 status 是 ``blocked``，不是 ``fail``——「樣本不足以宣稱」
    與「檢定不顯著」是兩件事，在報告裡必須寫成不同的話，所以在型別上也分開。
    """
    applicable = [g for g in gates if _gate_applies(g, facts)]
    notes: list[str] = []
    min_n: int | None = None
    status = "ok"
    for g in applicable:
        floor = g.get("min_n_eff")
        if floor is None:
            if g.get("requirement"):
                notes.append(f"{g['id']}：需人工確認——{g['requirement']}（{g['on_fail']}）")
            if g.get("reference_line"):
                notes.append(f"{g['id']}：參考線 {g['reference_line']}（{g['on_fail']}）")
            continue
        min_n = floor if min_n is None else max(min_n, floor)
        if facts.n_eff is not None and facts.n_eff < floor:
            soft = g.get("soft_floor")
            if soft is not None and facts.n_eff >= soft:
                status = "provisional" if status == "ok" else status
                notes.append(
                    f"{g['id']}：n_eff={facts.n_eff} 介於 {soft} 與 {floor} 之間"
                    f"——{g['on_fail']}"
                )
            else:
                status = "blocked"
                notes.append(
                    f"{g['id']}：n_eff={facts.n_eff} < {floor}——{g['on_fail']}（{g['why']}）"
                )
    return min_n, status, notes


def _gate_applies(gate: Mapping, facts: Facts) -> bool:
    when = gate.get("when", {})
    if when.get("estimand") == "any":
        when = {k: v for k, v in when.items() if k != "estimand"}
    return _matches(when, facts)


def _rule_id(slots: tuple[str, ...]) -> str:
    """處方的指紋。

    刻意由槽位明碼拼成再取 hash，而不是對整份處方取 hash：處方裡的說明文字
    日後可能潤飾，但只要五個槽位不變，判決就是同一個判決。rule_id 若隨措辭
    變動，歷史 trial 就無法跨版本比對。
    """
    body = "|".join(str(s) for s in slots)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]
    return f"sr{slots[0]}-{digest}"


# --------------------------------------------------------------------------
# 稽核：可回溯性的實作
# --------------------------------------------------------------------------

def audit_record(record: Mapping[str, Any]) -> list[str]:
    """重算一筆 trial 記錄的檢定處方，回報與記載不符之處。

    這是整套設計的收口。前面所有紀律都靠「呼叫端誠實宣告」，而這個函式讓
    不誠實變得可以被機械抓到：把記錄裡的 ``decision_path`` 餵回 :func:`resolve`，
    如果推導出的 ``rule_id`` 與記錄的不同，代表當時跑的檢定不是規則集規定的
    那一個——不論是手動改了、還是規則集版本對不上。

    回傳違規清單；空 list 代表這筆記錄可回溯。**不丟例外**，因為稽核要能一次
    掃完整份 ledger 並列出所有問題，而不是停在第一筆壞的上面。
    """
    problems: list[str] = []
    block = record.get("stat_decision")
    if not isinstance(block, Mapping):
        return [f"{record.get('trial_id', '?')}：沒有 stat_decision 區塊，無法回溯。"]

    if block.get("no_statistical_claim") is True:
        # L0 機械分診這類記錄不做任何統計宣稱，硬要附一份檢定處方反而是把
        # 「用程式比對了兩份檔案」包裝成統計判決。這裡只確認它確實沒有偷偷
        # 佔用 N，以及有寫下它憑什麼這樣分診。
        problems = []
        tid = record.get("trial_id", "?")
        if block.get("delta_n", 0) != 0:
            problems.append(
                f"{tid}：宣告 no_statistical_claim 卻計入 N（delta_n="
                f"{block.get('delta_n')}）——不做統計宣稱就不該佔用母體。"
            )
        if not block.get("basis"):
            problems.append(f"{tid}：宣告 no_statistical_claim 但沒寫 basis，無法回溯憑據。")
        return problems

    path = block.get("decision_path")
    if not isinstance(path, Mapping):
        return [f"{record.get('trial_id', '?')}：stat_decision 缺 decision_path。"]

    version = block.get("ruleset_version", DEFAULT_RULESET_VERSION)
    try:
        facts = Facts(**path)
        recomputed = resolve(facts, version=version)
    except DecisionError as e:
        return [f"{record.get('trial_id', '?')}：decision_path 重算失敗——{e}"]

    tid = record.get("trial_id", "?")
    for fieldname in ("rule_id", "primary_test", "se_correction", "threshold",
                      "delta_n", "may_be_selected"):
        recorded = block.get(fieldname)
        derived = getattr(recomputed, fieldname)
        if recorded is not None and recorded != derived:
            problems.append(
                f"{tid}：{fieldname} 記載為 {recorded!r}，但由 decision_path 重算應為 "
                f"{derived!r}（規則集 {version}）。"
            )
    if block.get("rule_id") is None:
        problems.append(f"{tid}：stat_decision 缺 rule_id，無法確認用的是哪條規則。")
    return problems


def latest_by_trial_id(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """同 ``trial_id`` 只留最後一筆（ledger 允許追加同 id 的更新行）。

    讀端統一用這個口徑，stat_tree 與 harness 也是取最後一筆。逐行加總 ``delta_n``
    的讀端，會在追加一行「完成行」時讓 N 加倍。沒有 ``trial_id`` 的記錄無法去重，
    逐筆保留；順序以各 id 第一次出現的位置為準。
    """
    order: list[Any] = []
    latest: dict[str, Mapping[str, Any]] = {}
    for i, rec in enumerate(records):
        tid = rec.get("trial_id")
        if not tid:
            order.append(i)
            continue
        if tid not in latest:
            order.append(tid)
        latest[tid] = rec
    return [records[k] if isinstance(k, int) else latest[k] for k in order]


def audit_ledger(records: Sequence[Mapping[str, Any]]) -> dict:
    """整份 ledger 的稽核報告，另附 N 記帳的重算。

    N 是 DSR 的分母，也是本專案最容易被悄悄低估的數字。這裡不信任任何人記在
    報告裡的 N，而是從 ledger 重算 ``purpose == selection`` 的筆數——同 ``trial_id``
    只算最後一筆，並檢查同 id 的 ``delta_n`` 沒有往下掉：追加一行較小的
    ``delta_n`` 不會被 append-only hook 擋下，卻會讓 N 變小。
    """
    problems: list[str] = []
    seen_delta: dict[str, int] = {}
    for rec in records:
        problems.extend(audit_record(rec))
        tid = rec.get("trial_id")
        block = rec.get("stat_decision")
        if tid and isinstance(block, Mapping):
            d = int(block.get("delta_n", 0))
            if tid in seen_delta and d < seen_delta[tid]:
                problems.append(
                    f"{tid}：delta_n 從 {seen_delta[tid]} 降為 {d}——同一個 trial 的"
                    "母體計數只增不減，下降會讓 N 變小、DSR 變好看。"
                )
            seen_delta[tid] = max(d, seen_delta.get(tid, 0))
    n_selection = 0
    for rec in latest_by_trial_id(records):
        block = rec.get("stat_decision")
        if isinstance(block, Mapping):
            n_selection += int(block.get("delta_n", 0))
    return {
        "n_records": len(records),
        "n_selection_recomputed": n_selection,
        "problems": problems,
        "auditable": not problems,
    }


__all__ = [
    "Facts",
    "Prescription",
    "resolve",
    "load_ruleset",
    "audit_record",
    "audit_ledger",
    "DecisionError",
    "UnderdeterminedError",
    "RulesetError",
    "DEFAULT_RULESET_VERSION",
]

#!/usr/bin/env python3
"""L0 機械分診：把 strategies/_generated/ 的候選分成「真的是候選」與「從來不是」。

為什麼這一關要放在最前面
------------------------
harness 的搜尋前沿顯示 gs-strategy 有 349 個候選從未被嘗試。直覺的做法是
「把它們跑完」。但那個直覺在統計上是有代價的，而且代價可以算：

Deflated Sharpe 的門檻隨試驗數 N 以約 sqrt(2 ln N) 成長。把 349 個候選全部
當成 selection trial 跑，母體從 33 漲到 382，門檻上漲三成——**上漲的門檻套在
所有現有與未來的策略上**。也就是說，跑完這 349 個，代價是讓 repo 裡每一支
既有策略都變得更難通過。

而這 349 個候選裡，絕大多數根本不帶任何資訊。本腳本用兩個機械判準把它們
分出來，兩個判準都可以被第三者重跑：

1. **無假說骨架**：manifest 的 ``matched_keywords`` 是空的、模板是 fallback。
   產生器沒有從論文抽出任何訊號，只是掛了一個 buy_and_hold 骨架上去。規範
   §一 關卡零要求「假說先於資料」；一個沒有假說的骨架從來沒有進入過檢定家族，
   所以把它排除是免費的，也不需要任何統計依據。

2. **與既有候選同組態**：strategy.py 的正規化 AST（剝掉 docstring）加上完整
   組態指紋（params、bundle、symbols、期間、資金）都相同。同一份程式碼配同一組
   參數跑兩次，第二次不會帶來任何新資訊，卻會讓 N 加一。

判準刻意都不看績效。看績效再決定要不要計入 N，就是選擇性回報。

排除不等於證偽
--------------
輸出的 disposition 一律是 ``excluded:``，不是 ``rejected``。harness 的
search-history 樹刻意把這兩者用不同顏色與符號分開——「這從來不是一個候選」
與「這被測過而且輸了」是完全不同的兩件事，混在一起會讓前者看起來像一個
研究結論。本腳本產出的記錄同樣標 ``no_statistical_claim: true``：它做的是
檔案比對，不是統計判決。

用法
----
    python scripts/triage_generated.py                # dry-run，只印報告
    python scripts/triage_generated.py --apply        # 寫入 log/trials.jsonl
    python scripts/triage_generated.py --json out.json
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
GENERATED = REPO / "strategies" / "_generated"
LEDGER = REPO / "log" / "trials.jsonl"
RULESET_VERSION = "1.1"

#: 產生器在抽不出訊號時掛上的 fallback 模板。這個名單是判準的一部分，
#: 所以寫死在這裡而不是靠猜——新增 fallback 模板時要一併更新。
FALLBACK_TEMPLATES = {"buy_and_hold"}

#: 進入組態指紋的 manifest 欄位。只放「會改變回測結果」的欄位；
#: name / description / tags 這類只影響閱讀的欄位刻意排除，否則同一個回測
#: 會因為標題不同而被當成兩個不同的候選——那正是這 349 個的成因。
CONFIG_KEYS = ("params", "bundle", "symbols", "asset_class",
               "start", "end", "capital_base")


@dataclass
class Candidate:
    name: str
    manifest_path: Path
    manifest: dict
    code_fp: str
    config_fp: str
    template: str
    matched_keywords: list[str]
    paper_title: str
    paper_url: str
    disposition: str = ""
    why: str = ""
    duplicate_of: str | None = None
    parse_error: str | None = None
    resurrect_when: str = ""
    rel_base: Path = REPO

    @property
    def config_ref(self) -> str:
        """harness 的搜尋前沿是靠 config_ref 字串比對來判定「已嘗試」的。

        所以它必須是 **repo 相對路徑**，一個位元組都不能差——絕對路徑或
        機器相關的路徑會讓分診跑完之後前沿還是滿的，看起來像什麼都沒發生。
        rel_base 由 scan() 決定，讓測試能對 repo 外的暫存目錄跑同一套邏輯。
        """
        return str(self.manifest_path.relative_to(self.rel_base)).replace("\\", "/")

    @property
    def class_key(self) -> str:
        return f"{self.code_fp}:{self.config_fp}"


# ---------------------------------------------------------------------------
# 指紋
# ---------------------------------------------------------------------------

def code_fingerprint(path: Path) -> str:
    """strategy.py 的正規化 AST 指紋——剝掉 docstring 後的程式碼。

    剝 docstring 是本函式的全部重點。這 349 份 strategy.py 的位元組全部不同，
    因為每一份的 docstring 都嵌了自己那篇論文的標題；但剝掉之後只剩三份。
    直接對檔案取 sha256 會得到 349 個相異值，然後讓人以為有 349 個候選。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    dumped = ast.dump(ast.fix_missing_locations(tree))
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]


def config_fingerprint(manifest: dict) -> str:
    payload = {k: manifest.get(k) for k in CONFIG_KEYS}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 掃描與分診
# ---------------------------------------------------------------------------

def _mojibake_hint(text: str) -> str | None:
    """認出 UTF-8 被當成 latin-1 解碼再重新編碼所留下的痕跡。

    `–`（U+2013）的 UTF-8 位元組是 E2 80 93；誤用 latin-1 解碼會得到 `â\\x80\\x93`，
    再存成 UTF-8 就把 U+0080 這個控制字元寫進檔案，而 YAML 拒收控制字元。
    這裡只做**辨識**不做修復：就地把字串修好會讓檔案能解析，然後產生器繼續
    產出同樣壞掉的下一批。壞掉的來源要在爬蟲那邊修。
    """
    for marker in ("â\x80", "â\x82", "â\x88", "Ã©", "Ã¢"):
        if marker in text:
            return (
                "UTF-8 被誤以 latin-1 解碼後再編碼（mojibake）。"
                "修復處在爬蟲的解碼層，不在此處；本腳本刻意不就地修字串，"
                "否則產生器會繼續產出同樣壞掉的下一批。"
            )
    return None


def scan(generated: Path = GENERATED) -> list[Candidate]:
    # config_ref 要能被 harness 比對，所以路徑一律相對於 repo 根；掃描目錄若不在
    # repo 底下（測試會這樣做），就退回它的上一層，讓相對路徑仍然成立。
    rel_base = REPO if generated.is_relative_to(REPO) else generated.parent
    out: list[Candidate] = []
    for manifest_path in sorted(generated.glob("*/manifest.yaml")):
        strategy_py = manifest_path.parent / "strategy.py"
        if not strategy_py.is_file():
            continue
        raw = manifest_path.read_text(encoding="utf-8")
        try:
            manifest = yaml.safe_load(raw) or {}
        except yaml.YAMLError as e:
            # manifest 讀不了不是「沒有候選」，是資料品質失敗（規範 §一 關卡一）。
            # 當成 0 個候選悄悄跳過，會讓一個壞掉的爬蟲永遠不被發現。
            out.append(Candidate(
                name=manifest_path.parent.name, manifest_path=manifest_path,
                manifest={}, code_fp=code_fingerprint(strategy_py), config_fp="",
                template="?", matched_keywords=[], paper_title="", paper_url="",
                parse_error=(_mojibake_hint(raw)
                             or str(e).splitlines()[0]),
                rel_base=rel_base,
            ))
            continue
        source = manifest.get("source") or {}
        paper = (source.get("inputs") or {}).get("paper") or {}
        out.append(Candidate(
            name=manifest_path.parent.name,
            manifest_path=manifest_path,
            manifest=manifest,
            code_fp=code_fingerprint(strategy_py),
            config_fp=config_fingerprint(manifest),
            template=str(source.get("template") or "?"),
            matched_keywords=list(source.get("matched_keywords") or []),
            paper_title=str(paper.get("title") or manifest.get("name") or ""),
            paper_url=str(paper.get("url") or ""),
            rel_base=rel_base,
        ))
    return out


def triage(candidates: list[Candidate]) -> list[Candidate]:
    """就地標上 disposition。判準順序固定，先無假說、後同組態。

    順序有意義：一個既沒有假說、又與別人同組態的候選，理由要記「無假說」，
    因為那才是它真正的問題。若先判同組態，303 個裡會有 302 個被記成
    「重複」，而掩蓋掉「產生器對這些論文根本沒抽出訊號」這個更重要的事實。

    排除的對象是**目前這個骨架**，不是那篇論文
    -------------------------------------------
    每個被排除的候選背後都是一篇不同的論文。它們之所以在此刻無法區分，是因為
    產生器對它們產出了同一份 buy_and_hold（或同一份 momentum 骨架）——manifest
    的 review_checklist 自己就寫著「Replace `_generate_signal()` with the paper's
    actual signal formula」，也就是**這 349 個沒有任何一個實作了它的論文**。

    所以每筆排除都附 ``resurrect_when``：把那篇論文的訊號真的寫進 strategy.py，
    它的組態指紋就會改變，屆時它是一個全新的、獨立的候選。不寫這一欄，這份
    分診就會被日後的人讀成「這 347 篇論文都沒價值」，那是一個本關沒有做過的宣稱。
    """
    seen_class: dict[str, str] = {}
    for cand in candidates:
        if cand.parse_error:
            cand.disposition = "excluded:manifest 無法解析"
            cand.why = (
                f"manifest.yaml 不是合法 YAML：{cand.parse_error} "
                f"在修好之前這個候選無法被回測，也無法判斷它是不是候選。"
            )
            cand.resurrect_when = "爬蟲解碼層修好並重新產生 manifest 之後重跑分診。"
            continue
        if not cand.matched_keywords and cand.template in FALLBACK_TEMPLATES:
            cand.disposition = "excluded:無假說骨架"
            cand.why = (
                f"產生器未從論文抽出任何訊號（matched_keywords 為空），"
                f"僅掛上 {cand.template} fallback 骨架。規範 §一 關卡零要求假說先於資料；"
                f"沒有假說者從未進入檢定家族，排除不計入 N。"
            )
            cand.resurrect_when = (
                "本關排除的是這個骨架，不是這篇論文。人讀過論文、寫下一句可證偽的"
                "假說並把訊號實作進 strategy.py 之後，它就是一個新的獨立候選。"
            )
            continue
        first = seen_class.get(cand.class_key)
        if first is not None:
            cand.disposition = "excluded:與既有候選同組態"
            cand.duplicate_of = first
            cand.why = (
                f"strategy.py 正規化 AST 與完整組態指紋均與 {first} 相同"
                f"（{cand.class_key}）——兩者都只是關鍵字命中後掛上的同一份骨架，"
                f"都沒有實作各自論文的訊號。重跑不產生新資訊，卻會讓 N 加一。"
            )
            cand.resurrect_when = (
                f"把本篇論文的訊號實作進 strategy.py；組態指紋一改變，它就不再與 "
                f"{first} 同組態，屆時是一個獨立候選。"
            )
            continue
        seen_class[cand.class_key] = cand.name
        cand.disposition = "候選"
        cand.why = (
            f"組態獨立且帶有訊號關鍵字 {cand.matched_keywords}；"
            f"進入 L1 假說萃取，尚未做任何統計宣稱。"
        )
    return candidates


# ---------------------------------------------------------------------------
# 多重檢定成本
# ---------------------------------------------------------------------------

def expected_max_sharpe(n_trials: int) -> float:
    """真實 Sharpe = 0 時，N 次獨立試驗的最大 Sharpe 期望值。

    Bailey & López de Prado (2014) 的 deflation benchmark：
    ``(1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e))``，γ 是 Euler-Mascheroni 常數。
    這就是 DSR 用來扣的那條線；它隨 N 上升，所以多跑一個候選會讓**所有**
    策略的門檻一起變高。本函式存在的唯一目的，是讓那個代價變成一個數字。
    """
    if n_trials < 2:
        return 0.0
    gamma = 0.5772156649015329
    from statistics import NormalDist
    nd = NormalDist()
    return ((1 - gamma) * nd.inv_cdf(1 - 1 / n_trials)
            + gamma * nd.inv_cdf(1 - 1 / (n_trials * math.e)))


# ---------------------------------------------------------------------------
# trial 記錄
# ---------------------------------------------------------------------------

def build_records(candidates: list[Candidate], now: str) -> list[dict]:
    """產出 research-trial/v1 記錄。

    樹的形狀刻意反映分診結論：根是這次分診本身，第二層是每個等價類的代表，
    重複者掛在代表底下。這樣 harness 的搜尋歷史樹會直接把「349 其實是 N 個
    相異組態」畫出來，而不是排成 349 個平行節點讓人繼續以為有 349 個方向。
    """
    root_id = f"t-triage-generated-{now[:10]}"
    records: list[dict] = [{
        "schema": "research-trial/v1",
        "trial_id": root_id,
        "parent_trial_id": None,
        "repo": "gs-strategy",
        "level": "direction",
        "hypothesis": "strategies/_generated 的候選中，有多少是真的相異研究方向",
        "acceptance_criterion": "以正規化 AST + 組態指紋做等價類分割，判準可重跑",
        "config_ref": None,
        "state": "complete",
        "started_at": now,
        "finished_at": now,
        "disposition": "informational",
        "outcome_manual": _headline(candidates),
        "source_doc": "docs/spec/auto-research-funnel.md",
        "stat_decision": {
            "ruleset_version": RULESET_VERSION,
            "purpose": "screening",
            "delta_n": 0,
            "no_statistical_claim": True,
            "basis": "mechanical_dedup: normalized-AST + config fingerprint",
        },
    }]

    reps = {c.name for c in candidates if c.disposition == "候選"}
    for cand in candidates:
        parent = root_id
        if cand.duplicate_of and cand.duplicate_of in reps:
            parent = f"t-gen-{cand.duplicate_of}"
        records.append({
            "schema": "research-trial/v1",
            "trial_id": f"t-gen-{cand.name}",
            "parent_trial_id": parent,
            "repo": "gs-strategy",
            "level": "direction",
            "hypothesis": cand.paper_title[:200],
            "acceptance_criterion": "L0 機械分診：是否為一個相異且帶假說的候選",
            "config_ref": cand.config_ref,
            "config_sha256": f"{cand.code_fp}:{cand.config_fp}",
            "state": "complete",
            "started_at": now,
            "finished_at": now,
            "disposition": cand.disposition,
            "why_abandoned": cand.why if cand.disposition.startswith("excluded") else None,
            "resurrect_when": cand.resurrect_when or None,
            "outcome_manual": (
                f"template={cand.template} keywords={cand.matched_keywords or '—'}"
            ),
            "source_doc": cand.paper_url or "docs/spec/auto-research-funnel.md",
            "stat_decision": {
                "ruleset_version": RULESET_VERSION,
                "purpose": "screening",
                "delta_n": 0,
                "no_statistical_claim": True,
                "basis": "mechanical_dedup: normalized-AST + config fingerprint",
            },
        })
    return records


def _headline(candidates: list[Candidate]) -> str:
    counts = Counter(c.disposition for c in candidates)
    classes = len({c.class_key for c in candidates})
    return (f"{len(candidates)} 個候選｜相異組態 {classes} 個｜"
            + "｜".join(f"{k} {v}" for k, v in counts.most_common()))


# ---------------------------------------------------------------------------
# 報告
# ---------------------------------------------------------------------------

def report(candidates: list[Candidate], n_existing: int) -> str:
    lines: list[str] = []
    classes: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates:
        classes[c.class_key].append(c)

    broken = [c for c in candidates if c.parse_error]
    lines.append(f"掃描 {len(candidates)} 個候選（{GENERATED}）")
    lines.append(f"相異等價類：{len(classes) - (1 if broken else 0)}"
                 f"（正規化 AST + 完整組態指紋；不含無法解析者）")
    lines.append("")
    for key, members in sorted(classes.items(), key=lambda kv: -len(kv[1])):
        head = members[0]
        if head.parse_error:
            continue
        lines.append(f"  [{key}] x{len(members):<4d} template={head.template:<15s}"
                     f" keywords={head.matched_keywords or '—'}")
        lines.append(f"        代表：{head.name}")
    lines.append("")

    if broken:
        lines.append(f"⚠ 資料品質（規範 §一 關卡一）：{len(broken)} 份 manifest 不是合法 YAML")
        for c in broken[:5]:
            lines.append(f"    {c.name}")
        if len(broken) > 5:
            lines.append(f"    …另 {len(broken) - 5} 份")
        srcs = Counter(c.name.split("_")[0] for c in broken)
        lines.append(f"    來源分佈：{dict(srcs)}")
        lines.append(f"    {broken[0].parse_error}")
        lines.append("")

    counts = Counter(c.disposition for c in candidates)
    lines.append("分診結果：")
    for disp, n in counts.most_common():
        lines.append(f"  {disp:<28s} {n:>4d}")
    lines.append("")

    n_new = counts.get("候選", 0)
    naive_n = n_existing + len(candidates)
    disciplined_n = n_existing + n_new
    b0 = expected_max_sharpe(n_existing)
    b_naive = expected_max_sharpe(naive_n)
    b_disc = expected_max_sharpe(disciplined_n)
    lines.append("多重檢定成本（DSR deflation benchmark，真實 Sharpe = 0 時的 E[max SR]）：")
    lines.append(f"  現況        N={n_existing:<4d}  門檻 {b0:.3f}")
    lines.append(f"  全部跑完    N={naive_n:<4d}  門檻 {b_naive:.3f}"
                 f"   （+{(b_naive / b0 - 1) * 100:.1f}%，套用在所有既有與未來策略上）")
    lines.append(f"  分診後      N={disciplined_n:<4d}  門檻 {b_disc:.3f}"
                 f"   （+{(b_disc / b0 - 1) * 100:.1f}%）")
    lines.append("")
    lines.append("注意：排除不是證偽。disposition 一律標 excluded:，記錄標")
    lines.append("no_statistical_claim——本關做的是檔案比對，不是統計判決。每筆排除")
    lines.append("都附 resurrect_when：排除的是這個骨架，不是那篇論文。這 349 個沒有")
    lines.append("任何一個實作了自己論文的訊號（manifest 的 review_checklist 自己就")
    lines.append("這樣寫），所以真正的下一步是讀論文寫假說，不是把骨架跑完。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def count_existing_selection_trials(ledger: Path = LEDGER) -> int:
    """ledger 上已計入母體的 selection trial 數。

    讀不到就回 0 並在報告裡誠實顯示——不猜一個看起來合理的 N。猜出來的 N
    會讓 DSR 那一欄變得體面，而那正是這整套東西要防的事。
    """
    if not ledger.is_file():
        return 0
    from strategies._common.validation.decision import latest_by_trial_id
    records = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    # 同 trial_id 取最後一筆，與 stat_tree／audit_ledger 同口徑。
    return sum(int((r.get("stat_decision") or {}).get("delta_n", 0))
               for r in latest_by_trial_id(records))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="把 trial 記錄附加到 log/trials.jsonl（預設只印報告）")
    ap.add_argument("--json", type=Path, help="把分診明細另存成 JSON")
    ap.add_argument("--baseline-n", type=int, default=None,
                    help="覆寫既有母體大小（預設讀 ledger）")
    args = ap.parse_args(argv)

    if not GENERATED.is_dir():
        print(f"找不到 {GENERATED}", file=sys.stderr)
        return 2

    candidates = triage(scan())
    n_existing = (args.baseline_n if args.baseline_n is not None
                  else count_existing_selection_trials())
    print(report(candidates, n_existing))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    records = build_records(candidates, now)

    if args.json:
        args.json.write_text(json.dumps(
            [{"name": c.name, "disposition": c.disposition, "why": c.why,
              "template": c.template, "keywords": c.matched_keywords,
              "class": c.class_key, "duplicate_of": c.duplicate_of,
              "resurrect_when": c.resurrect_when,
              "config_ref": c.config_ref, "paper": c.paper_title}
             for c in candidates], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明細已寫入 {args.json}")

    if args.apply:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"\n已附加 {len(records)} 筆記錄到 {LEDGER}")
        print("ledger 是 append-only：既有行不得修改，重跑會產生重複的 trial_id，"
              "由 harness 取同 id 的最後一筆。")
    else:
        print(f"\n[dry-run] 將產生 {len(records)} 筆 trial 記錄。"
              f"確認無誤後加 --apply 寫入 {LEDGER}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

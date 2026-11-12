# quant_crawler/ideas/triage.py
"""idea 卡的機械分診。判準可被第三者重跑，且刻意不看任何績效數字。

對應 Gslang v0.1 做不到的事（見 genesis-deck scripts/formal/gslang_compiler.py 的
_UNSUPPORTED_SIGNAL／_UNSUPPORTED_STMT）：多標的／截面、停損停利、加減碼、盤中、歷史引用。
這些不是「寫不好」，是語言表達不了，所以是 defer（待擴充），不是 drop（不是候選）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from quant_crawler.ideas.card import IdeaCard, set_status

# (pattern, 原因)。pattern 對 hypothesis+mechanism+market+horizon 的小寫文字比對。
UNSUPPORTED = [
    (r"cross[- ]?section|\brank(ing)?\b|截面|排名|相對強弱|long[/-]short portfolio|portfolio|投組|多標的|\bpairs?\b|配對|價差",
     "多標的／截面／配對：Gslang v0.1 只支援單一標的"),
    (r"stop[- ]?loss|take[- ]?profit|trailing[- ]stop|停損|停利|移動停損|atr stop",
     "停損／停利／移動停損：Gslang v0.1 出場只能由轉換條件決定"),
    (r"pyramid|scale[- ]?in|scale[- ]?out|加碼|減碼|分批",
     "加碼／減碼／分批：Gslang v0.1 只能直接調整到目標部位"),
    (r"intraday|\btick\b|minute|盤中|分鐘|limit order|限價|order book|委託簿",
     "盤中／限價：Gslang v0.1 只支援日頻、收盤決策、下一根開盤成交"),
    (r"crossover|golden cross|death cross|交叉|cross(es|ed|ing)?\s+(above|below|over|under)|slope|斜率|momentum change|前一根",
     "交叉／斜率／歷史引用：Gslang v0.1 沒有歷史引用語法"),
    (r"grid search|walk[- ]?forward optim|參數優化|網格",
     "參數優化：Gslang v0.1 不做優化，參數由人給定"),
]
RESURRECT = "Gslang 擴充該能力後重新分診（見 docs/gslang-strategy-formation-gap-eval）"

_FUTURES = re.compile(r"futures?|期貨|\btx\b|\bmtx\b|台指|小台|index future", re.I)
_STOCK = re.compile(r"single[- ]stock|individual stock|stocks?\b|equit(y|ies)|個股|股票", re.I)


@dataclass(frozen=True)
class TriageResult:
    status: str            # triaged | defer | drop
    reason: str
    resurrect_when: str = ""


def normalize_signature(s: str) -> str:
    words = re.findall(r"[a-z0-9一-鿿]+", s.lower())
    return " ".join(sorted(set(words)))


def _text(card: IdeaCard) -> str:
    return " ".join([card.hypothesis, card.mechanism, card.market, card.horizon]).lower()


def triage(card: IdeaCard, known_signatures: set[str]) -> TriageResult:
    if not card.hypothesis.strip():
        return TriageResult("drop", "無假說：抽取不出可驗證的假說（規範 §一 關卡零要求假說先於資料）")
    sig = normalize_signature(card.signature)
    if sig and sig in known_signatures:
        return TriageResult("drop", "與既有候選簽章重複：同一組態再跑一次只會讓試驗數 N 加一")
    text = _text(card)
    for pat, why in UNSUPPORTED:
        if re.search(pat, text):
            return TriageResult("defer", why, RESURRECT)
    m = card.market
    if not _FUTURES.search(m) or (_STOCK.search(m) and not _FUTURES.search(m)):
        return TriageResult("defer", "市場不是單一期貨標的：Gslang／回測管線只接單一期貨 FSM", RESURRECT)
    if _STOCK.search(m) and re.search(r"stocks|個股|equities", m, re.I):
        return TriageResult("defer", "個股／多標的：Gslang v0.1 只支援單一標的", RESURRECT)
    return TriageResult("triaged", "通過機械分診：有假說、非重複、可能表達為單一期貨 FSM")


def apply_triage(card: IdeaCard, known_signatures: set[str]) -> TriageResult:
    r = triage(card, known_signatures)
    set_status(card, r.status, r.reason)
    card.resurrect_when = r.resurrect_when
    card.fsm_expressible = True if r.status == "triaged" else (False if r.status == "defer" else None)
    return r

# quant_crawler/ideas/extract.py
"""LLM 抽取 idea 卡。LLM 說「這是逐字引用」一律用程式驗證（learnings: llm-verbatim-quote-needs-machine-check）。"""
from __future__ import annotations

import json
import os
import re
from typing import Callable

import requests

from quant_crawler.ideas.card import IdeaCard, slug
from quant_crawler.storage.models import PaperRecord

LLM = Callable[[str, str], str]

FIELDS = ("hypothesis", "mechanism")
_DEGRADE_ERRORS = (TypeError, AttributeError, ValueError, KeyError)
_CJK = re.compile(r"[一-鿿]")
_DECODER = json.JSONDecoder()

SYSTEM = (
    "你是量化研究助理。只依使用者提供的標題與摘要抽取，不得引入摘要沒有的內容。"
    "輸出**單一 JSON 物件**，鍵：hypothesis（一句話、方向先押定）、mechanism（為何訊號應存在）、"
    "market、horizon、data_needs（陣列）、flaws（陣列）、signature（3–8 個英文關鍵詞，空白分隔，"
    "描述指標＋規則＋標的）、quotes（陣列，每項 {field: 'hypothesis'|'mechanism', text: 摘要中的"
    "**逐字**片段}）。找不到依據的欄位留空字串，不要編造。"
)


def build_prompt(rec: PaperRecord) -> tuple[str, str]:
    user = f"標題：{rec.title}\n\n摘要：{rec.abstract}\n\n來源：{rec.url}"
    return SYSTEM, user


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _long_enough(text: str) -> bool:
    """中文引用以非空白字元 ≥ 6 計；其餘以空白切分的單字 ≥ 3 計。"""
    if _CJK.search(text):
        return len(re.sub(r"\s+", "", text)) >= 6
    return len(text.split()) >= 3


def verify_quotes(quotes: list[dict], source_text: str) -> list[dict]:
    """只保留：為 dict、field 為 hypothesis/mechanism、長度足夠、且逐字出現在 source_text 的引用。
    型別異常的項目一律略過，不丟例外。"""
    if not isinstance(quotes, list) or not isinstance(source_text, str):
        return []
    hay = _norm(source_text)
    out = []
    for q in quotes:
        if not isinstance(q, dict):
            continue
        field, text = q.get("field"), q.get("text")
        if not isinstance(field, str) or field not in FIELDS:
            continue
        if not isinstance(text, str) or not _long_enough(text.strip()):
            continue
        if _norm(text) in hay:
            out.append({"field": field, "text": text})
    return out


def _parse_json(text: str) -> dict | None:
    """從每個 '{' 起嘗試 raw_decode，回傳第一個成功且為 dict 的物件。"""
    if not isinstance(text, str):
        return None
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = _DECODER.raw_decode(text, m.start())
        except ValueError:  # json.JSONDecodeError 為其子類
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _str(v) -> str:
    return v if isinstance(v, str) else ""


def _strlist(v) -> list[str]:
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, str)]


def card_id_for(rec: PaperRecord) -> str:
    return slug(f"{rec.source}_{rec.source_id}")


def _base_card(rec: PaperRecord) -> IdeaCard:
    return IdeaCard(id=card_id_for(rec), source=f"{rec.source}:{rec.source_id}", title=rec.title)


def extract_card(rec: PaperRecord, llm: LLM) -> IdeaCard:
    card = _base_card(rec)
    try:
        system, user = build_prompt(rec)
        d = _parse_json(llm(system, user))
        if d is None:
            card.reason = "抽取失敗：LLM 回應不是 JSON"
            return card
        raw = d.get("quotes", [])
        # 標題與摘要分別比對，引用不得跨越兩者邊界；結果取聯集並去重。
        quotes: list[dict] = []
        for src in (rec.title, rec.abstract):
            for q in verify_quotes(raw, src):
                if q not in quotes:
                    quotes.append(q)
        have = {q["field"] for q in quotes}
        card.quotes = quotes
        card.hypothesis = _str(d.get("hypothesis")) if "hypothesis" in have else ""
        card.mechanism = _str(d.get("mechanism")) if "mechanism" in have else ""
        card.market = _str(d.get("market"))
        card.horizon = _str(d.get("horizon"))
        card.data_needs = _strlist(d.get("data_needs"))
        card.flaws = _strlist(d.get("flaws"))
        card.signature = _str(d.get("signature"))
        return card
    except _DEGRADE_ERRORS as e:
        failed = _base_card(rec)
        failed.reason = f"抽取失敗：{type(e).__name__}"
        return failed


class OpenAICompatLLM:
    """打 OpenAI 相容的 /chat/completions。金鑰只讀環境變數，不寫進 log。

    環境變數：GDECK_LLM_URL（含 /v1 的 base）、GDECK_LLM_KEY、GDECK_LLM_MODEL。
    """

    def __init__(self, url: str | None = None, key: str | None = None, model: str | None = None,
                 timeout: float = 120.0) -> None:
        self.url = (url or os.environ["GDECK_LLM_URL"]).rstrip("/")
        self._key = key or os.environ["GDECK_LLM_KEY"]
        self.model = model or os.environ["GDECK_LLM_MODEL"]
        self.timeout = timeout

    def __call__(self, system: str, user: str) -> str:
        r = requests.post(
            f"{self.url}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"},
            json={"model": self.model, "temperature": 0,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

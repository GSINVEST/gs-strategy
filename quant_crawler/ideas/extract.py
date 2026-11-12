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


def verify_quotes(quotes: list[dict], source_text: str) -> list[dict]:
    hay = _norm(source_text)
    out = []
    for q in quotes or []:
        t = _norm(str(q.get("text", "")))
        if t and t in hay:
            out.append({"field": q.get("field", ""), "text": q["text"]})
    return out


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def extract_card(rec: PaperRecord, llm: LLM) -> IdeaCard:
    card = IdeaCard(id=slug(f"{rec.source}_{rec.source_id}"),
                    source=f"{rec.source}:{rec.source_id}", title=rec.title)
    system, user = build_prompt(rec)
    d = _parse_json(llm(system, user))
    if d is None:
        card.reason = "抽取失敗：LLM 回應不是 JSON"
        return card
    quotes = verify_quotes(d.get("quotes", []), f"{rec.title} {rec.abstract}")
    have = {q["field"] for q in quotes}
    card.quotes = quotes
    card.hypothesis = str(d.get("hypothesis", "")) if "hypothesis" in have else ""
    card.mechanism = str(d.get("mechanism", "")) if "mechanism" in have else ""
    card.market = str(d.get("market", ""))
    card.horizon = str(d.get("horizon", ""))
    card.data_needs = [str(x) for x in d.get("data_needs", []) or []]
    card.flaws = [str(x) for x in d.get("flaws", []) or []]
    card.signature = str(d.get("signature", ""))
    return card


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

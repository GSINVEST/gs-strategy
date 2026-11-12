# tests/test_idea_extract.py
import json

from quant_crawler.ideas.extract import build_prompt, extract_card, verify_quotes
from quant_crawler.storage.models import PaperRecord

REC = PaperRecord(
    source="arxiv", source_id="2605.04004", title="Trend following in TX futures",
    abstract="We show that buying when the close exceeds the 200-day average earns excess "
             "returns in index futures because of slow information diffusion.",
    url="https://arxiv.org/abs/2605.04004",
)


def _resp(**over):
    d = {
        "hypothesis": "收盤價高於 200 日均線時做多 TX",
        "mechanism": "資訊擴散緩慢",
        "market": "TX futures", "horizon": "daily",
        "data_needs": ["TX 日線"], "flaws": ["樣本短"],
        "signature": "sma200 trend tx",
        "quotes": [
            {"field": "hypothesis", "text": "buying when the close exceeds the 200-day average"},
            {"field": "mechanism", "text": "slow information diffusion"},
        ],
    }
    d.update(over)
    return json.dumps(d, ensure_ascii=False)


def test_prompt_contains_source_text_and_no_key():
    system, user = build_prompt(REC)
    assert "200-day average" in user and "JSON" in system


def test_extract_ok():
    c = extract_card(REC, lambda s, u: _resp())
    assert c.id == "arxiv_2605_04004"
    assert c.hypothesis and c.mechanism
    assert len(c.quotes) == 2


def test_fabricated_quote_clears_field():
    bad = _resp(quotes=[
        {"field": "hypothesis", "text": "this sentence is not in the abstract"},
        {"field": "mechanism", "text": "slow information diffusion"},
    ])
    c = extract_card(REC, lambda s, u: bad)
    assert c.hypothesis == ""            # 沒有可驗證依據 → 清空
    assert c.mechanism != ""
    assert all(q["field"] == "mechanism" for q in c.quotes)


def test_verify_quotes_whitespace_tolerant():
    q = [{"field": "hypothesis", "text": "buying  when the\nclose exceeds"}]
    assert verify_quotes(q, REC.abstract) == q


def test_json_in_code_fence_and_garbage():
    fenced = "```json\n" + _resp() + "\n```"
    assert extract_card(REC, lambda s, u: fenced).hypothesis
    c = extract_card(REC, lambda s, u: "sorry, cannot")
    assert c.hypothesis == "" and "抽取失敗" in c.reason

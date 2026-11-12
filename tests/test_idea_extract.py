# tests/test_idea_extract.py
import json

import pytest
import requests

from quant_crawler.ideas import extract as ex
from quant_crawler.ideas.extract import (
    OpenAICompatLLM,
    _parse_json,
    build_prompt,
    extract_card,
    verify_quotes,
)
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


# ---- 以下為 fix round 1 新增 ----

def test_quotes_not_list_yields_no_quotes():
    for bad in ("abc", {}, 5, None):
        c = extract_card(REC, lambda s, u, b=bad: _resp(quotes=b))
        assert c.hypothesis == "" and c.mechanism == "" and c.quotes == []


def test_non_dict_items_and_non_str_field_are_skipped():
    qs = [
        "slow information diffusion",
        {"field": ["hypothesis"], "text": "buying when the close exceeds"},
        {"field": 7, "text": "buying when the close exceeds"},
        {"field": "mechanism", "text": "slow information diffusion"},
    ]
    c = extract_card(REC, lambda s, u: _resp(quotes=qs))
    assert c.hypothesis == ""
    assert c.mechanism != ""
    assert [q["field"] for q in c.quotes] == ["mechanism"]


def test_null_hypothesis_with_valid_quote():
    c = extract_card(REC, lambda s, u: _resp(hypothesis=None))
    assert c.hypothesis == ""            # 不得變成字串 "None"
    assert c.mechanism != ""
    assert "None" not in c.hypothesis


def test_data_needs_and_flaws_non_list_become_empty():
    c = extract_card(REC, lambda s, u: _resp(data_needs="TX", flaws={"a": 1}))
    assert c.data_needs == [] and c.flaws == []


def test_non_str_field_values_become_empty():
    c = extract_card(REC, lambda s, u: _resp(market=123, horizon=None, signature=["x"]))
    assert c.market == "" and c.horizon == "" and c.signature == ""


def test_two_json_objects_first_valid_dict_wins():
    text = ('{"hypothesis": "A", "mechanism": "B", "quotes": []} '
            '{"hypothesis": "Z", "quotes": []}')
    d = _parse_json(text)
    assert d["hypothesis"] == "A"


def test_braces_in_prose_do_not_break_parsing():
    text = "note {see below} then " + _resp()
    c = extract_card(REC, lambda s, u: text)
    assert c.hypothesis != "" and c.mechanism != ""


def test_short_quote_rejected():
    # "the" 出現在摘要中，但只有 1 個單字 → 不採信
    assert verify_quotes([{"field": "hypothesis", "text": "the"}], REC.abstract) == []
    c = extract_card(REC, lambda s, u: _resp(
        quotes=[{"field": "hypothesis", "text": "the"}]))
    assert c.hypothesis == ""


def test_non_hypothesis_mechanism_field_dropped():
    q = [{"field": "market", "text": "buying when the close exceeds"}]
    assert verify_quotes(q, REC.abstract) == []


def test_quote_spanning_title_abstract_boundary_rejected():
    q = [{"field": "hypothesis", "text": "TX futures We show that"}]
    assert verify_quotes(q, REC.title) == []
    assert verify_quotes(q, REC.abstract) == []
    c = extract_card(REC, lambda s, u: _resp(quotes=q, mechanism=""))
    assert c.hypothesis == ""


def test_chinese_quote_length_rule():
    src = "收盤價高於均線做多"
    assert verify_quotes([{"field": "hypothesis", "text": "資訊"}], src) == []
    ok = [{"field": "hypothesis", "text": "收盤價高於均線"}]
    assert verify_quotes(ok, src) == ok


def test_unexpected_type_error_degrades_to_failure_card(monkeypatch):
    def boom(quotes, text):
        raise TypeError("boom")
    monkeypatch.setattr(ex, "verify_quotes", boom)
    c = extract_card(REC, lambda s, u: _resp())
    assert c.hypothesis == "" and "抽取失敗" in c.reason


class _FakeResp:
    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc

    def raise_for_status(self):
        if self._exc is not None:
            raise self._exc

    def json(self):
        return self._payload


def test_openai_compat_success_path(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json, timeout=timeout)
        return _FakeResp({"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    llm = OpenAICompatLLM(url="http://gw.test/v1/", key="SECRET-KEY", model="m1")
    assert llm("sys", "usr") == "hi"
    assert seen["url"] == "http://gw.test/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer SECRET-KEY"
    assert seen["body"]["model"] == "m1" and seen["body"]["temperature"] == 0


def test_openai_compat_error_does_not_leak_key(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResp(exc=requests.HTTPError("503 Server Error"))

    monkeypatch.setattr(requests, "post", fake_post)
    llm = OpenAICompatLLM(url="http://gw.test/v1", key="SECRET-KEY", model="m1")
    with pytest.raises(requests.HTTPError) as info:
        llm("sys", "usr")
    assert "SECRET-KEY" not in str(info.value)

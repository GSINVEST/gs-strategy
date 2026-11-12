# tests/test_idea_gslang_bridge.py
import json
import os
from pathlib import Path

import pytest

from quant_crawler.ideas.card import IdeaCard, set_status
from quant_crawler.ideas import gslang_bridge as gb

FORMAL = os.environ.get("GDECK_FORMAL", "/home/kevin/gdeck-formal")
needs_formal = pytest.mark.skipif(
    not (Path(FORMAL) / "scripts/formal/gslang_compiler.py").exists(),
    reason="GDECK_FORMAL 快照不存在")

SMA_GSL = """\
策略 sma_trend
標的 TX 期貨 日頻 乘數=200 最小跳動=TODO(人)
時間 決策=收盤 成交=下一根開盤價 滑價=每筆半個價差
參數 window = 200
參數 position_contracts = 1
參數 allow_short = 否
參數 days_before_close = 10
參數 capital_base = 5000000
指標 close_price = CLOSE@1()
指標 sma_200 = SMA@1(視窗=window)
訊號 trend_state = 比較(close_price, sma_200)
狀態 空手 : 部位 == 0
狀態 做多 : 部位 > 0
狀態 做空 : 部位 < 0
轉換 進多  從 空手 當 trend_state 為 HIGH  到 做多  目標部位=position_contracts
轉換 出多  從 做多 當 trend_state 為 LOW   到 空手  目標部位=0
換月 做多  距到期 days_before_close 日 平倉並於新近月重開
成本 手續費 = 200 每口每筆
成本 滑價   = 6 點 價差
不變量 部位絕對值 <= position_contracts            來源=程式推導
壓力 跳空 點數=TODO(人)
範圍外 參數優化 統計檢定 訊號預測力 委託簿 保證金追繳
"""


def _card(**kw):
    base = dict(id="sma_trend", source="s", title="t", hypothesis="h", market="TX futures")
    base.update(kw)
    c = IdeaCard(**base)
    set_status(c, "triaged")
    return c


def test_unavailable_when_no_root(monkeypatch, tmp_path):
    monkeypatch.delenv("GDECK_FORMAL", raising=False)
    with pytest.raises(gb.FormalUnavailable):
        gb.load_formal(str(tmp_path / "nope"))


@needs_formal
def test_compile_ok():
    r = gb.compile_gsl(SMA_GSL, "sma_trend", FORMAL)
    assert r.ok and r.ir["id"] == "sma_trend" and not r.unsupported
    assert r.todo_count >= 1          # 最小跳動、跳空壓力仍是 TODO(人)


@needs_formal
def test_compile_unsupported_is_classified():
    bad = SMA_GSL + "\n轉換 停損出場 從 做多 當 trend_state 為 LOW 到 空手 目標部位=0 停損\n"
    bad = bad.replace("指標 sma_200", "停損 stop_loss = 2\n指標 sma_200")
    r = gb.compile_gsl(bad, "x", FORMAL)
    assert not r.ok and r.unsupported and "不支援" in r.message


@needs_formal
def test_syntax_error_is_not_unsupported():
    r = gb.compile_gsl("這不是 gslang", "x", FORMAL)
    assert not r.ok and not r.unsupported and r.message


@needs_formal
def test_advance_writes_files_and_status(tmp_path):
    c = _card()
    r = gb.advance_card_to_fsm(c, SMA_GSL, tmp_path, FORMAL)
    assert r.ok and c.status == "fsm"
    assert (tmp_path / "sma_trend.gsl").is_file()
    assert json.loads((tmp_path / "sma_trend.ir.json").read_text(encoding="utf-8"))["id"] == "sma_trend"


@needs_formal
def test_advance_unsupported_defers(tmp_path):
    c = _card()
    bad = SMA_GSL.replace("指標 sma_200", "停損 stop_loss = 2\n指標 sma_200")
    gb.advance_card_to_fsm(c, bad, tmp_path, FORMAL)
    assert c.status == "defer"


@needs_formal
def test_gen_python_refuses_todo_constants():
    r = gb.compile_gsl(SMA_GSL.replace("參數 window = 200", "參數 window = TODO(人)"), "x", FORMAL)
    if r.ok:                            # 編譯可通過（TODO 降階成 IR todo 物件），codegen 必須拒絕
        with pytest.raises(gb.CodegenFailed):
            gb.gen_python(r.ir, FORMAL)


@needs_formal
def test_draft_uses_gateway_prompt():
    seen = {}

    def llm(system, user):
        seen["s"], seen["u"] = system, user
        return "```gsl\n" + SMA_GSL + "\n```"

    out = gb.draft_gsl(_card(hypothesis="站上均線做多"), llm, FORMAL)
    assert out.lstrip().startswith("策略") and "站上均線做多" in seen["u"] and "Gslang" in seen["s"]

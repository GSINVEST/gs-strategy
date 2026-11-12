# tests/test_idea_admit.py
import hashlib
from pathlib import Path

import pytest
import yaml

from quant_crawler.ideas.admit import gate_admission, update_index, write_bundle
from quant_crawler.ideas.card import IdeaCard, set_status

GSL_SRC = Path(__file__).resolve().parents[1] / "strategies" / "_common" / "gslang"

IR = {"id": "demo_x", "title_zh": "示範", "instruments": [{"id": "TX"}],
      "constants": [{"name": "window", "value": 200}, {"name": "position_contracts", "value": 1}],
      "costs": {"commission": {"per_instrument": {"TX": 200}}, "slippage": {"spread_points": 6.0}}}


def _card(status_path=("triaged", "fsm", "impl", "bt_registered", "bt_done")):
    c = IdeaCard(id="demo_x", source="arxiv:1", title="T", hypothesis="站上均線做多", market="TX futures")
    for s in status_path:
        set_status(c, s)
    return c


def test_bundle_files_and_vendored_gslang(tmp_path):
    d = write_bundle(_card(), IR, "def initialize(context):\n    pass\n", tmp_path, GSL_SRC)
    assert {p.name for p in d.iterdir()} >= {"strategy.py", "manifest.yaml", "config.yaml", "README.md", "gslang"}
    m = yaml.safe_load((d / "manifest.yaml").read_text(encoding="utf-8"))
    assert m["id"] == "demo_x" and m["asset_class"] == "future" and m["bundle"] == "tquant_future"
    assert m["symbols"] == ["TX"] and "idea-funnel" in m["tags"] and m["spec_version"] == 1
    src = (GSL_SRC / "indicators.py").read_bytes()
    assert hashlib.sha256((d / "gslang" / "indicators.py").read_bytes()).digest() == hashlib.sha256(src).digest()


def test_index_append_only(tmp_path):
    update_index(tmp_path, {"id": "b", "kind": "idea-funnel"})
    update_index(tmp_path, {"id": "a", "kind": "idea-funnel"})
    idx = yaml.safe_load((tmp_path / "INDEX.yaml").read_text(encoding="utf-8"))
    assert idx["schema"] == "strategy-index/1" and [e["id"] for e in idx["entries"]] == ["a", "b"]
    with pytest.raises(ValueError):
        update_index(tmp_path, {"id": "a", "kind": "idea-funnel"})


def test_gate_blocks_until_all_clear(tmp_path):
    c = _card()
    res = tmp_path / "bt.pkl"
    assert gate_admission(c, True, True, None)                   # 缺回測結果
    res.write_bytes(b"x")
    assert gate_admission(c, False, True, res)                   # 預登記未簽核
    assert gate_admission(c, True, False, res)                   # bundle 驗證不過
    early = _card(("triaged", "fsm", "impl"))
    assert gate_admission(early, True, True, res)                # 還沒到 bt_done
    assert gate_admission(c, True, True, res) == []

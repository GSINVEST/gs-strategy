"""research/ 草稿的基本守門：來源 sha256 相符、仍標為不可抽 IR、不出現已驗證用語。"""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRAFTS = sorted((ROOT / "research").glob("*/draft.json"))
FORBIDDEN = ["已證明", "證明正確", "保證安全", "已驗證策略", "通過驗證"]


def test_drafts_present():
    assert {p.parent.name for p in DRAFTS} == {"fin_otc_spread_tx", "pledge_wall_00631l"}


@pytest.mark.parametrize("path", DRAFTS, ids=lambda p: p.parent.name)
def test_draft_is_honest(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["draft_version"] == "strategy-research-draft/0.1"
    for s in d["provenance"]["sources"]:
        raw = (ROOT / s["path"]).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(raw).hexdigest() == s["sha256_lf"], "來源檔被改過，草稿可能過期"
    assert d["ir_status"]["extractable"] is False
    ids = {g["id"] for g in d["gaps"]}
    assert set(d["ir_status"]["blocked_by"]) <= ids
    text = json.dumps(d, ensure_ascii=False)
    assert not [w for w in FORBIDDEN if w in text]


@pytest.mark.parametrize("path", DRAFTS, ids=lambda p: p.parent.name)
def test_not_a_strategy_bundle(path):
    assert not (path.parent / "strategy.py").exists()

# tests/test_idea_admit.py
import hashlib
from unittest import mock
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
    c.verdict = "pass_with_bounds"
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


def test_gate_fail_closed_types(tmp_path):
    c = _card()
    res = tmp_path / "bt.pkl"
    res.write_bytes(b"x")
    for bad in ("False", "True", {"a": 1}, mock.Mock(), 1, None):
        assert gate_admission(c, bad, True, res), bad
        assert gate_admission(c, True, bad, res), bad
    for v in ("", "counterexample", "inconclusive", "pass_with_bounds_x"):
        c2 = _card()
        c2.verdict = v
        assert gate_admission(c2, True, True, res), v
    empty = tmp_path / "empty.pkl"
    empty.write_bytes(b"")
    assert gate_admission(c, True, True, empty)
    assert gate_admission(c, True, True, tmp_path)  # 目錄不算檔案
    assert gate_admission(c, True, True, tmp_path / "nope")


def test_bundle_cleanup_and_retry(tmp_path):
    bad = tmp_path / "gsl"
    bad.mkdir()
    (bad / "__init__.py").write_text("x")
    (bad / "broken.py").symlink_to(tmp_path / "missing")   # 複製中途失敗
    root = tmp_path / "s"
    root.mkdir()
    with pytest.raises(Exception):
        write_bundle(_card(), IR, "x", root, bad)
    assert list(root.iterdir()) == []
    write_bundle(_card(), IR, "x", root, GSL_SRC)
    assert (root / "demo_x" / "strategy.py").exists()


def test_bundle_existing_dir_untouched(tmp_path):
    d = tmp_path / "demo_x"
    d.mkdir()
    (d / "keep.txt").write_text("k")
    with pytest.raises(FileExistsError):
        write_bundle(_card(), IR, "x", tmp_path, GSL_SRC)
    assert [p.name for p in d.iterdir()] == ["keep.txt"]
    assert [p.name for p in tmp_path.iterdir()] == ["demo_x"]


@pytest.mark.parametrize("bad", ["../x", "/abs", "_common", "", "a/b", "a\\b", "..", "-x", "A"])
def test_bad_card_id_rejected_before_write(tmp_path, bad):
    c = _card()
    c.id = bad  # 繞過 dataclass 驗證
    with pytest.raises(ValueError):
        write_bundle(c, IR, "x", tmp_path, GSL_SRC)
    assert list(tmp_path.iterdir()) == []


def test_bundle_contents_and_vendor_all(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for n in ("__init__.py", "indicators.py", "extra.py"):
        (src / n).write_text(f"# {n}\n")
    (src / "notes.txt").write_text("no")
    root = tmp_path / "s"
    root.mkdir()
    d = write_bundle(_card(), IR, "PY_BODY\n", root, src)
    assert (d / "strategy.py").read_text(encoding="utf-8") == "PY_BODY\n"
    assert (d / "gslang" / "__init__.py").read_text() == "# __init__.py\n"
    assert (d / "gslang" / "extra.py").exists() and not (d / "gslang" / "notes.txt").exists()
    so = (d / "gslang" / "SOURCE.txt").read_text(encoding="utf-8")
    assert str(src) in so and "do not edit" in so
    assert "不代表策略有效" in (d / "manifest.yaml").read_text(encoding="utf-8")
    assert "TLC PASS" in (d / "README.md").read_text(encoding="utf-8")


def test_index_robustness(tmp_path):
    p = tmp_path / "INDEX.yaml"
    p.write_text("schema: strategy-index/1\nextra: 7\nentries:\n- {id: z, note: keep}\n", encoding="utf-8")
    update_index(tmp_path, {"id": "a"})
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert doc["extra"] == 7 and doc["entries"][1] == {"id": "z", "note": "keep"}
    p.write_text("schema: strategy-index/1\nentries:\n", encoding="utf-8")
    update_index(tmp_path, {"id": "a"})
    assert [e["id"] for e in yaml.safe_load(p.read_text(encoding="utf-8"))["entries"]] == ["a"]
    with pytest.raises(ValueError):
        update_index(tmp_path, {"kind": "x"})
    p.write_text("schema: strategy-index/1\nentries:\n- {kind: x}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        update_index(tmp_path, {"id": "b"})
    p.write_text("schema: other/9\nentries: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        update_index(tmp_path, {"id": "b"})
    assert not list(tmp_path.glob("*.tmp"))

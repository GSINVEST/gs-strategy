# tests/test_idea_final_fixes.py
"""最後一次小修：bundle 自含 futures_setup、batch_id 撞名、長度前綴雜湊、symlink、validation.n_trials。"""
import ast
import hashlib
import os
import sys

import pytest
import yaml

from quant_crawler.ideas import admit, cli
from quant_crawler.ideas.batch import register_batch
from quant_crawler.ideas.card import REPO, IdeaCard, save_card, set_status

GSL_SRC = REPO / "strategies" / "_common" / "gslang"
NOW = "2026-11-12T00:00:00Z"


def _impl(cid):
    c = IdeaCard(id=cid, source="s", title="t", hypothesis="站上均線做多", market="TX futures")
    for s in ("triaged", "fsm", "impl"):
        set_status(c, s)
    c.verdict = "pass_with_bounds"
    return c


IR = {"id": "x", "instruments": [{"id": "TX"}]}


# ---- C-a：bundle 自含 futures_setup.py；乾淨 sys.path（只有 bundle 目錄）下所有非標準庫、非第三方的 import 都解析得到 ----
def test_staged_bundle_resolves_imports_from_bundle_dir_only(tmp_path):
    strat = ("from futures_setup import apply_taiwan_futures_costs, make_roll_futures_handler\n"
             "from gslang.indicators import sma\n"
             "import zipline\n\n\ndef initialize(context):\n    pass\n\n\ndef handle_data(context, data):\n    pass\n")
    d, sha = admit.stage_bundle(_impl("imp_x"), IR, strat, tmp_path / "bundle", GSL_SRC,
                                "2020-01-01", "2026-04-30")
    allowed_third_party = {"zipline", "pandas", "numpy"}
    tree = ast.parse((d / "strategy.py").read_text(encoding="utf-8"))
    tops = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            tops |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            tops.add(n.module.split(".")[0])
    missing = [t for t in sorted(tops)
               if t not in sys.stdlib_module_names and t not in allowed_third_party
               and not ((d / f"{t}.py").is_file() or (d / t / "__init__.py").is_file())]
    assert missing == [], f"bundle 目錄解析不到：{missing}"
    src = (d / "gslang" / "SOURCE.txt").read_text(encoding="utf-8")
    assert "futures_setup.py" in src and "strategies/_common/futures_setup.py" in src
    (d / "futures_setup.py").write_text("# changed\n", encoding="utf-8")
    assert admit.bundle_sha256(d) != sha                         # futures_setup.py 算進雜湊


# ---- I-b：batch_id 與卡片 trial_id 撞名 ----
def test_batch_id_colliding_with_card_trial_id_rejected(tmp_path, monkeypatch, capsys):
    for bad in ("idea-bar", "idea-baz", "idea-a_b"):
        with pytest.raises(ValueError, match="撞名"):
            register_batch([_impl("bar")], tmp_path / "b", NOW, tmp_path / "l.jsonl", tmp_path / "p", batch_id=bad)
    assert not (tmp_path / "l.jsonl").exists()
    # 預設批次名與含連字號者不受影響
    register_batch([_impl("bar")], tmp_path / "b", NOW, tmp_path / "l.jsonl", tmp_path / "p")
    register_batch([_impl("baz")], tmp_path / "b", NOW, tmp_path / "l.jsonl", tmp_path / "p",
                   batch_id="idea-second-batch")
    cards_dir = tmp_path / "ideas"
    save_card(_impl("zed"), cards_dir)
    monkeypatch.setattr(cli, "CARDS", cards_dir)
    monkeypatch.setattr(cli, "LEDGER", tmp_path / "log2" / "trials.jsonl")
    assert cli.main(["batch", "--register", "--batch-id", "idea-zed", "--start", "2020-01-01",
                     "--end", "2026-04-30"]) == 2
    assert "撞名" in capsys.readouterr().err


# ---- m1：長度前綴雜湊 ----
def test_bundle_sha256_is_length_prefixed(tmp_path):
    d = tmp_path / "d"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_bytes(b"xy")
    (d / "sub" / "b.txt").write_bytes(b"")
    h = hashlib.sha256()
    for rel, content in (("a.txt", b"xy"), ("sub/b.txt", b"")):
        rb = rel.encode()
        h.update(len(rb).to_bytes(8, "big") + rb + len(content).to_bytes(8, "big") + content)
    assert admit.bundle_sha256(d) == h.hexdigest()
    # 以 NUL 分隔的舊方案下，這兩份內容會得到相同的位元組串流；現在雜湊必須不同
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    (a / "f").write_bytes(b"1" + bytes(1) + b"g" + bytes(1) + b"2")
    (b / "f").write_bytes(b"1")
    (b / "g").write_bytes(b"2")
    assert admit.bundle_sha256(a) != admit.bundle_sha256(b)


# ---- m2：symlink 一律拒絕 ----
@pytest.mark.skipif(os.name == "nt", reason="symlink")
def test_symlinks_rejected_in_staging_and_copy(tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "real.py").write_text("x", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    os.symlink(tmp_path / "outside.txt", staged / "link.txt")
    with pytest.raises(ValueError, match="symlink"):
        admit.reject_symlinks(staged)
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    with pytest.raises(ValueError, match="symlink"):
        admit.copy_staged_bundle(staged, strategies, "sym_x", "0" * 64)
    assert list(strategies.iterdir()) == []                     # 不留暫存目錄或半成品
    (staged / "link.txt").unlink()
    (tmp_path / "d").mkdir()
    os.symlink(tmp_path / "d", staged / "linkdir")
    with pytest.raises(ValueError, match="symlink"):
        admit.reject_symlinks(staged)


# ---- m5：staging config 的 validation.n_trials（runner 的 resolve_n_trials 讀這個鍵）----
def test_stage_bundle_writes_validation_n_trials(tmp_path):
    from strategies._common.validation.report import resolve_n_trials
    d, _ = admit.stage_bundle(_impl("nt_x"), IR, "x", tmp_path / "b", GSL_SRC, "2020-01-01", "2026-04-30",
                              n_trials=7)
    cfg = yaml.safe_load((d / "config.yaml").read_text(encoding="utf-8"))
    assert resolve_n_trials(cfg) == (7, "config")
    d2, _ = admit.stage_bundle(_impl("nt_x"), IR, "x", tmp_path / "b2", GSL_SRC, "2020-01-01", "2026-04-30")
    assert "validation" not in yaml.safe_load((d2 / "config.yaml").read_text(encoding="utf-8"))

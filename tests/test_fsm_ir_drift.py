"""FSM IR 漂移偵測：真實 repo 無漂移 + 負向測試（改檔、刪檔、CRLF 等價、壞 IR）。"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import check_fsm_ir_drift as drift  # noqa: E402

IDS = ["vgrsi_tx", "cubic_momentum_tx", "tsmom_tx_mtx"]


def test_committed_irs_have_no_drift_against_this_repo():
    problems, checked = drift.check(REPO)
    assert problems == []
    assert [Path(c).parent.name for c in checked] == sorted(IDS)


def _fake_root(tmp_path, crlf=False):
    sources = []
    for rel in ("strategies/x/strategy.py", "strategies/x/config.yaml"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        body = f"# {rel}\nline2\n"
        p.write_bytes((body.replace("\n", "\r\n") if crlf else body).encode())
        sources.append({"repo": "gs-strategy", "path": rel, "sha256_lf": hashlib.sha256(body.encode()).hexdigest()})
    (tmp_path / "strategies/x/fsm.ir.json").write_text(json.dumps({"provenance": {"sources": sources}}), encoding="utf-8")
    return tmp_path


def test_no_drift_and_crlf_is_equivalent(tmp_path):
    assert drift.check(_fake_root(tmp_path, crlf=True))[0] == []


def test_edited_source_is_reported(tmp_path):
    root = _fake_root(tmp_path)
    (root / "strategies/x/strategy.py").write_text("# 改過\n", encoding="utf-8")
    problems, _ = drift.check(root)
    assert len(problems) == 1 and "漂移" in problems[0] and "strategy.py" in problems[0]


def test_missing_source_is_reported(tmp_path):
    root = _fake_root(tmp_path)
    (root / "strategies/x/config.yaml").unlink()
    problems, _ = drift.check(root)
    assert len(problems) == 1 and "找不到來源檔" in problems[0]


def test_unreadable_ir_is_reported_and_exit_code_2(tmp_path):
    root = _fake_root(tmp_path)
    (root / "strategies/x/fsm.ir.json").write_text("{not json", encoding="utf-8")
    problems, checked = drift.check(root)
    assert checked == [] and "讀不懂" in problems[0]
    sys.argv = ["x", "--root", str(root)]
    assert drift.main() == 2


def test_cli_exit_codes(tmp_path):
    root = _fake_root(tmp_path)
    sys.argv = ["x", "--root", str(root)]
    assert drift.main() == 0
    (root / "strategies/x/strategy.py").write_text("# 改過\n", encoding="utf-8")
    assert drift.main() == 1


@pytest.mark.parametrize("sid", IDS)
def test_ir_files_declare_gs_strategy_sources_that_exist(sid):
    ir = json.loads((REPO / "strategies" / sid / "fsm.ir.json").read_text(encoding="utf-8"))
    assert ir["id"] == sid
    paths = {s["path"] for s in ir["provenance"]["sources"]}
    assert f"strategies/{sid}/strategy.py" in paths and f"strategies/{sid}/config.yaml" in paths

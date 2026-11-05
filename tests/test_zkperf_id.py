"""manifest 選填欄位 zkperf_id：validator 驗格式、webui 只顯示「已登記」。

注意：zkperf_registered 只代表 manifest 宣告了 id，不是 proof 狀態；
驗證在 genesis-deck gateway，gs-strategy 不驗證。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from quant_crawler.webui import stats

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "validate_dashboard_bundle", REPO / "scripts" / "validate_dashboard_bundle.py")
vdb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vdb)


def _bundle(tmp_path: Path, **extra) -> Path:
    b = tmp_path / "demo_bundle"
    b.mkdir()
    m = {"id": "demo_bundle", "name": "Demo", "description": "d",
         "asset_class": "future", "bundle": "tquant_future",
         "start": "2020-01-01", "end": "2024-12-31", "capital_base": 1000000,
         "params": {}, "spec_version": 1}
    m.update(extra)
    (b / "manifest.yaml").write_text(yaml.safe_dump(m), encoding="utf-8")
    (b / "strategy.py").write_text(
        "def initialize(c):\n    pass\ndef handle_data(c, d):\n    pass\n",
        encoding="utf-8")
    return b


def _zk_errors(b: Path) -> list[str]:
    return [e for e in vdb.validate(b) if "zkperf_id" in e]


def test_absent_is_ok(tmp_path):
    assert _zk_errors(_bundle(tmp_path)) == []


@pytest.mark.parametrize("val", ["abc", "a1_b-2", "0" * 64, "a" + "b" * 63])
def test_valid_ids_accepted(tmp_path, val):
    assert _zk_errors(_bundle(tmp_path, zkperf_id=val)) == []


@pytest.mark.parametrize("val", ["ab", "A" * 5, "-abc", "_abc", "a" * 65,
                                 "ab c", "", 123, ["abc"], True])
def test_invalid_ids_give_clear_error_not_crash(tmp_path, val):
    errs = _zk_errors(_bundle(tmp_path, zkperf_id=val))
    assert len(errs) == 1
    assert "^[a-z0-9][a-z0-9_-]{2,63}$" in errs[0]


def test_null_treated_as_absent(tmp_path):
    assert _zk_errors(_bundle(tmp_path, zkperf_id=None)) == []


def _record(tmp_path, **extra):
    b = _bundle(tmp_path, **extra)
    return stats._bundle_record(b, "manual", tmp_path / "export")


def test_record_without_id(tmp_path):
    r = _record(tmp_path)
    assert r["zkperf_id"] is None and r["zkperf_registered"] is False


def test_record_with_id(tmp_path):
    r = _record(tmp_path, zkperf_id="my-strat_01")
    assert r["zkperf_id"] == "my-strat_01" and r["zkperf_registered"] is True


def test_record_invalid_id_not_registered(tmp_path):
    r = _record(tmp_path, zkperf_id="BAD ID")
    assert r["zkperf_id"] is None and r["zkperf_registered"] is False

"""Tests for strategies/_common/validation/report.py — synthetic returns only."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

report = importlib.import_module("strategies._common.validation.report")


def _perf_frame(seed=7, n=504, drift=0.0004):
    rng = np.random.RandomState(seed)
    rets = rng.normal(drift, 0.01, n)
    return pd.DataFrame({"returns": rets})


def test_build_report_fields_and_sanity():
    rep = report.build_report(_perf_frame(), n_trials=1)
    for key in ("schema", "n_days", "total_return", "cagr",
                "annualized_sharpe", "max_drawdown", "psr", "dsr", "n_trials"):
        assert key in rep, key
    assert rep["n_days"] == 504
    assert -1.0 < rep["max_drawdown"] <= 0.0
    assert 0.0 <= rep["psr"] <= 1.0


def test_dsr_deflates_with_more_trials():
    perf = _perf_frame()
    dsr_1 = report.build_report(perf, n_trials=1)["dsr"]
    dsr_100 = report.build_report(perf, n_trials=100)["dsr"]
    assert dsr_100 < dsr_1            # more trials -> harsher deflation


def test_missing_column_and_short_series_raise():
    with pytest.raises(ValueError):
        report.build_report(pd.DataFrame({"close": [1, 2, 3]}))
    with pytest.raises(ValueError):
        report.build_report(pd.DataFrame({"returns": [0.01] * 5}))


def test_write_sidecar_for_success_and_silence(tmp_path):
    p = tmp_path / "perf.parquet"
    _perf_frame().to_parquet(p, index=False)

    sidecar = report.write_sidecar_for(p, n_trials=3)
    assert sidecar is not None and sidecar.exists()
    doc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert doc["schema"] == report.SCHEMA
    assert doc["n_trials"] == 3
    assert sidecar.name == "perf.parquet.validation.json"

    garbage = tmp_path / "bad.parquet"
    garbage.write_text("not parquet", encoding="utf-8")
    assert report.write_sidecar_for(garbage) is None   # never raises


def test_cli_parquet_roundtrip(tmp_path):
    perf = _perf_frame()
    p = tmp_path / "perf.parquet"
    perf.to_parquet(p, index=False)
    out = tmp_path / "report.json"
    rc = report.main([str(p), "--n-trials", "10", "-o", str(out)])
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == report.SCHEMA
    assert doc["n_trials"] == 10


"""Appended coverage for the frequency / n_trials provenance and manifest
write-back added on dev/validation-deflate-fixes."""

# --------------------------------------------------------------------------
# 修正 1 — periods_per_year derivation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("freq,expected", [
    ("daily", 252),
    ("weekly", 52),
    ("monthly", 12),
    ("minute", 252),      # perf frames stay daily even for minute bars
    ("MONTHLY", 12),      # case/whitespace tolerant
    ("  weekly ", 52),
])
def test_periods_per_year_for_known_frequencies(freq, expected):
    assert report.periods_per_year_for(freq) == expected


@pytest.mark.parametrize("freq", [None, "", "fortnightly", 12, ["daily"]])
def test_periods_per_year_for_unknown_returns_none(freq):
    assert report.periods_per_year_for(freq) is None


def _stamped(freq, periods):
    """A perf frame whose rows really are spaced at *freq*."""
    idx = pd.date_range("2020-01-06", periods=periods, freq=freq)
    rng = np.random.default_rng(11)
    return pd.DataFrame({"dt": idx,
                         "returns": rng.normal(0.001, 0.02, periods)})


@pytest.mark.parametrize("freq,periods,expected", [
    ("B", 260, 252),      # business-daily rows
    ("W-FRI", 120, 52),   # weekly rows
    ("ME", 60, 12),       # month-end rows
])
def test_periods_per_year_measured_from_timestamps(freq, periods, expected):
    ppy, source = report.infer_periods_per_year(_stamped(freq, periods))
    assert ppy == expected
    assert source.startswith("inferred:")


def test_inference_beats_a_misleading_rebalance_declaration():
    """A monthly-rebalanced strategy still emits daily perf rows.

    Regression guard: deriving periods_per_year from the manifest's
    ``rebalance`` key annualised 252 daily rows as if they were 21 years,
    turning a 17.66% one-year CAGR into 0.78%.
    """
    perf = _stamped("B", 252)
    ppy, source = report.resolve_periods_per_year({"rebalance": "monthly"}, perf)
    assert ppy == 252
    assert source == "inferred:daily"


def test_explicit_periods_per_year_overrides_inference():
    perf = _stamped("B", 252)
    assert report.resolve_periods_per_year(
        {"validation": {"periods_per_year": 26}}, perf
    ) == (26, "validation.periods_per_year")


def test_inference_declines_without_timestamps():
    bare = pd.DataFrame({"returns": [0.01] * 40})
    assert report.infer_periods_per_year(bare) == (None, None)


@pytest.mark.parametrize("cfg", [
    None, {}, {"rebalance": "monthly"}, {"validation": {}},
    {"validation": {"periods_per_year": "abc"}},
])
def test_resolve_periods_per_year_falls_back_to_252_but_says_so(cfg):
    ppy, source = report.resolve_periods_per_year(cfg)
    assert ppy == 252
    assert source == "default"          # the fallback is never silent


def test_monthly_annualisation_differs_from_daily():
    perf = _perf_frame()
    daily = report.build_report(perf, periods_per_year=252,
                                periods_per_year_source="inferred:monthly")
    monthly = report.build_report(perf, periods_per_year=12,
                                  periods_per_year_source="inferred:monthly")
    assert monthly["periods_per_year"] == 12
    # SR scales with sqrt(periods_per_year): annualising a monthly strategy
    # with 252 would overstate it by sqrt(252/12) ~= 4.58x
    assert monthly["annualized_sharpe"] == pytest.approx(
        daily["annualized_sharpe"] * (12 / 252) ** 0.5, rel=1e-9)
    assert abs(monthly["annualized_sharpe"]) < abs(
        daily["annualized_sharpe"])
    assert monthly["periods_per_year_source"] == "inferred:monthly"
    # a derived frequency leaves no "assumed 252" warning behind
    assert not any("252" in w for w in monthly["warnings"])


def test_default_frequency_leaves_a_trace_in_the_report():
    rep = report.build_report(_perf_frame(), n_trials=5,
                              n_trials_source="config")
    assert rep["periods_per_year"] == 252
    assert rep["periods_per_year_source"] == "default"
    assert any("252" in w for w in rep["warnings"])


# --------------------------------------------------------------------------
# 修正 2 — n_trials provenance and dsr_deflated
# --------------------------------------------------------------------------

def test_resolve_n_trials_priority_config_beats_env():
    cfg = {"validation": {"n_trials": 64}}
    assert report.resolve_n_trials(cfg, {"GS_VALIDATION_N_TRIALS": "8"}) == (
        64, "config")


def test_resolve_n_trials_env_when_config_silent():
    assert report.resolve_n_trials({}, {"GS_VALIDATION_N_TRIALS": "8"}) == (
        8, "env")
    assert report.resolve_n_trials(None, {"GS_VALIDATION_N_TRIALS": "8"}) == (
        8, "env")


def test_resolve_n_trials_default_when_nothing_declared():
    assert report.resolve_n_trials({}, {}) == (1, "default")
    # junk values do not become a fake N
    assert report.resolve_n_trials({"validation": {"n_trials": "many"}},
                                   {"GS_VALIDATION_N_TRIALS": "lots"}) == (
        1, "default")
    assert report.resolve_n_trials({"validation": {"n_trials": 0}}, {}) == (
        1, "default")


def test_dsr_not_deflated_when_n_trials_is_an_assumption():
    rep = report.build_report(_perf_frame())     # default source, N=1
    assert rep["n_trials"] == 1
    assert rep["n_trials_source"] == "default"
    assert rep["dsr_deflated"] is False
    assert rep["warnings"], "an undeflated DSR must carry a warning"
    joined = " ".join(rep["warnings"])
    assert "DSR" in joined and "deflate" in joined
    # At n_trials=1 the deflation benchmark is 0, so DSR is just PSR: a real
    # statistic that still tracks the return series, but carrying no
    # multiple-testing correction — hence dsr_deflated=False above.
    assert rep["dsr"] == pytest.approx(rep["psr"])
    other = report.build_report(_perf_frame(seed=99, drift=-0.001))
    assert other["dsr"] == pytest.approx(other["psr"])
    assert other["dsr"] != pytest.approx(rep["dsr"])   # not a constant


def test_dsr_deflated_true_when_n_trials_declared():
    for source in ("config", "env", "cli", "explicit"):
        rep = report.build_report(_perf_frame(), n_trials=30,
                                  n_trials_source=source)
        assert rep["dsr_deflated"] is True
        assert rep["n_trials_source"] == source
        assert not any("DSR" in w for w in rep["warnings"])


def test_declared_single_trial_is_still_not_deflated():
    """Declaring N=1 must not buy a "deflated" label for an undeflated number.

    Regression guard: dsr_deflated used to be derived from n_trials_source, so
    validation: {n_trials: 1} in config.yaml earned dsr_deflated=True on a
    Sharpe that had no multiple-testing correction applied at all.
    """
    rep = report.build_report(_perf_frame(), n_trials=1,
                              n_trials_source="config")
    assert rep["dsr_deflated"] is False
    assert any("DSR" in w for w in rep["warnings"])


def test_dsr_at_one_trial_equals_psr_not_one():
    """N=1 must collapse DSR onto PSR, not onto the constant 1.0.

    Regression guard for the sharpe.py extreme-value formula degenerating at
    n_trials == 1 (e=1 -> norm.ppf(0) = -inf -> PSR(-inf) = 1.0 for every
    series), which handed any backtest a perfect DSR.
    """
    rep = report.build_report(_perf_frame(), n_trials=1)
    assert rep["dsr"] == pytest.approx(rep["psr"])
    assert rep["dsr"] != pytest.approx(1.0)


def test_dsr_strictly_decreases_as_trials_grow():
    """More trials tried -> harder to believe the Sharpe."""
    perf = _perf_frame()
    vals = [report.build_report(perf, n_trials=n)["dsr"]
            for n in (1, 2, 10, 100)]
    assert vals == sorted(vals, reverse=True)
    assert vals[0] > vals[-1]


def test_write_sidecar_measures_factor_from_a_stamped_perf(tmp_path):
    """The runner writes perf frames that carry timestamps; use them."""
    p = tmp_path / "perf.parquet"
    _stamped("ME", 60).to_parquet(p, index=False)

    sidecar = report.write_sidecar_for(p, cfg={"validation": {"n_trials": 8}})
    doc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert doc["periods_per_year"] == 12
    assert doc["periods_per_year_source"] == "inferred:monthly"
    assert not any("年化係數退回預設" in w for w in doc["warnings"])


def test_cli_measures_frequency_instead_of_assuming_252(tmp_path, capsys):
    """`python -m ...report PERF.parquet` must measure, not assume.

    Regression guard: the CLI's fallback branch passed
    DEFAULT_PERIODS_PER_YEAR straight through, so build_report never got the
    None that triggers inference. Every CLI run reported 252 with source
    "default" plus a warning saying the timestamps were unreadable -- on
    frames that carried perfectly good timestamps.
    """
    p = tmp_path / "perf.parquet"
    _stamped("ME", 60).to_parquet(p, index=False)

    assert report.main([str(p)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["periods_per_year"] == 12
    assert doc["periods_per_year_source"] == "inferred:monthly"
    assert not any("年化係數退回預設" in w for w in doc["warnings"])


def test_cli_explicit_flags_still_win_over_inference(tmp_path, capsys):
    p = tmp_path / "perf.parquet"
    _stamped("ME", 60).to_parquet(p, index=False)

    assert report.main([str(p), "--periods-per-year", "26"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["periods_per_year"] == 26
    assert doc["periods_per_year_source"] == "cli"


def test_write_sidecar_records_sources(tmp_path, monkeypatch):
    monkeypatch.delenv(report.N_TRIALS_ENV_VAR, raising=False)
    p = tmp_path / "perf.parquet"
    _perf_frame().to_parquet(p, index=False)

    sidecar = report.write_sidecar_for(
        p, cfg={"validation": {"n_trials": 12}})
    doc = json.loads(sidecar.read_text(encoding="utf-8"))
    # _perf_frame() carries no timestamps, so the factor cannot be measured
    # and the report says so instead of pretending it derived one.
    assert doc["periods_per_year"] == 252
    assert doc["periods_per_year_source"] == "default"
    assert doc["n_trials"] == 12
    assert doc["n_trials_source"] == "config"
    assert doc["dsr_deflated"] is True

    bare = report.write_sidecar_for(p, cfg={})
    doc = json.loads(bare.read_text(encoding="utf-8"))
    assert doc["n_trials_source"] == "default"
    assert doc["dsr_deflated"] is False
    assert doc["periods_per_year_source"] == "default"


# --------------------------------------------------------------------------
# 修正 3 — apply_to_manifest
# --------------------------------------------------------------------------

MANIFEST_TEXT = """\
id: demo_tx
name: 示範策略
description: |
  中文說明寫回後必須保持可讀，不可被跳脫成 ASCII 逃逸序列。
asset_class: future
bundle: tquant_future
rebalance: monthly
params:
  root_symbol: TX
  window: 30
tags:
- futures
- 台股
spec_version: 1
"""


def _sample_report():
    return report.build_report(_perf_frame(), n_trials=48,
                               n_trials_source="config",
                               periods_per_year=12,
                               periods_per_year_source="inferred:monthly")


def test_apply_to_manifest_roundtrip_is_all_strings(tmp_path):
    yaml = pytest.importorskip("yaml")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(MANIFEST_TEXT, encoding="utf-8")

    rep = _sample_report()
    assert report.apply_to_manifest(manifest, rep) is True

    doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    block = doc["validation"]
    assert isinstance(block, dict)
    # the importer rejects anything that is not a string -> string mapping
    assert all(isinstance(k, str) and isinstance(v, str)
               for k, v in block.items()), block
    for key in ("psr", "dsr", "annualized_sharpe", "max_drawdown",
                "n_trials", "dsr_deflated"):
        assert key in block, key
    assert block["n_trials"] == "48"
    assert block["dsr_deflated"] == "true"
    # floats are formatted, not str(float)-dumped
    assert block["psr"] == f"{rep['psr']:.4f}"
    assert len(block["dsr"].split(".")[-1]) == 4


def test_apply_to_manifest_preserves_existing_keys_and_unicode(tmp_path):
    yaml = pytest.importorskip("yaml")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(MANIFEST_TEXT, encoding="utf-8")
    before = yaml.safe_load(manifest.read_text(encoding="utf-8"))

    assert report.apply_to_manifest(manifest, _sample_report()) is True
    raw = manifest.read_text(encoding="utf-8")
    after = yaml.safe_load(raw)

    for key, value in before.items():
        assert after[key] == value, key
    assert set(after) == set(before) | {"validation"}
    assert "示範策略" in raw and "\\u" not in raw     # allow_unicode=True


def test_apply_to_manifest_overwrites_only_the_validation_key(tmp_path):
    yaml = pytest.importorskip("yaml")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(MANIFEST_TEXT + "validation:\n  psr: '0.1000'\n"
                                        "  stale_key: gone\n",
                        encoding="utf-8")
    assert report.apply_to_manifest(manifest, _sample_report()) is True
    block = yaml.safe_load(manifest.read_text(encoding="utf-8"))["validation"]
    assert block["psr"] != "0.1000"
    assert "stale_key" not in block


def test_apply_to_manifest_returns_false_instead_of_raising(tmp_path):
    rep = _sample_report()
    # missing file
    assert report.apply_to_manifest(tmp_path / "nope.yaml", rep) is False
    # a directory, not a file
    assert report.apply_to_manifest(tmp_path, rep) is False
    # broken YAML
    broken = tmp_path / "broken.yaml"
    broken.write_text("id: demo\n  bad: [unclosed\n", encoding="utf-8")
    assert report.apply_to_manifest(broken, rep) is False
    # valid YAML that is not a mapping
    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("- just\n- a list\n", encoding="utf-8")
    assert report.apply_to_manifest(scalar, rep) is False


def test_manifest_nan_is_formatted_not_crashed():
    rep = dict(_sample_report(), cagr=float("nan"))
    block = report.manifest_validation_block(rep)
    assert block["cagr"] == "nan"
    assert all(isinstance(v, str) for v in block.values())


def test_cli_can_write_back_to_a_manifest(tmp_path):
    yaml = pytest.importorskip("yaml")
    perf_path = tmp_path / "perf.parquet"
    _perf_frame().to_parquet(perf_path, index=False)
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(MANIFEST_TEXT, encoding="utf-8")

    rc = report.main([str(perf_path), "--frequency", "monthly",
                      "--n-trials", "20", "--manifest", str(manifest)])
    assert rc == 0
    doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert doc["validation"]["n_trials"] == "20"
    assert doc["validation"]["periods_per_year"] == "12"
    assert doc["id"] == "demo_tx"

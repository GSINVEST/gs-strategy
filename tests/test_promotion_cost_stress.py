import copy

import numpy as np
import pandas as pd
import pytest
import yaml

from strategies._common.promotion import cost_stress

DEFAULTS = ({"TX": 200, "MTX": 100, "TE": 200}, 6.0)


def test_doubles_explicit_costs_and_spread():
    cfg = {"params": {"per_contract_cost": {"TX": 200, "MTX": 100}, "spread_points": 6.0, "lookback": 252}}
    out = cost_stress.scaled_config(cfg, 2.0, defaults=DEFAULTS)
    p = out["params"]
    assert p["per_contract_cost"]["TX"] == 400 and p["per_contract_cost"]["MTX"] == 200
    assert p["spread_points"] == 12.0
    assert p["lookback"] == 252                       # 其他參數不動


def test_missing_costs_expand_from_defaults_then_scale():
    # config 沒寫成本時 zipline 用預設值；乘二必須也乘到預設值，不能漏掉。
    out = cost_stress.scaled_config({"params": {}}, 2.0, defaults=DEFAULTS)
    p = out["params"]
    assert p["per_contract_cost"] == {"TX": 400, "MTX": 200, "TE": 400}
    assert p["spread_points"] == 12.0


def test_partial_cost_map_keeps_other_default_roots_scaled():
    cfg = {"params": {"per_contract_cost": {"MTX": 150}}}
    p = cost_stress.scaled_config(cfg, 2.0, defaults=DEFAULTS)["params"]
    assert p["per_contract_cost"] == {"TX": 400, "MTX": 300, "TE": 400}


def test_does_not_mutate_input_and_handles_missing_params():
    cfg = {"start": "2020-01-01"}
    before = copy.deepcopy(cfg)
    out = cost_stress.scaled_config(cfg, 2.0, defaults=DEFAULTS)
    assert cfg == before
    assert out["params"]["spread_points"] == 12.0 and out["start"] == "2020-01-01"


@pytest.mark.parametrize("factor", [0, -1, 0.5, float("nan")])
def test_factor_must_be_at_least_one(factor):
    # 「壓力」只准加重成本；<1 會讓結果變好看，拒絕。
    with pytest.raises(ValueError):
        cost_stress.scaled_config({"params": {}}, factor, defaults=DEFAULTS)


def test_factor_one_is_identity_on_costs():
    cfg = {"params": {"per_contract_cost": {"TX": 200}, "spread_points": 6.0}}
    p = cost_stress.scaled_config(cfg, 1.0, defaults=DEFAULTS)["params"]
    assert p["per_contract_cost"]["TX"] == 200 and p["spread_points"] == 6.0


def test_run_cost_stress_writes_scaled_config_and_returns(tmp_path, monkeypatch):
    sdir = tmp_path / "s"
    sdir.mkdir()
    (sdir / "strategy.py").write_text("# stub\n", encoding="utf-8")
    (sdir / "config.yaml").write_text(
        yaml.safe_dump({"start": "2020-01-01", "params": {"per_contract_cost": {"TX": 200}, "spread_points": 6.0}}),
        encoding="utf-8")
    seen = {}

    def fake_run(strategy_path, config_path, output_path=None, **kw):
        seen["cfg"] = yaml.safe_load(open(config_path, encoding="utf-8"))
        seen["strategy"] = str(strategy_path)
        pd.DataFrame({"returns": [0.01, -0.02, 0.03]}).to_pickle(output_path)

    monkeypatch.setattr(cost_stress, "_run_strategy", fake_run)
    monkeypatch.setattr(cost_stress, "_default_costs", lambda: DEFAULTS)
    out = tmp_path / "stress.parquet"
    res = cost_stress.run_cost_stress(sdir, out, factor=2.0)
    assert seen["cfg"]["params"]["per_contract_cost"]["TX"] == 400
    assert seen["cfg"]["params"]["spread_points"] == 12.0
    assert seen["strategy"].endswith("strategy.py")
    assert list(pd.read_parquet(out)["returns"]) == [0.01, -0.02, 0.03]
    assert res["factor"] == 2.0 and res["n_obs"] == 3
    assert (sdir / "config.yaml").read_text(encoding="utf-8").count("400") == 0   # 原檔不被改


def test_run_cost_stress_errors_without_returns(tmp_path, monkeypatch):
    sdir = tmp_path / "s"
    sdir.mkdir()
    (sdir / "strategy.py").write_text("#\n", encoding="utf-8")
    (sdir / "config.yaml").write_text("start: x\n", encoding="utf-8")
    monkeypatch.setattr(cost_stress, "_default_costs", lambda: DEFAULTS)
    monkeypatch.setattr(cost_stress, "_run_strategy",
                        lambda s, c, output_path=None, **k: pd.DataFrame({"x": [1]}).to_pickle(output_path))
    with pytest.raises(ValueError, match="returns"):
        cost_stress.run_cost_stress(sdir, tmp_path / "o.parquet")


def test_cli_stress_subcommand(tmp_path, monkeypatch, capsys):
    from scripts import promotion_gate
    called = {}
    monkeypatch.setattr(promotion_gate, "__name__", promotion_gate.__name__)
    import strategies._common.promotion.cost_stress as cs
    monkeypatch.setattr(cs, "run_cost_stress",
                        lambda d, o, f: called.update(d=str(d), o=str(o), f=f) or {"factor": f, "n_obs": 1})
    rc = promotion_gate.main(["stress", "--strategy-dir", str(tmp_path), "--out", str(tmp_path / "o.parquet")])
    assert rc == 0 and called["f"] == 2.0
    assert '"n_obs": 1' in capsys.readouterr().out


def test_cli_stress_bad_factor_is_input_error(tmp_path):
    from scripts import promotion_gate
    (tmp_path / "config.yaml").write_text("start: x\n", encoding="utf-8")
    rc = promotion_gate.main(["stress", "--strategy-dir", str(tmp_path),
                              "--out", str(tmp_path / "o.parquet"), "--factor", "0.5"])
    assert rc == 3

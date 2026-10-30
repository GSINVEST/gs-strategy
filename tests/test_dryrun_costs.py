"""D3 L1 成本模型（純函式，不需 bundle）。規格：§6.2、gs-strategy#53/#56。"""
import math

import pytest

from strategies._common.dryrun.costs import (
    SENSITIVITY_SPREADS, L1Params, decide_fill, scenario_params,
)

NAN = float("nan")


def _fill(params=None, **kw):
    args = dict(side=1, qty=1, ref_price=20000.0, bar_volume=50000.0)
    args.update(kw)
    return decide_fill(params or L1Params(), **args)


def test_buy_pays_half_spread_above_close():
    d = _fill()
    assert d.filled_qty == 1 and d.price == 20003.0   # 6 點完整價差，每邊 3 點
    assert d.reason is None and d.impact_points == 0.0


def test_sell_pays_half_spread_below_close():
    d = _fill(side=-1)
    assert d.price == 19997.0


def test_default_params_are_the_documented_ones():
    p = L1Params()
    assert p.spread_points == 6.0 and p.volume_limit == 0.05
    assert p.impact_enabled is False


def test_vvs_default_eta_is_refused():
    with pytest.raises(ValueError):
        L1Params(impact_enabled=True)                  # 不得沿用預設 eta
    with pytest.raises(ValueError):
        L1Params(impact_enabled=True, eta=0.049018)    # zipline DEFAULT_ETA 明確拒絕
    L1Params(impact_enabled=True, eta=42.0)


@pytest.mark.parametrize("ref", [NAN, 0.0, -1.0, None])
def test_bad_reference_price_is_rejected_with_reason(ref):
    d = _fill(ref_price=ref)
    assert d.filled_qty == 0 and d.price is None and d.reason == "NAN_PRICE"


@pytest.mark.parametrize("vol,reason", [(NAN, "NAN_VOLUME"), (None, "NAN_VOLUME"),
                                         (0.0, "ZERO_VOLUME"), (-5.0, "ZERO_VOLUME")])
def test_bad_volume_is_rejected_with_reason(vol, reason):
    d = _fill(bar_volume=vol)
    assert d.filled_qty == 0 and d.price is None and d.reason == reason


def test_volume_cap_partial_fill():
    d = _fill(qty=10, bar_volume=100.0)                # 5% * 100 = 5 口
    assert d.filled_qty == 5 and d.reason == "PARTIAL_VOLUME_CAP"
    assert d.price == 20003.0


def test_volume_cap_zero_rejects():
    d = _fill(qty=1, bar_volume=10.0)                  # floor(0.5) = 0
    assert d.filled_qty == 0 and d.reason == "VOLUME_CAP_ZERO"


def test_psi_ge_one_is_actually_capped():
    d = _fill(qty=300, bar_volume=100.0)
    assert d.filled_qty <= 5                           # xsmom 那種 ψ≥1 訂單不能全成交


def test_sigma_nan_is_irrelevant_when_impact_off():
    d = _fill(sigma=NAN, adv=NAN)
    assert d.price == 20003.0 and d.reason is None


def test_impact_formula_when_enabled():
    p = L1Params(impact_enabled=True, eta=2000.0)
    d = decide_fill(p, side=1, qty=1, ref_price=20000.0, bar_volume=50000.0,
                    adv=40000.0, sigma=0.16)
    bps = 2000.0 * 0.16 * math.sqrt(1 / 40000.0)
    assert d.impact_points == pytest.approx(20000.0 * bps / 1e4)
    assert d.price == pytest.approx(20003.0 + d.impact_points)


@pytest.mark.parametrize("sigma,adv", [(NAN, 40000.0), (0.16, NAN), (None, None), (0.16, 0.0)])
def test_nodata_with_impact_uses_conservative_fallback_and_says_so(sigma, adv):
    p = L1Params(impact_enabled=True, eta=2000.0)
    d = decide_fill(p, side=1, qty=1, ref_price=20000.0, bar_volume=50000.0,
                    adv=adv, sigma=sigma)
    assert d.filled_qty == 1
    assert "NO_DATA_FALLBACK" in d.flags
    assert d.impact_points == pytest.approx(20000.0 * 7.5 / 1e4)     # 7.5 bp ≈ 15 點


def test_scenarios_are_monotonic_in_cost():
    buys = {n: _fill(scenario_params(n)).price
            for n in ("optimistic", "base", "pessimistic")}
    assert buys["optimistic"] < buys["base"] < buys["pessimistic"]
    sells = {n: _fill(scenario_params(n), side=-1).price
             for n in ("optimistic", "base", "pessimistic")}
    assert sells["optimistic"] > sells["base"] > sells["pessimistic"]


def test_pessimistic_is_double_cost():
    base, pess = scenario_params("base"), scenario_params("pessimistic")
    assert pess.spread_points == 2 * base.spread_points
    assert pess.commission_mult == 2 * base.commission_mult


def test_sensitivity_grid_documented():
    assert SENSITIVITY_SPREADS == (2.0, 4.0, 6.0)


def test_unknown_scenario_rejected():
    with pytest.raises(KeyError):
        scenario_params("yolo")


def test_bad_inputs_raise():
    with pytest.raises(ValueError):
        L1Params(spread_points=-1)
    with pytest.raises(ValueError):
        L1Params(volume_limit=0)
    with pytest.raises(ValueError):
        _fill(qty=0)
    with pytest.raises(ValueError):
        _fill(side=0)


def test_infinite_inputs_are_rejected_not_crashing():
    assert _fill(bar_volume=float("inf")).reason == "NAN_VOLUME"
    assert _fill(ref_price=float("inf")).reason == "NAN_PRICE"

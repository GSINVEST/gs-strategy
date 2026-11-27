"""apply_taiwan_futures_costs：明確給 0 的滑價要是 0，只有 None 才用預設值。

不需要 zipline：以假的 zipline.api 取代，記錄 set_slippage / set_commission 收到的模型。
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "strategies" / "_common" / "futures_setup.py"
FILES = sorted(ROOT.glob("strategies/*/futures_setup.py"))


class _Model:
    def __init__(self, **kw):
        self.kw = kw


def _load(path, calls):
    api = types.ModuleType("zipline.api")
    for name in ("cancel_order", "continuous_future", "get_open_orders", "order_target", "set_benchmark", "symbol"):
        setattr(api, name, lambda *a, **k: None)
    api.set_commission = lambda **k: calls.setdefault("commission", k["futures"])
    api.set_slippage = lambda **k: calls.setdefault("slippage", k["futures"])
    commission = types.ModuleType("zipline.finance.commission"); commission.PerContract = _Model
    slippage = types.ModuleType("zipline.finance.slippage"); slippage.FixedSlippage = _Model
    keys = ("zipline", "zipline.api", "zipline.finance", "zipline.finance.commission", "zipline.finance.slippage")
    saved = {k: sys.modules.get(k) for k in keys}
    sys.modules.update({"zipline": types.ModuleType("zipline"), "zipline.api": api, "zipline.finance": types.ModuleType("zipline.finance"),
                        "zipline.finance.commission": commission, "zipline.finance.slippage": slippage})
    try:
        spec = importlib.util.spec_from_file_location("fs_costs_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod


def _apply(path, **kw):
    calls = {}
    mod = _load(path, calls)
    mod.apply_taiwan_futures_costs(**kw)
    return mod, calls


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_zero_spread_is_zero(path):
    _, calls = _apply(path, spread_points=0)
    assert calls["slippage"].kw["spread"] == 0


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_none_spread_uses_default(path):
    mod, calls = _apply(path, spread_points=None)
    assert calls["slippage"].kw["spread"] == mod.DEFAULT_SPREAD_POINTS


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_explicit_spread_passes_through(path):
    _, calls = _apply(path, spread_points=2.5)
    assert calls["slippage"].kw["spread"] == 2.5


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_per_contract_cost_none_and_zero(path):
    mod, calls = _apply(path, per_contract_cost=None)
    assert calls["commission"].kw["cost"] == mod.DEFAULT_PER_CONTRACT
    mod, calls = _apply(path, per_contract_cost={})
    assert calls["commission"].kw["cost"] == mod.DEFAULT_PER_CONTRACT
    mod, calls = _apply(path, per_contract_cost={"TX": 0})
    assert calls["commission"].kw["cost"]["TX"] == 0
    assert calls["commission"].kw["cost"]["MTX"] == mod.DEFAULT_PER_CONTRACT["MTX"]


@pytest.mark.parametrize("path", [p for p in FILES if p != CANONICAL], ids=lambda p: p.parent.name)
def test_copies_match_canonical(path):
    # 各策略的 futures_setup.py 是從 strategies/_common 複製出來的，必須保持一致。
    assert path.read_bytes() == CANONICAL.read_bytes()

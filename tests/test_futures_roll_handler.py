"""換月處理器：下一個合約不存在（資料包尚無次月）時要略過，不可對 None 下單。

不需要 zipline：以假的 zipline.api 取代，只驗證處理器的決策。
"""
import datetime as dt
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted(ROOT.glob("strategies/*/futures_setup.py"))


def _load(path, orders, open_orders=None):
    api = types.ModuleType("zipline.api")
    for name in ("cancel_order", "continuous_future", "set_benchmark", "set_commission", "set_slippage", "symbol"):
        setattr(api, name, lambda *a, **k: None)
    api.continuous_future = lambda *a, **k: "CONT"
    api.get_open_orders = lambda: open_orders or {}
    api.order_target = lambda asset, n: orders.append((asset, n))
    commission = types.ModuleType("zipline.finance.commission"); commission.PerContract = object
    slippage = types.ModuleType("zipline.finance.slippage"); slippage.FixedSlippage = object
    saved = {k: sys.modules.get(k) for k in ("zipline", "zipline.api", "zipline.finance", "zipline.finance.commission", "zipline.finance.slippage")}
    sys.modules.update({"zipline": types.ModuleType("zipline"), "zipline.api": api, "zipline.finance": types.ModuleType("zipline.finance"),
                        "zipline.finance.commission": commission, "zipline.finance.slippage": slippage})
    try:
        spec = importlib.util.spec_from_file_location("fs_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod


class _Contract:
    root_symbol = "TX"
    def __init__(self, name, close):
        self.name, self.auto_close_date = name, close


def _ctx(held, amount=1):
    return types.SimpleNamespace(portfolio=types.SimpleNamespace(positions={held: types.SimpleNamespace(amount=amount)}))


def _data(front, day):
    return types.SimpleNamespace(current=lambda cont, field: front, current_session=day)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_front_none_skips_roll(path):
    orders = []
    mod = _load(path, orders)
    held = _Contract("TX202611", dt.datetime(2026, 11, 18))
    mod.make_roll_futures_handler(10)(_ctx(held), _data(None, dt.datetime(2026, 11, 9)))
    assert orders == []


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_roll_to_new_front(path):
    orders = []
    mod = _load(path, orders)
    held, nxt = _Contract("TX202611", dt.datetime(2026, 11, 18)), _Contract("TX202612", dt.datetime(2026, 12, 16))
    mod.make_roll_futures_handler(10)(_ctx(held, 2), _data(nxt, dt.datetime(2026, 11, 9)))
    assert orders == [(held, 0), (nxt, 2)]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_not_yet_due_no_roll(path):
    orders = []
    mod = _load(path, orders)
    held = _Contract("TX202611", dt.datetime(2026, 11, 18))
    mod.make_roll_futures_handler(10)(_ctx(held), _data(None, dt.datetime(2026, 10, 1)))
    assert orders == []

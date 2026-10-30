"""zipline-tej 轉接層：在不改 futures_setup.py 與策略檔的前提下，換上 L1 滑價模型。

做法同 scripts/experiments/slippage_compare.py：執行期把 ``futures_setup.apply_taiwan_futures_costs``
換成帶 L1 模型的版本，跑完還原。zipline 只在函式內 import，讓純函式部分與測試不需要 bundle。

基準價用收盤價（``execution_price_type``，與回測可比），不用 zipline 預設的 (H+L)/2。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Optional

import yaml

from .costs import L1Params, decide_fill
from .types import DailyRow, SimResult, SimulatedFill, SimulatedOrder

COMMON = Path(__file__).resolve().parents[1]
_MODEL_CLS = None


def _isnan(x) -> bool:
    return x is None or x != x


def _l1_model_class():
    """延遲建立 FixedSlippage 的子類（需要 allowed_asset_types 讓 set_slippage(futures=...) 接受）。"""
    global _MODEL_CLS
    if _MODEL_CLS is not None:
        return _MODEL_CLS
    from zipline.finance import slippage as zs

    class L1Slippage(zs.FixedSlippage):
        def __init__(self, params: L1Params, strategy_id: str, cost_map: dict, sink: list):
            super().__init__(spread=params.spread_points)
            self.l1 = params
            self.strategy_id = strategy_id
            self.cost_map = cost_map
            self.sink = sink

        # -- 紀錄 ---------------------------------------------------------
        def _record(self, data, order, side, qty, ref, d):
            a = order.asset
            root = a.root_symbol
            comm = (self.cost_map.get(root, 0.0) * self.l1.commission_mult * d.filled_qty
                    if d is not None else 0.0)
            self.sink.append(SimulatedFill(
                order=SimulatedOrder(self.strategy_id, str(data.current_dt.date()), a.symbol, side, qty),
                root_symbol=root, multiplier=float(a.price_multiplier),
                filled_qty=d.filled_qty if d else 0,
                ref_price=None if _isnan(ref) else float(ref),
                fill_price=d.price if d else None,
                spread_points=d.spread_points if d else 0.0,
                impact_points=d.impact_points if d else 0.0,
                commission_ntd=float(comm), reason=d.reason if d else None,
                flags=d.flags if d else ()))

        def _adv_sigma(self, data, asset):
            if not self.l1.impact_enabled:
                return None, None
            try:
                h = data.history(asset, ["close", "volume"], 21, "1d")
                adv = float(h["volume"].iloc[:-1].mean())
                sigma = float(h["close"].pct_change().std() * (252 ** 0.5))
                return adv, sigma
            except Exception:
                return None, None

        # -- zipline 介面 -------------------------------------------------
        def simulate(self, data, asset, orders_for_asset, trading_policy, execution_price_type):
            volume = data.current(asset, "volume")
            price = data.current(asset, execution_price_type)
            if _isnan(volume) or volume == 0 or _isnan(price):
                # zipline 在這裡靜默 return；我們把拒絕與原因寫下來
                for o in orders_for_asset:
                    if o.open_amount == 0:
                        continue
                    side = 1 if o.open_amount > 0 else -1
                    d = decide_fill(self.l1, side=side, qty=abs(int(o.open_amount)),
                                    ref_price=price, bar_volume=volume)
                    self._record(data, o, side, abs(int(o.open_amount)), price, d)
                return
            yield from super().simulate(data, asset, orders_for_asset, trading_policy,
                                        execution_price_type)

        def process_order(self, data, order, execution_price_type):
            side = 1 if order.open_amount > 0 else -1
            qty = abs(int(order.open_amount))
            ref = data.current(order.asset, execution_price_type)
            vol = data.current(order.asset, "volume")
            adv, sigma = self._adv_sigma(data, order.asset)
            d = decide_fill(self.l1, side=side, qty=qty, ref_price=ref, bar_volume=vol,
                            adv=adv, sigma=sigma)
            self._record(data, order, side, qty, ref, d)
            if d.filled_qty == 0:
                return None, None
            return d.price, side * d.filled_qty

    _MODEL_CLS = L1Slippage
    return _MODEL_CLS


def _load_futures_setup():
    """以 ``futures_setup`` 這個名字載入 _common 版，讓策略 import 時拿到被換過的那份。"""
    mod = sys.modules.get("futures_setup")
    if mod is None or Path(getattr(mod, "__file__", "")).resolve() != (COMMON / "futures_setup.py"):
        spec = importlib.util.spec_from_file_location("futures_setup", COMMON / "futures_setup.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["futures_setup"] = mod
        spec.loader.exec_module(mod)
    return mod


class ZiplineSimRunner:
    """實作 executor.SimRunner。需要 TEJAPI_KEY、網路（TEJ 交易日曆）與 tquant_future bundle。"""

    def __init__(self, bundle: str = "tquant_future", calendar: str = "TEJ_morning_future",
                 framework_version: Optional[str] = None):
        self.bundle, self.calendar_name = bundle, calendar
        self._fw = framework_version

    def framework_version(self) -> str:
        """框架版本字串須與晉升記錄一致。fork 的套件 metadata 常是 0.0.0（沒有鑑別力），
        所以那種情況要求明確指定（參數或 GS_FRAMEWORK_VERSION，建議填 zipline-tej 的 commit）。"""
        fw = self._fw or os.environ.get("GS_FRAMEWORK_VERSION")
        if fw:
            return fw
        for dist in ("zipline-tej", "zipline-reloaded", "zipline"):
            try:
                ver = metadata.version(dist)
            except metadata.PackageNotFoundError:
                continue
            if ver not in ("0.0.0", "0.0.0.dev0"):
                return f"{dist} {ver}"
        raise RuntimeError("zipline 套件版本沒有鑑別力（0.0.0 或找不到）；"
                           "請以 framework_version 參數或 GS_FRAMEWORK_VERSION 明確指定（建議 zipline-tej commit）")

    def sessions(self, start: str, end: str) -> list:
        import pandas as pd
        from zipline.utils.calendar_utils import get_calendar
        cal = get_calendar(self.calendar_name)
        out = cal.sessions_in_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"))
        return [s.strftime("%Y-%m-%d") for s in out]

    def latest_snapshot(self) -> str:
        """bundle 裡**實際有成交量**的最後一天。不能用 reader.last_available_dt：
        它是交易日曆的結尾（實測 2027-10-29），不是資料的結尾。"""
        import numpy as np
        from zipline.data import bundles
        table = bundles.load(self.bundle).equity_daily_bar_reader._table
        day, vol = table["day"], table["volume"]
        best, chunk = 0, 4_000_000
        for i in range(0, len(day), chunk):
            d, v = day[i:i + chunk], vol[i:i + chunk]
            m = v > 0
            if m.any():
                best = max(best, int(d[m].max()))
        if best <= 0:
            raise RuntimeError(f"bundle {self.bundle} 沒有任何有成交量的資料")
        return str(np.datetime64(best, "s").astype("datetime64[D]"))

    def simulate(self, strategy_dir: Path, *, start: str, end: str, capital_base: float,
                 params: L1Params, scenario: str) -> SimResult:
        from zipline.api import set_benchmark, set_commission, set_slippage, symbol
        from zipline.finance.commission import PerContract
        from .. import runner as zl_runner

        strategy_dir = Path(strategy_dir)
        fs = _load_futures_setup()
        sink: list = []
        cost_map = dict(fs.DEFAULT_PER_CONTRACT)

        def patched(per_contract_cost=None, spread_points=None, benchmark=None):
            cm = dict(fs.DEFAULT_PER_CONTRACT)
            if per_contract_cost:
                cm.update(per_contract_cost)
            cost_map.update(cm)
            # PerContract 以 cost_map 計費；情境的手續費倍數在模型紀錄與下面這裡一致套用
            set_commission(futures=PerContract(
                cost={k: v * params.commission_mult for k, v in cm.items()}, exchange_fee=0))
            set_slippage(futures=_l1_model_class()(params, strategy_dir.name, cost_map, sink))
            if benchmark:
                set_benchmark(symbol(benchmark))

        orig = fs.apply_taiwan_futures_costs
        fs.apply_taiwan_futures_costs = patched
        prev_val = os.environ.get("STRATEGY_VALIDATION")
        os.environ["STRATEGY_VALIDATION"] = "0"
        try:
            cfg = zl_runner.load_config(strategy_dir / "config.yaml")
            cfg["start"], cfg["end"], cfg["capital_base"] = start, end, float(capital_base)
            with tempfile.TemporaryDirectory(prefix="dryrun_cfg_") as td:
                cfg_path = Path(td) / "config.yaml"
                cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
                perf = zl_runner.run_strategy_from_config(strategy_dir / "strategy.py", cfg_path)
        finally:
            fs.apply_taiwan_futures_costs = orig
            if prev_val is None:
                os.environ.pop("STRATEGY_VALIDATION", None)
            else:
                os.environ["STRATEGY_VALIDATION"] = prev_val

        daily = []
        for ts, row in perf.iterrows():
            pos = {p["sid"].symbol: int(p["amount"]) for p in (row.get("positions") or [])}
            daily.append(DailyRow(session=ts.strftime("%Y-%m-%d"),
                                  equity=float(row["portfolio_value"]), position=pos))
        return SimResult(daily=daily, fills=sink)

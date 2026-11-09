"""Common Zipline setup for Taiwan futures backtests.

All strategies share:
  * commission: PerContract by root_symbol (NTD per contract)
  * slippage:   FixedSlippage by spread in points
  * benchmark:  IR0001 (加權報酬指數)
  * roll:       calendar-based, additive adjustment
"""
from __future__ import annotations

from typing import Iterable, Mapping

from zipline.api import (
    cancel_order,
    continuous_future,
    get_open_orders,
    order_target,
    set_benchmark,
    set_commission,
    set_slippage,
    symbol,
)
from zipline.finance.commission import PerContract
from zipline.finance.slippage import FixedSlippage


# Default per-contract fees (NTD). 200 covers TX broker + clearing on a typical
# round trip ballpark; adjust per broker. Stock futures share the 100 NTD bucket.
DEFAULT_PER_CONTRACT = {
    "TX": 200,    # 大台
    "MTX": 100,   # 小台
    "TE": 200,    # 電子期
    "TF": 200,    # 金融期
    "GTF": 100,   # 櫃買期
    "XIF": 100,   # 非金電期
}

# Default slippage in *index points*. TX point value = 200 NTD; MTX = 50 NTD.
DEFAULT_SPREAD_POINTS = 6.0

DEFAULT_BENCHMARK = "IR0001"


def apply_taiwan_futures_costs(
    per_contract_cost: Mapping[str, float] | None = None,
    spread_points: float | None = None,
    benchmark: str | None = None,
) -> None:
    """Set commission, slippage, and benchmark in one call.

    Call this from inside ``initialize(context)``.
    """
    cost_map = dict(DEFAULT_PER_CONTRACT)
    if per_contract_cost:
        cost_map.update(per_contract_cost)
    set_commission(futures=PerContract(cost=cost_map, exchange_fee=0))
    set_slippage(
        futures=FixedSlippage(spread=spread_points or DEFAULT_SPREAD_POINTS)
    )
    if benchmark:
        # IR0001 (加權報酬指數) lives in the `tquant` equity bundle, not
        # `tquant_future`. Caller must opt-in *and* ensure the symbol exists
        # in the ingested bundle, otherwise zipline raises SymbolNotFound.
        set_benchmark(symbol(benchmark))


def make_continuous_taiwan_futures(
    root_symbols: Iterable[str],
    offset: int = 0,
    roll: str = "calendar",
    adjustment: str = "add",
):
    """Build continuous_future objects for a list of Taiwan futures roots."""
    return [
        continuous_future(rs, offset=offset, roll=roll, adjustment=adjustment)
        for rs in root_symbols
    ]


def make_roll_futures_handler(days_before_close: int = 10):
    """Return a schedulable function that rolls held contracts before expiry."""

    def roll_futures(context, data):
        open_orders = get_open_orders()
        for held_contract in list(context.portfolio.positions):
            if held_contract in open_orders:
                continue
            days_left = (
                held_contract.auto_close_date.date()
                - data.current_session.date()
            ).days
            if days_left > days_before_close:
                continue
            cont = continuous_future(
                held_contract.root_symbol,
                offset=0,
                roll="calendar",
                adjustment="add",
            )
            front = data.current(cont, "contract")
            if front is None:
                # 資料包還沒有下一個合約（例如近月快到期但次月尚無資料）：無法換月，維持持倉，
                # 不可對 None 下單（zipline 會丟 UnsupportedOrderParameters 使整個回測失敗）。
                continue
            if front != held_contract:
                pos_size = context.portfolio.positions[held_contract].amount
                order_target(held_contract, 0)
                order_target(front, pos_size)

    return roll_futures

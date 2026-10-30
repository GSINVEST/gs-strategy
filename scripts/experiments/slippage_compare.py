"""比較台指期回測的滑價模型：FixedSlippage vs VolatilityVolumeShare（實驗，不改策略）。

做法：在 import 策略前，把 futures_setup.apply_taiwan_futures_costs 換成帶指定滑價
模型的版本（只動執行期物件，不改檔）。每筆成交以包裝器記錄 (參考價, 成交價, 口數,
ψ, σ, ADV, 是否落到 NO_DATA 後備值)，輸出 JSON。

用法（需 TEJAPI_KEY 與 tquant_future bundle）：
  python slippage_compare.py <strategy_dir> <variant> <out.json> [start] [end]
variant: fixed6 | fixed0 | vvs_raw | vvs_default | vvs_x2 | vvs_eta0
  vvs_raw     = 未加 shim 的 VolatilityVolumeShare（驗證是否丟例外）
  vvs_*       = 加 execution_price_type shim 後的版本
"""
from __future__ import annotations

import json
import math
import os
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "strategies" / "_common"))
sys.path.insert(0, str(ROOT))

import futures_setup  # noqa: E402
import runner  # noqa: E402
from zipline.api import set_commission, set_slippage  # noqa: E402
from zipline.finance import slippage as zs  # noqa: E402
from zipline.finance.commission import PerContract  # noqa: E402

FILLS: list[dict] = []
NAN_VOL_EVENTS: list[dict] = []
VOLUME_LIMIT = 0.05


class LoggedFixed(zs.FixedSlippage):
    def process_order(self, data, order, execution_price_type):
        px, amt = super().process_order(data, order, execution_price_type)
        if px is not None:
            ref = float(data.current(order.asset, execution_price_type))
            FILLS.append(dict(date=str(data.current_dt.date()), root=order.asset.root_symbol,
                              sym=order.asset.symbol, amount=float(amt), ref=ref, fill=float(px),
                              mult=float(order.asset.price_multiplier), model="fixed"))
        return px, amt


class ShimVVS(zs.VolatilityVolumeShare):
    """補上 fork 的 execution_price_type 參數，其餘行為不變（僅供實驗）。"""

    def process_order(self, data, order, execution_price_type=None):
        mv, vol = self._get_window_data(data, order.asset, 20)
        hl = data.current(order.asset, ["high", "low"])
        ref = float(np.mean([hl["high"], hl["low"]]))
        bar_vol = float(data.current(order.asset, "volume"))
        if os.environ.get("VVS_NAN_GUARD") == "1" and (np.isnan(bar_vol) or np.isnan(ref)):
            # 診斷用：當 bar 成交量或 (H+L)/2 基準價為 NaN 時略過（原版 `not nan` 為 False，會算出 NaN 成交價）
            NAN_VOL_EVENTS.append(dict(date=str(data.current_dt.date()), sym=order.asset.symbol))
            return None, None
        px, amt = super().process_order(data, order)
        if px is not None:
            nodata = (mv == 0) or bool(np.isnan(vol))
            psi = abs(amt) / mv if mv else float("nan")
            FILLS.append(dict(date=str(data.current_dt.date()), root=order.asset.root_symbol,
                              sym=order.asset.symbol, amount=float(amt), ref=ref, fill=float(px),
                              mult=float(order.asset.price_multiplier), model="vvs",
                              adv=float(mv), sigma=None if np.isnan(vol) else float(vol),
                              bar_vol=bar_vol, psi=float(psi), nodata=bool(nodata)))
        return px, amt


def make_model(variant: str):
    if variant == "fixed6":
        return LoggedFixed(spread=6.0)
    if variant == "fixed0":
        return LoggedFixed(spread=0.0)
    if variant == "vvs_raw":
        return zs.VolatilityVolumeShare(volume_limit=VOLUME_LIMIT)
    if variant == "vvs_default":
        return ShimVVS(volume_limit=VOLUME_LIMIT)  # TX/MTX 不在表內 -> DEFAULT_ETA
    if variant == "vvs_x2":
        return ShimVVS(volume_limit=VOLUME_LIMIT, eta=zs.DEFAULT_ETA * 2)
    if variant == "vvs_eta0":
        return ShimVVS(volume_limit=VOLUME_LIMIT, eta=0.0)
    raise SystemExit(f"unknown variant {variant}")


def main() -> None:
    strat_dir, variant, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    model = make_model(variant)

    def patched(per_contract_cost=None, spread_points=None, benchmark=None):
        cost_map = dict(futures_setup.DEFAULT_PER_CONTRACT)
        if per_contract_cost:
            cost_map.update(per_contract_cost)
        set_commission(futures=PerContract(cost=cost_map, exchange_fee=0))
        set_slippage(futures=model)

    futures_setup.apply_taiwan_futures_costs = patched

    cfg = runner.load_config(strat_dir / "config.yaml")
    if len(sys.argv) > 4:
        cfg["start"] = sys.argv[4]
    if len(sys.argv) > 5:
        cfg["end"] = sys.argv[5]
    tmp = out.with_suffix(".cfg.yaml")
    import yaml
    tmp.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    os.environ["STRATEGY_VALIDATION"] = "0"
    res = {"variant": variant, "model": repr(model), "strategy": strat_dir.name,
           "start": cfg["start"], "end": cfg["end"]}
    try:
        perf = runner.run_strategy_from_config(strat_dir / "strategy.py", tmp)
    except Exception as exc:  # 要的就是例外本身
        res["error"] = f"{type(exc).__name__}: {exc}"
        try:
            res["traceback_tail"] = traceback.format_exc().splitlines()[-6:]
        except Exception:  # zipline 的例外鏈會讓 traceback 模組自己出錯
            res["traceback_tail"] = [repr(exc)]
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print("ERROR", res["error"])
        return

    pv = perf["portfolio_value"]
    ret = perf["returns"]
    cap = float(cfg.get("capital_base"))
    f = pd.DataFrame(FILLS)
    if len(f):
        f["side"] = np.sign(f["amount"])
        f["slip_pts"] = (f["fill"] - f["ref"]) * f["side"]  # 不利為正
        f["slip_ntd"] = f["slip_pts"] * f["amount"].abs() * f["mult"]
        f["contracts"] = f["amount"].abs()
    res.update(
        final_value=float(pv.iloc[-1]), net_return_pct=float((pv.iloc[-1] / cap - 1) * 100),
        sharpe_ann=float(ret.mean() / ret.std(ddof=1) * math.sqrt(252)) if ret.std() > 0 else None,
        n_days=int(len(ret)), n_fills=int(len(f)),
        contracts_traded=float(f.contracts.sum()) if len(f) else 0,
        slip_total_pts=float((f.slip_pts * f.contracts).sum()) if len(f) else 0,
        slip_total_ntd=float(f.slip_ntd.sum()) if len(f) else 0,
        slip_mean_pts_per_contract=float((f.slip_pts * f.contracts).sum() / f.contracts.sum()) if len(f) else None,
        slip_max_pts=float(f.slip_pts.max()) if len(f) else None,
        roots=sorted(f.root.unique().tolist()) if len(f) else [],
    )
    if len(f) and "psi" in f:
        v = f[f.model == "vvs"]
        res.update(psi_median=float(v.psi.median()), psi_max=float(v.psi.max()),
                   adv_median=float(v.adv.median()), sigma_median=float(v.sigma.median()),
                   n_nodata_fallback=int(v.nodata.sum()),
                   nodata_dates=v[v.nodata].date.tolist()[:20])
    res["nan_volume_skips"] = len(NAN_VOL_EVENTS)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    f.to_csv(out.with_suffix(".fills.csv"), index=False)
    print(json.dumps({k: v for k, v in res.items() if k != "nodata_dates"}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""為 promotion_gate evaluate 產生輸入：用 dry run 的 zipline 模擬器（L1 成本）回測策略。

    python scripts/d1_perf_inputs.py --strategy-id vgrsi_tx --out-dir /tmp/d1

輸出（含 `returns` 欄的 parquet，供 --perf／--stress）與 meta.json（framework_version、data_snapshot、
cost_model_version、區間）。perf＝base 情境（價差 6 點），stress＝pessimistic 情境（價差 12 點、手續費 ×2）。
報酬區間用該策略 config.yaml 的 start～end（樣本內回測），不做任何調參；這支腳本只負責「如實算出序列」，
判定由閘門做。需要 zipline-tej 與 TEJ 金鑰（環境變數），在有 bundle 的主機上跑。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from strategies._common.dryrun.costs import COST_MODEL_VERSION, scenario_params  # noqa: E402
from strategies._common.dryrun.zl_adapter import ZiplineSimRunner  # noqa: E402


def _returns(sim) -> pd.DataFrame:
    eq = pd.Series({d.session: d.equity for d in sim.daily}).sort_index()
    ret = eq.pct_change().dropna()
    return pd.DataFrame({"session": ret.index.astype(str), "returns": ret.to_numpy(dtype=float)})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy-id", required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--end", help="覆寫 config 的 end（預設用 config.yaml）")
    a = ap.parse_args(argv)

    sdir = REPO / "strategies" / a.strategy_id
    cfg = yaml.safe_load((sdir / "config.yaml").read_text(encoding="utf-8"))
    start, end = str(cfg["start"]), a.end or str(cfg["end"])
    capital = float(cfg.get("capital_base", 5_000_000))
    runner = ZiplineSimRunner()
    snap = runner.latest_snapshot()
    if snap < end:
        end = snap
    base_spread = float(cfg.get("params", {}).get("spread_points", 6.0))

    a.out_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for sc, name in (("base", "perf"), ("pessimistic", "stress")):
        sim = runner.simulate(sdir, start=start, end=end, capital_base=capital,
                              params=scenario_params(sc, base_spread), scenario=sc)
        df = _returns(sim)
        df.to_parquet(a.out_dir / f"{a.strategy_id}.{name}.parquet", index=False)
        out[name] = dict(n=len(df), mean=float(df["returns"].mean()), std=float(df["returns"].std()),
                         fills=len(sim.fills))
    meta = dict(strategy_id=a.strategy_id, start=start, end=end, capital_base=capital,
                framework_version=runner.framework_version(), data_snapshot=snap,
                cost_model_version=COST_MODEL_VERSION, series=out)
    (a.out_dir / f"{a.strategy_id}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

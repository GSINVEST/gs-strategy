"""D3 與真實 zipline-tej／bundle 的冒煙測試。沒有 TEJAPI_KEY 或 bundle 時 skip（CI 屬此類）。"""
from pathlib import Path

import pytest

from tests.conftest import requires_tej

pytestmark = requires_tej

BUNDLE_DIR = Path.home() / ".zipline" / "data" / "tquant_future"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def runner():
    if not BUNDLE_DIR.exists():
        pytest.skip("no tquant_future bundle ingested")
    from strategies._common.dryrun.zl_adapter import ZiplineSimRunner
    return ZiplineSimRunner()


def test_three_scenarios_on_vgrsi_are_ordered_and_cost_is_logged(runner):
    from strategies._common.dryrun.costs import SCENARIOS, scenario_params
    res = {sc: runner.simulate(REPO / "strategies/vgrsi_tx", start="2024-01-02", end="2024-06-28",
                               capital_base=5_000_000, params=scenario_params(sc), scenario=sc)
           for sc in SCENARIOS}
    eq = {sc: r.daily[-1].equity for sc, r in res.items()}
    assert eq["pessimistic"] <= eq["base"] <= eq["optimistic"]
    base = res["base"].fills
    assert base, "這段期間 vgrsi_tx 應該至少有一筆成交"
    f = next(x for x in base if x.filled_qty)
    assert f.spread_points == 3.0 and f.impact_points == 0.0
    assert abs(f.fill_price - f.ref_price) == pytest.approx(3.0)


def test_snapshot_is_data_end_not_calendar_end(runner):
    # reader.last_available_dt 是日曆結尾（實測落在 2027），不是資料結尾
    import pandas as pd
    snap = pd.Timestamp(runner.latest_snapshot())
    assert pd.Timestamp("2024-01-01") < snap < pd.Timestamp.now() + pd.Timedelta(days=1)


def test_sessions_accepts_plain_dates(runner):
    s = runner.sessions("2024-01-02", "2024-01-05")
    assert s[0] == "2024-01-02" and s == sorted(s) and len(s) <= 4


def test_framework_version_is_derived_from_installed_code(runner):
    fw = runner.framework_version()
    assert "+" in fw and len(fw.split("+")[1]) == 12
    assert fw == runner.framework_version()


def test_same_input_twice_is_identical(runner):
    from strategies._common.dryrun.costs import scenario_params
    kw = dict(start="2024-01-02", end="2024-03-29", capital_base=5_000_000,
              params=scenario_params("base"), scenario="base")
    a = runner.simulate(REPO / "strategies/vgrsi_tx", **kw)
    b = runner.simulate(REPO / "strategies/vgrsi_tx", **kw)
    assert [d.equity for d in a.daily] == [d.equity for d in b.daily]
    assert a.fills == b.fills

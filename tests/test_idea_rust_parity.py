# tests/test_idea_rust_parity.py
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from quant_crawler.ideas.ir_sim import simulate
from quant_crawler.ideas.rust_codegen import RustUnsupported, generate_rust

# 只含產生器與模擬器用到的欄位（不依賴 Gslang 快照）
def make_ir(ind="SMA@1", window=20):
    return {
        "id": "sma_trend",
        "constants": [{"name": "window", "value": window}, {"name": "position_contracts", "value": 1}],
        "signals": [{"id": "trend_state", "compute": {
            "indicators": {"close_price": {"fn": "CLOSE@1", "args": {}},
                           "avg": {"fn": ind, "args": {"window": "window"}}},
            "form": "zone_cmp", "left": "close_price", "right": "avg"}}],
        "states": [{"id": "FLAT", "predicate": "position == 0"},
                   {"id": "LONG", "predicate": "position > 0"},
                   {"id": "SHORT", "predicate": "position < 0"}],
        "transitions": [
            {"id": "T1", "from": ["FLAT"], "on": "BAR_CLOSE", "guard": "trend_state == HIGH",
             "to": "LONG", "orders": [{"kind": "target_position", "target": "position_contracts"}]},
            {"id": "T2", "from": ["LONG"], "on": "BAR_CLOSE", "guard": "trend_state == LOW",
             "to": "FLAT", "orders": [{"kind": "target_position", "target": "0"}]},
            {"id": "T3", "from": ["LONG"], "on": "ROLL_DUE", "guard": "true",
             "to": "LONG", "orders": [{"kind": "target_position", "target": "position_contracts"}]},
        ],
    }


def make_ir_short(ind="SMA@1", window=20):
    ir = make_ir(ind, window)
    ir["transitions"] = [
        {"id": "T1", "from": ["FLAT"], "on": "BAR_CLOSE", "guard": "trend_state == HIGH",
         "to": "LONG", "orders": [{"kind": "target_position", "target": "position_contracts"}]},
        {"id": "T4", "from": ["FLAT"], "on": "BAR_CLOSE", "guard": "trend_state == LOW",
         "to": "SHORT", "orders": [{"kind": "target_position", "target": "0 - position_contracts"}]},
        {"id": "T2", "from": ["LONG"], "on": "BAR_CLOSE", "guard": "not trend_state == HIGH",
         "to": "FLAT", "orders": [{"kind": "target_position", "target": "0"}]},
        {"id": "T5", "from": ["SHORT"], "on": "BAR_CLOSE",
         "guard": "trend_state == HIGH or trend_state == NO_SIGNAL",
         "to": "FLAT", "orders": [{"kind": "target_position", "target": "0"}]},
    ]
    return ir


def bars(n=400, seed=7, nan_at=()):
    rnd = random.Random(seed)
    px, out = 17000.0, []
    for i in range(n):
        o = px
        c = o + rnd.uniform(-80, 85)
        out.append((o, max(o, c) + 5, min(o, c) - 5, float("nan") if i in nan_at else c))
        px = c
    return out


def test_sim_produces_fills():
    fills = simulate(make_ir(), bars())
    assert len(fills) >= 10
    assert fills[0][0] >= 20  # 暖機期（window 根）不交易
    assert [f[2] for f in fills] == [1.0, 0.0] * (len(fills) // 2) + [1.0] * (len(fills) % 2)


def test_sim_short_reaches_short_state():
    fills = simulate(make_ir_short(), bars())
    assert any(f[2] < 0 for f in fills) and any(f[2] > 0 for f in fills)


def test_sim_nan_current_bar_is_no_signal():
    # 當根收盤為 NaN：CLOSE 指標回 None → NO_SIGNAL，該根不產生新目標
    clean = simulate(make_ir(), bars())
    sig_bar = clean[0][0] - 1
    nan = simulate(make_ir(), bars(nan_at=(sig_bar,)))
    assert nan[0][0] != clean[0][0]


def test_window_nonpositive_refused():
    for w in (0, -3):
        with pytest.raises(RustUnsupported):
            generate_rust(make_ir("SMA@1", w))


def test_unsupported_indicator_refused():
    ir = make_ir()
    ir["signals"][0]["compute"]["indicators"]["avg"]["fn"] = "RSI@1"
    with pytest.raises(RustUnsupported):
        generate_rust(ir)


def test_non_zone_cmp_refused():
    ir = make_ir()
    ir["signals"][0]["compute"]["form"] = "zone_threshold"
    with pytest.raises(RustUnsupported):
        generate_rust(ir)


# 已知限制：Rust SMA 循序加總、NumPy 成對加總，浮點最後一位元可能不同；
# 固定種子資料要求逐筆相等，平手邊界翻轉時須換資料，不可放寬比對。
@pytest.mark.skipif(shutil.which("cargo") is None, reason="Rust parity 未實跑：需要 cargo")
@pytest.mark.parametrize("maker", [make_ir, make_ir_short], ids=["long", "short"])
@pytest.mark.parametrize("ind,window", [("SMA@1", 20), ("EMA@1", 5)])
@pytest.mark.parametrize("nan_at", [(), (60, 61, 150, 151, 152, 301, 399)], ids=["clean", "nan"])
def test_rust_matches_python(tmp_path, maker, ind, window, nan_at):
    ir = maker(ind, window)
    (tmp_path / "src").mkdir()
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname="strat"\nversion="0.0.0"\nedition="2021"\n[workspace]\n', encoding="utf-8")
    (tmp_path / "src" / "main.rs").write_text(generate_rust(ir), encoding="utf-8")
    b = subprocess.run(["cargo", "build", "--release", "-q"], cwd=tmp_path, capture_output=True, text=True)
    assert b.returncode == 0, b.stderr
    data = bars(nan_at=nan_at)
    stdin = "\n".join(" ".join(repr(x) for x in bar) for bar in data) + "\n"
    r = subprocess.run([str(tmp_path / "target" / "release" / "strat")], input=stdin,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = [tuple(float(x) for x in line.split(",")) for line in r.stdout.splitlines()]
    want = [(float(i), p, pos) for i, p, pos in simulate(ir, data)]
    assert got == want and len(got) >= 1, (len(got), len(want))

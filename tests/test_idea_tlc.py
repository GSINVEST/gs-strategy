# tests/test_idea_tlc.py
import json
import subprocess

import pytest

from quant_crawler.ideas import tlc
from quant_crawler.ideas.tlc import run_tlc, verdict_from_atlas, write_demo_params


def _atlas(status):
    return {"schema": "strategy-fsm-atlas/1", "tlc_ran": True,
            "targets": [{"id": "x", "status": status}]}


def test_verdict_mapping():
    assert verdict_from_atlas(_atlas("pass_with_bounds"), "x") == "pass_with_bounds"
    assert verdict_from_atlas(_atlas("counterexample"), "x") == "counterexample"
    assert verdict_from_atlas(_atlas("whatever_new"), "x") == "inconclusive"


def test_real_atlas_statuses_are_preserved():
    # fsm_atlas.py 實際會輸出的 target status（TARGET_ORDER + unchecked/vacuous/proposed_pass）
    for s in ("needs_review", "mutation_not_caught", "unchecked", "vacuous", "proposed_pass"):
        assert verdict_from_atlas(_atlas(s), "x") == s


def test_not_run_is_unchecked():
    a = _atlas("pass_with_bounds")
    a["tlc_ran"] = False
    assert verdict_from_atlas(a, "x") == "unchecked"


def test_missing_target_is_unchecked():
    assert verdict_from_atlas(_atlas("pass_with_bounds"), "other") == "unchecked"


@pytest.mark.parametrize("bad", [
    {"tlc_ran": True, "targets": 5},          # targets 不是 list
    {"tlc_ran": True, "targets": [1]},        # 元素不是 dict
    {"tlc_ran": True, "targets": "x"},        # targets 是字串（可迭代但元素是字元）
    {"tlc_ran": True, "targets": None},
    {"tlc_ran": True, "targets": [{"id": "x", "status": ["pass_with_bounds"]}]},  # status 非字串
    {"tlc_ran": True, "targets": [{"id": ["x"], "status": "pass_with_bounds"}]},  # id 非字串
])
def test_malformed_targets_fail_closed(bad):
    assert verdict_from_atlas(bad, "x") == "unchecked"


@pytest.mark.parametrize("bad", [None, [], "atlas", 5])
def test_non_dict_atlas_fails_closed(bad):
    assert verdict_from_atlas(bad, "x") == "unchecked"


def test_demo_params_are_marked_demo(tmp_path):
    ir = {"id": "x", "costs": {"slippage": {"spread_points": 6.0}}}
    p = write_demo_params(ir, tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert p.name == "x.demo.json" and d["ir_id"] == "x"
    assert d["schema"] == "strategy-fsm-model-params/1"
    assert all(v["kind"] == "demo" for v in d["params"].values())


def test_demo_params_skip_values_ir_already_fixes(tmp_path):
    # IR 已有具體滑價／跳空時，參數檔不得覆蓋（ir_to_tla 會拒絕 PARAM_OVERRIDES_IR）
    ir = {"id": "x",
          "costs": {"slippage": {"adverse_points_per_fill": 3.0}},
          "stress": [{"kind": "gap", "params": {"gap_points": 500}}]}
    p = write_demo_params(ir, tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert "slip_pts" not in d["params"] and "gap_points" not in d["params"]


def test_demo_params_fill_todo_values(tmp_path):
    ir = {"id": "x",
          "costs": {"slippage": {"adverse_points_per_fill": {"todo": "TODO(人)"}}},
          "stress": [{"kind": "gap", "params": {"gap_points": {"todo": "TODO(人)"}}}]}
    p = write_demo_params(ir, tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert set(d["params"]) == {"slip_pts", "gap_points"}


# ───────── run_tlc 的失敗路徑（monkeypatch subprocess.run，不需 Java）─────────

@pytest.fixture
def fake_root(tmp_path):
    root = tmp_path / "formal"
    (root / "scripts" / "formal").mkdir(parents=True)
    (root / "scripts" / "formal" / "gslang_compiler.py").write_text("", encoding="utf-8")
    (root / "scripts" / "formal" / "fsm_atlas.py").write_text("", encoding="utf-8")
    return root


@pytest.fixture
def ir_file(tmp_path):
    p = tmp_path / "x.ir.json"
    p.write_text(json.dumps({"id": "x"}), encoding="utf-8")
    return p


def _out_arg(cmd):
    return cmd[cmd.index("--out") + 1]


def _fake_run(monkeypatch, behavior):
    """behavior(cmd) -> CompletedProcess 或丟例外。"""
    def fake(cmd, **kw):
        return behavior(cmd)
    monkeypatch.setattr(tlc.subprocess, "run", fake)


def test_timeout_is_inconclusive(monkeypatch, fake_root, ir_file, tmp_path):
    def boom(cmd):
        raise subprocess.TimeoutExpired(cmd, 1)
    _fake_run(monkeypatch, boom)
    r = run_tlc(ir_file, tmp_path / "w", root=str(fake_root))
    assert r.verdict == "inconclusive"
    assert "timeout" in r.detail.get("error", "").lower()


def test_launch_oserror_is_unchecked(monkeypatch, fake_root, ir_file, tmp_path):
    def boom(cmd):
        raise FileNotFoundError("python missing")
    _fake_run(monkeypatch, boom)
    r = run_tlc(ir_file, tmp_path / "w", root=str(fake_root))
    assert r.verdict == "unchecked"


def test_stale_atlas_is_not_trusted_on_nonzero_exit(monkeypatch, fake_root, ir_file, tmp_path):
    # 上一輪留下的 pass_with_bounds atlas；本輪子行程 rc=1 崩潰 → 不得回傳舊 verdict
    work = tmp_path / "w"
    work.mkdir()
    (work / "atlas.json").write_text(json.dumps(_atlas("pass_with_bounds")), encoding="utf-8")

    def no_output(cmd):
        return subprocess.CompletedProcess(cmd, 1, "", "crash")
    _fake_run(monkeypatch, no_output)
    r = run_tlc(ir_file, work, root=str(fake_root))
    assert r.verdict == "unchecked"


def test_stale_atlas_is_not_trusted_on_zero_exit_without_new_file(monkeypatch, fake_root, ir_file, tmp_path):
    # 突變防護：rc=0 但子行程沒寫新檔，舊的 pass_with_bounds 不得被當成本次結果
    work = tmp_path / "w"
    work.mkdir()
    (work / "atlas.json").write_text(json.dumps(_atlas("pass_with_bounds")), encoding="utf-8")

    def no_new_file(cmd):
        return subprocess.CompletedProcess(cmd, 0, "", "")
    _fake_run(monkeypatch, no_new_file)
    r = run_tlc(ir_file, work, root=str(fake_root))
    assert r.verdict == "unchecked"
    assert r.verdict != "pass_with_bounds"


def test_corrupt_atlas_is_unchecked(monkeypatch, fake_root, ir_file, tmp_path):
    def garbage(cmd):
        open(_out_arg(cmd), "w", encoding="utf-8").write("{not json")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    _fake_run(monkeypatch, garbage)
    r = run_tlc(ir_file, tmp_path / "w", root=str(fake_root))
    assert r.verdict == "unchecked"


def test_structurally_bad_atlas_is_unchecked(monkeypatch, fake_root, ir_file, tmp_path):
    def bad_shape(cmd):
        open(_out_arg(cmd), "w", encoding="utf-8").write(json.dumps({"tlc_ran": True, "targets": 5}))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    _fake_run(monkeypatch, bad_shape)
    r = run_tlc(ir_file, tmp_path / "w", root=str(fake_root))
    assert r.verdict == "unchecked"


def test_nonzero_exit_is_not_trusted_even_if_atlas_says_pass(monkeypatch, fake_root, ir_file, tmp_path):
    # fsm_atlas.main 成功時一律 return 0；非零＝崩潰，atlas 不可信
    def pass_then_fail(cmd):
        open(_out_arg(cmd), "w", encoding="utf-8").write(json.dumps(_atlas("pass_with_bounds")))
        return subprocess.CompletedProcess(cmd, 1, "", "err")
    _fake_run(monkeypatch, pass_then_fail)
    r = run_tlc(ir_file, tmp_path / "w", root=str(fake_root))
    assert r.verdict == "unchecked"


def test_counterexample_exit0_is_kept(monkeypatch, fake_root, ir_file, tmp_path):
    # 合法的反例結果（rc=0）不得被丟掉
    def cex(cmd):
        open(_out_arg(cmd), "w", encoding="utf-8").write(json.dumps(_atlas("counterexample")))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    _fake_run(monkeypatch, cex)
    r = run_tlc(ir_file, tmp_path / "w", root=str(fake_root))
    assert r.verdict == "counterexample"


def test_leftover_ir_and_params_are_cleared(monkeypatch, fake_root, ir_file, tmp_path):
    work = tmp_path / "w"
    (work / "ir").mkdir(parents=True)
    (work / "params").mkdir(parents=True)
    (work / "ir" / "other.ir.json").write_text("{}", encoding="utf-8")
    (work / "params" / "other.demo.json").write_text("{}", encoding="utf-8")
    seen = {}

    def record(cmd):
        seen["ir"] = sorted(p.name for p in (work / "ir").iterdir())
        seen["params"] = sorted(p.name for p in (work / "params").iterdir())
        return subprocess.CompletedProcess(cmd, 1, "", "")
    _fake_run(monkeypatch, record)
    run_tlc(ir_file, work, root=str(fake_root))
    assert seen["ir"] == ["x.ir.json"]
    assert seen["params"] == ["x.demo.json"]


def test_clear_dir_keeps_unrelated_files(monkeypatch, fake_root, ir_file, tmp_path):
    work = tmp_path / "w"
    (work / "ir").mkdir(parents=True)
    (work / "params").mkdir(parents=True)
    (work / "ir" / "keep.txt").write_text("keep", encoding="utf-8")
    (work / "params" / "keep.txt").write_text("keep", encoding="utf-8")
    (work / "ir" / "other.ir.json").write_text("{}", encoding="utf-8")
    (work / "params" / "other.demo.json").write_text("{}", encoding="utf-8")

    def nothing(cmd):
        return subprocess.CompletedProcess(cmd, 1, "", "")
    _fake_run(monkeypatch, nothing)
    run_tlc(ir_file, work, root=str(fake_root))
    assert (work / "ir" / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (work / "params" / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (work / "ir" / "other.ir.json").exists()
    assert not (work / "params" / "other.demo.json").exists()

# tests/test_idea_tlc.py
import json

from quant_crawler.ideas.tlc import verdict_from_atlas, write_demo_params


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

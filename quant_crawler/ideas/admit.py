# quant_crawler/ideas/admit.py
"""入庫：把通過者寫成 dashboard-spec v1 bundle，並登記進 strategies/INDEX.yaml。"""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from quant_crawler.ideas.card import IdeaCard


def _const(ir: dict, name: str, default=None):
    for c in ir.get("constants", []):
        if c["name"] == name:
            return c["value"]
    return default


def write_bundle(card: IdeaCard, ir: dict, strategy_py: str, strategies_dir: Path, gslang_src: Path) -> Path:
    d = strategies_dir / card.id
    if d.exists():
        raise FileExistsError(f"{d} 已存在；入庫不覆蓋")
    d.mkdir(parents=True)
    (d / "strategy.py").write_text(strategy_py, encoding="utf-8", newline="\n")
    (d / "gslang").mkdir()
    for name in ("__init__.py", "indicators.py"):        # vendored：bundle 不得依賴 _common
        shutil.copyfile(gslang_src / name, d / "gslang" / name)
    sym = [i["id"] for i in ir.get("instruments", [])]
    cost = ir.get("costs", {}).get("commission", {}).get("per_instrument", {})
    spread = ir.get("costs", {}).get("slippage", {}).get("spread_points")
    params = {"root_symbol": sym[0] if sym else "TX",
              "position_contracts": _const(ir, "position_contracts", 1),
              "days_before_close": _const(ir, "days_before_close", 10),
              "per_contract_cost": dict(cost), "spread_points": spread}
    manifest = {
        "id": card.id, "name": card.title or card.id,
        "description": f"idea 漏斗產物。假說：{card.hypothesis}\n來源：{card.source}\n"
                       f"TLC verdict：{card.verdict}（示範參數的有限模型；不代表策略有效）。\n",
        "asset_class": "future", "bundle": "tquant_future",
        "start": "2020-01-01", "end": "2026-04-30",
        "capital_base": int(_const(ir, "capital_base", 5_000_000)),
        "params": params, "tags": ["idea-funnel", "gslang", "taiwan", "futures"],
        "requires_tej_key": True, "extra_deps": [], "symbols": sym, "spec_version": 1,
    }
    (d / "manifest.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
                                     encoding="utf-8", newline="\n")
    config = {"start": manifest["start"], "end": manifest["end"], "capital_base": manifest["capital_base"],
              "bundle": "tquant_future", "params": params}
    (d / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                                   encoding="utf-8", newline="\n")
    (d / "README.md").write_text(
        f"# {card.id}\n\n{card.hypothesis}\n\n- 來源：{card.source}\n- Gslang：`{card.gsl_path}`\n"
        f"- 批次：{card.batch_id}\n- 缺陷（抽取時列出）：{'；'.join(card.flaws) or '—'}\n\n"
        "> 本策略由 idea 漏斗自動產生，TLC PASS 只代表「在示範參數的有限模型內未發現違反」。\n",
        encoding="utf-8", newline="\n")
    return d


def update_index(strategies_dir: Path, entry: dict) -> None:
    p = strategies_dir / "INDEX.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
    doc = doc or {"schema": "strategy-index/1", "entries": []}
    if any(e["id"] == entry["id"] for e in doc["entries"]):
        raise ValueError(f"{entry['id']} 已在索引；不覆蓋")
    doc["entries"] = sorted(doc["entries"] + [entry], key=lambda e: e["id"])
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")


def gate_admission(card: IdeaCard, batch_prereg_ok: bool, bundle_ok: bool, bt_result: Path | None) -> list[str]:
    why = []
    if card.status != "bt_done":
        why.append(f"卡片狀態是 {card.status}，必須是 bt_done")
    if not batch_prereg_ok:
        why.append("批次 preregistration 未通過 check_preregistration（需人簽核）")
    if not bundle_ok:
        why.append("bundle 未通過 validate_dashboard_bundle")
    if bt_result is None or not Path(bt_result).is_file():
        why.append("沒有回測結果檔")
    return why

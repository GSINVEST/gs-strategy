# quant_crawler/ideas/admit.py
"""入庫：把通過者寫成 dashboard-spec v1 bundle，並登記進 strategies/INDEX.yaml。"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

import yaml

from quant_crawler.ideas.card import _ID_RE, IdeaCard, rel_to_repo

_SKIP_DIRS = {"__pycache__"}


def _umask() -> int:
    m = os.umask(0)
    os.umask(m)
    return m


def _normalize_perms(root: Path) -> None:
    """mkdtemp 建的目錄是 0o700，rename 後其他使用者讀不到。依 umask 還原：
    目錄 0o777&~umask、檔案 0o666&~umask，原有可執行位保留。"""
    um = _umask()
    for dp, dns, fns in os.walk(root):
        os.chmod(dp, 0o777 & ~um)
        for fn in fns:
            f = Path(dp) / fn
            is_x = bool(stat.S_IMODE(f.stat().st_mode) & 0o111)      # 可執行位保留
            os.chmod(f, (0o777 if is_x else 0o666) & ~um)


def bundle_sha256(d: Path) -> str:
    """整個 bundle 的內容雜湊：對所有檔案（相對 posix 路徑穩定排序）的「路徑＋內容」。
    排除 __pycache__／*.pyc（跑回測時 import 策略會產生，不屬於內容）。"""
    h = hashlib.sha256()
    files = []
    for dp, dns, fns in os.walk(d):
        dns[:] = [x for x in dns if x not in _SKIP_DIRS]
        for fn in fns:
            if fn.endswith(".pyc"):
                continue
            f = Path(dp) / fn
            files.append((f.relative_to(d).as_posix(), f))
    for rel, f in sorted(files):
        h.update(rel.encode("utf-8") + b"\x00" + f.read_bytes() + b"\x00")
    return h.hexdigest()


def _const(ir: dict, name: str, default=None):
    for c in ir.get("constants", []):
        if c["name"] == name:
            return c["value"]
    return default


def _check_id(cid) -> None:
    if not isinstance(cid, str) or not _ID_RE.match(cid):
        raise ValueError(f"不合法的 card.id：{cid!r}")


def write_bundle(card: IdeaCard, ir: dict, strategy_py: str, strategies_dir: Path, gslang_src: Path,
                 start: str, end: str) -> Path:
    cid = card.id
    _check_id(cid)
    d = strategies_dir / cid
    if d.exists():
        raise FileExistsError(f"{d} 已存在；入庫不覆蓋")
    tmp = Path(tempfile.mkdtemp(prefix=f".{cid}.tmp-", dir=strategies_dir))
    try:
        _fill_bundle(tmp, card, ir, strategy_py, gslang_src, start, end)
        _normalize_perms(tmp)
        os.rename(tmp, d)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return d


def stage_bundle(card: IdeaCard, ir: dict, strategy_py: str, dest: Path, gslang_src: Path,
                 start: str, end: str) -> tuple[Path, str]:
    """寫 staging bundle（回測用、入庫前的唯一內容來源）。可重做：既有 dest 會被取代。回傳 (路徑, sha256)。"""
    _check_id(card.id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".stage-", dir=dest.parent))
    try:
        _fill_bundle(tmp, card, ir, strategy_py, gslang_src, start, end)
        _normalize_perms(tmp)
        if dest.exists():
            shutil.rmtree(dest)
        os.rename(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest, bundle_sha256(dest)


def copy_staged_bundle(staged: Path, strategies_dir: Path, cid: str, expected_sha: str) -> Path:
    """把 staging bundle 複製（tmp 目錄＋rename）到 strategies/<id>，複製後比對 sha，不符即拒絕。"""
    _check_id(cid)
    d = strategies_dir / cid
    if d.exists():
        raise FileExistsError(f"{d} 已存在；入庫不覆蓋")
    tmp = Path(tempfile.mkdtemp(prefix=f".{cid}.tmp-", dir=strategies_dir))
    try:
        shutil.copytree(staged, tmp, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        got = bundle_sha256(tmp)
        if got != expected_sha:
            raise ValueError(f"staging bundle 內容雜湊 {got[:12]} 與登記的 {expected_sha[:12]} 不符")
        _normalize_perms(tmp)
        os.rename(tmp, d)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return d


def _fill_bundle(d: Path, card: IdeaCard, ir: dict, strategy_py: str, gslang_src: Path,
                 start: str, end: str) -> None:
    (d / "strategy.py").write_text(strategy_py, encoding="utf-8", newline="\n")
    (d / "gslang").mkdir()
    for f in sorted(Path(gslang_src).glob("*.py")):      # vendored：bundle 不得依賴 _common
        shutil.copyfile(f, d / "gslang" / f.name)
    (d / "gslang" / "SOURCE.txt").write_text(
        f"vendored copy, do not edit\nsource: {rel_to_repo(gslang_src)}\n", encoding="utf-8", newline="\n")
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
        "start": start, "end": end,
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


def update_index(strategies_dir: Path, entry: dict) -> None:
    p = strategies_dir / "INDEX.yaml"
    if not isinstance(entry, dict) or "id" not in entry:
        raise ValueError("index entry 缺 id")
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
    doc = doc or {"schema": "strategy-index/1", "entries": []}
    if not isinstance(doc, dict):
        raise ValueError("INDEX.yaml 格式不是 mapping")
    if doc.get("schema", "strategy-index/1") != "strategy-index/1":
        raise ValueError(f"未知 schema：{doc.get('schema')!r}")
    doc.setdefault("schema", "strategy-index/1")
    entries = doc.get("entries") or []
    if not isinstance(entries, list) or any(not isinstance(e, dict) or "id" not in e for e in entries):
        raise ValueError("INDEX.yaml 既有 entries 格式錯誤（缺 id）")
    if any(e["id"] == entry["id"] for e in entries):
        raise ValueError(f"{entry['id']} 已在索引；不覆蓋")
    doc["entries"] = sorted(entries + [entry], key=lambda e: e["id"])
    fd, tmp = tempfile.mkstemp(prefix="INDEX.", suffix=".tmp", dir=strategies_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
        # mkstemp 建的是 0o600；replace 前改成既有 INDEX 的 mode，沒有就依 umask
        mode = stat.S_IMODE(p.stat().st_mode) if p.exists() else (0o666 & ~_umask())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def gate_admission(card: IdeaCard, batch_prereg_ok: bool, bundle_ok: bool, bt_result: Path | None) -> list[str]:
    why = []
    if card.status != "bt_done":
        why.append(f"卡片狀態是 {card.status}，必須是 bt_done")
    if card.verdict != "pass_with_bounds":
        why.append(f"TLC verdict 是 {card.verdict!r}，必須是 pass_with_bounds")
    if batch_prereg_ok is not True:
        why.append("批次 preregistration 未通過 check_preregistration（需人簽核）")
    if bundle_ok is not True:
        why.append("bundle 未通過 validate_dashboard_bundle")
    try:
        ok = bt_result is not None and Path(bt_result).is_file() and Path(bt_result).stat().st_size > 0
    except (OSError, TypeError):
        ok = False
    if not ok:
        why.append("沒有回測結果檔（需存在且非空）")
    return why

"""idea 漏斗 CLI：python -m quant_crawler.ideas <extract|triage|fsm|batch|backtest|admit|report>。

誠實性：沒有 TEJAPI_KEY 時 backtest 明確回報「未實跑」；沒有 GDECK_LLM_* 時 extract／fsm 明確報錯；
本 CLI 不代跑 S5 回測。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from quant_crawler.ideas.card import iter_cards, load_card, save_card, set_status
from quant_crawler.ideas.triage import apply_triage, normalize_signature

REPO = Path(__file__).resolve().parents[2]
CARDS = REPO / "ideas"
STRATEGIES = REPO / "strategies"
PREREG_DIR = REPO / "experiments" / "preregistration"
GSLANG_SRC = REPO / "strategies" / "_common" / "gslang"


def run_all(cards_dir: Path, known_signatures: set | None = None) -> int:
    """對 status=new 的卡分診。簽章重複：先出現者（依 id 排序）保留，之後同簽章者 drop。

    known_signatures：呼叫端額外已知的簽章（會先正規化）。
    """
    cards = list(iter_cards(cards_dir))
    known = {normalize_signature(c.signature) for c in cards
             if c.status not in ("new", "drop") and c.signature}
    known |= {normalize_signature(s) for s in (known_signatures or ())}
    n = 0
    for c in cards:
        if c.status != "new":
            continue
        apply_triage(c, known)
        if c.status == "triaged" and c.signature:
            known.add(normalize_signature(c.signature))
        save_card(c, cards_dir)
        n += 1
    return n


def triage_dir(cards_dir: Path) -> int:
    return run_all(cards_dir)


def report_counts(cards_dir: Path) -> Counter:
    return Counter(c.status for c in iter_cards(cards_dir))


def _run_check(script: str, target: Path) -> tuple[bool, str]:
    """真的跑 repo 內的驗證腳本，回 (通過, 輸出)。"""
    r = subprocess.run([sys.executable, str(REPO / "scripts" / script), str(target)],
                       capture_output=True, text=True, cwd=str(REPO))
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def admit_card(card, *, strategies_dir: Path, prereg_dir: Path, gslang_src: Path,
               bt_result: Path | None, work_dir: Path, cards_dir: Path | None = None,
               admitted_at: str = "") -> list[str]:
    """入庫。回傳未通過原因（空 list = 已入庫）。失敗時移除剛寫入的 bundle，不留殘件。

    batch_prereg_ok／bundle_ok 都由真的執行 check_preregistration／validate_dashboard_bundle 得出。
    """
    from quant_crawler.ideas.admit import gate_admission, update_index, write_bundle

    # 先擋狀態／verdict／結果檔（不寫任何東西）；兩個驗證項此時傳 True 只為了單獨看其餘原因。
    pre = [w for w in gate_admission(card, True, True, bt_result)]
    if pre:
        return pre
    try:
        ir = json.loads(Path(card.ir_path).read_text(encoding="utf-8"))
        spy = (work_dir / card.id / "strategy.py").read_text(encoding="utf-8")
    except (OSError, ValueError) as e:
        return [f"讀不到 IR 或 strategy.py：{type(e).__name__}: {e}"]

    prereg = prereg_dir / f"{card.batch_id}.yaml"
    if card.batch_id and prereg.is_file():
        prereg_ok, prereg_out = _run_check("check_preregistration.py", prereg)
    else:
        prereg_ok, prereg_out = False, f"找不到批次 preregistration：{prereg}"

    strategies_dir.mkdir(parents=True, exist_ok=True)
    bundle = write_bundle(card, ir, spy, strategies_dir, gslang_src)
    try:
        bundle_ok, bundle_out = _run_check("validate_dashboard_bundle.py", bundle)
        why = gate_admission(card, prereg_ok, bundle_ok, bt_result)
        if why:
            if not prereg_ok:
                why.append(prereg_out)
            if not bundle_ok:
                why.append(bundle_out)
            shutil.rmtree(bundle, ignore_errors=True)
            return why
        update_index(strategies_dir, {"id": card.id, "batch_id": card.batch_id, "verdict": card.verdict,
                                      "admitted_at": admitted_at, "source": card.source})
    except BaseException:
        shutil.rmtree(bundle, ignore_errors=True)
        raise
    set_status(card, "admitted", "通過入庫閘門")
    if cards_dir is not None:
        save_card(card, cards_dir)
    return []


def _cmd_extract(a) -> int:
    from quant_crawler.config import DB_PATH
    from quant_crawler.ideas.extract import OpenAICompatLLM, extract_card
    from quant_crawler.storage.db import Storage
    try:
        llm = OpenAICompatLLM()
    except Exception as e:  # 缺 GDECK_LLM_* 時明確報錯，不假裝成功
        print(f"無法抽取：{e}（需要環境變數 GDECK_LLM_URL／GDECK_LLM_KEY／GDECK_LLM_MODEL）", file=sys.stderr)
        return 2
    st = Storage(DB_PATH)
    made = 0
    for rec in st.latest(a.limit or 50, a.source):
        if (rec.published or "") < a.since:
            continue
        card = extract_card(rec, llm)
        if (CARDS / f"{card.id}.md").exists():
            continue
        save_card(card, CARDS)
        made += 1
    print(f"新增 {made} 張卡 → {CARDS}")
    return 0


def _cmd_triage(a) -> int:
    print(f"分診 {run_all(CARDS)} 張")
    return 0


def _cmd_report(a) -> int:
    for k, v in sorted(report_counts(CARDS).items()):
        print(f"{k:14s}{v}")
    defers = Counter(c.reason for c in iter_cards(CARDS) if c.status == "defer")
    for why, n in defers.most_common(10):
        print(f"  defer x {n}：{why}")
    return 0


def _cmd_fsm(a) -> int:
    import tempfile

    from quant_crawler.ideas import gslang_bridge as gb
    from quant_crawler.ideas import tlc
    from quant_crawler.ideas.extract import OpenAICompatLLM
    try:
        llm = OpenAICompatLLM()
    except Exception as e:
        print(f"無法草稿 Gslang：{e}（需要 GDECK_LLM_*）", file=sys.stderr)
        return 2
    work = CARDS / "_work"
    for c in iter_cards(CARDS):
        if c.status != "triaged":
            continue
        gsl = gb.draft_gsl(c, llm, a.root)
        r = gb.advance_card_to_fsm(c, gsl, work / c.id, a.root)
        if c.status == "fsm":
            res = tlc.run_tlc(Path(c.ir_path), Path(tempfile.mkdtemp()), a.root)
            c.verdict = res.verdict
            if res.verdict == "pass_with_bounds":
                (work / c.id / "strategy.py").write_text(gb.gen_python(r.ir, a.root), encoding="utf-8", newline="\n")
                set_status(c, "impl", "TLC 在示範參數的有限模型內未發現違反")
            else:
                c.reason = f"TLC verdict={res.verdict}；不進 impl"
        save_card(c, CARDS)
    return 0


def _cmd_batch(a) -> int:
    from quant_crawler.ideas.batch import register_batch, select_batch
    sel = select_batch(list(iter_cards(CARDS)))
    print(f"候選 {len(sel)} 個：{[c.id for c in sel]}")
    if not a.register:
        print("(dry-run；加 --register 才寫登記)")
        return 0
    if not sel:
        print("沒有候選，不登記")
        return 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    info = register_batch(sel, REPO / "experiments" / "idea_batches", now, REPO / "log" / "trials.jsonl",
                          PREREG_DIR, cards_dir=CARDS)
    print(f"已登記 {info['batch_id']}，n_trials={info['n_trials']}；請補 preregistration 的 "
          "author／hypothesis／mechanism／holding_periods 等欄位後再回測")
    return 0


def _cmd_backtest(a) -> int:
    if not os.environ.get("TEJAPI_KEY"):
        print("回測未實跑：沒有 TEJAPI_KEY（也需已 ingest 的 tquant_future bundle）。本 CLI 不產生任何回測結果。",
              file=sys.stderr)
        return 3
    print(f"TEJAPI_KEY 已設定，但本 CLI 不代跑回測。請手動執行：scripts/run_strategy.sh {a.id}；"
          "跑完把結果檔路徑交給 admit --bt-result")
    return 0


def _cmd_admit(a) -> int:
    path = CARDS / f"{a.id}.md"
    if not path.is_file():
        print(f"找不到卡片 {path}", file=sys.stderr)
        return 2
    card = load_card(path)
    why = admit_card(card, strategies_dir=STRATEGIES, prereg_dir=PREREG_DIR, gslang_src=GSLANG_SRC,
                     bt_result=Path(a.bt_result) if a.bt_result else None, work_dir=CARDS / "_work",
                     cards_dir=CARDS, admitted_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    if why:
        print("入庫被拒：", file=sys.stderr)
        for w in why:
            print(f"  - {w}", file=sys.stderr)
        return 1
    print(f"已入庫 {card.id}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quant_crawler.ideas")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--since", required=True)
    e.add_argument("--limit", type=int)
    e.add_argument("--source")
    e.set_defaults(fn=_cmd_extract)
    sub.add_parser("triage").set_defaults(fn=_cmd_triage)
    sub.add_parser("report").set_defaults(fn=_cmd_report)
    f = sub.add_parser("fsm")
    f.add_argument("--root")
    f.set_defaults(fn=_cmd_fsm)
    b = sub.add_parser("batch")
    b.add_argument("--register", action="store_true")
    b.set_defaults(fn=_cmd_batch)
    t = sub.add_parser("backtest")
    t.add_argument("id")
    t.set_defaults(fn=_cmd_backtest)
    d = sub.add_parser("admit")
    d.add_argument("id")
    d.add_argument("--bt-result")
    d.set_defaults(fn=_cmd_admit)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

"""idea 漏斗 CLI：python -m quant_crawler.ideas <extract|triage|fsm|batch|backtest|admit|report>。

誠實性：沒有 TEJAPI_KEY 時 backtest 明確回報「未實跑」；沒有 GDECK_LLM_* 時 extract／fsm 明確報錯；
本 CLI 不代跑 S5 回測。

結束碼：0 成功；1 作業失敗／入庫被拒；2 用法或環境錯誤；3 回測未實跑；4 batch --register 沒有候選。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from quant_crawler.ideas.card import _ID_RE, iter_cards, load_card, save_card, set_status
from quant_crawler.ideas.triage import apply_triage, normalize_signature

REPO = Path(__file__).resolve().parents[2]
CARDS = REPO / "ideas"
STRATEGIES = REPO / "strategies"
PREREG_DIR = REPO / "experiments" / "preregistration"
GSLANG_SRC = REPO / "strategies" / "_common" / "gslang"
CHECK_TIMEOUT = 120      # 驗證腳本逾時秒數；逾時視為未通過
EXIT_NO_CANDIDATE = 4


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
    """真的跑 repo 內的驗證腳本，回 (通過, 輸出)。逾時或無法執行一律視為未通過（fail-closed）。"""
    try:
        r = subprocess.run([sys.executable, str(REPO / "scripts" / script), str(target)],
                           capture_output=True, text=True, cwd=str(REPO), timeout=CHECK_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"{script} 逾時（>{CHECK_TIMEOUT}s），視為未通過"
    except OSError as e:
        return False, f"{script} 無法執行：{type(e).__name__}: {e}"
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _precheck(card, bt_result):
    """寫入任何東西之前就能判定的條件：卡片狀態、verdict、回測結果檔。

    gate_admission 的 batch_prereg_ok／bundle_ok 在此刻無法判定，故以具名參數傳 True，
    只為了讓這兩項不出現在預檢結果；真正的值之後由實際執行驗證腳本得出，再完整呼叫一次 gate_admission。
    """
    from quant_crawler.ideas.admit import gate_admission
    return gate_admission(card, batch_prereg_ok=True, bundle_ok=True, bt_result=bt_result)


def _index_has(strategies_dir: Path, cid: str) -> bool:
    import yaml
    p = strategies_dir / "INDEX.yaml"
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, yaml.YAMLError):
        return False
    entries = (doc.get("entries") or []) if isinstance(doc, dict) else []
    return any(isinstance(e, dict) and e.get("id") == cid for e in entries)


def admit_card(card, *, strategies_dir: Path, prereg_dir: Path, gslang_src: Path,
               bt_result: Path | None, work_dir: Path, cards_dir: Path | None = None,
               admitted_at: str = "") -> list[str]:
    """入庫。回傳未通過原因（空 list = 已入庫）。失敗時只移除「自己剛建立」的 bundle。

    batch_prereg_ok／bundle_ok 都由真的執行 check_preregistration／validate_dashboard_bundle 得出。
    順序：寫 bundle → 驗證 → update_index → 存卡。update_index 之後若存卡失敗，不回滾（索引已記載），
    改回報明確訊息請人手動把卡片設為 admitted；重跑時會因 bundle 與索引都已有該 id 而給出可辨識訊息。
    """
    from quant_crawler.ideas.admit import gate_admission, update_index, write_bundle

    pre = _precheck(card, bt_result)
    if pre:
        return pre
    if not isinstance(card.id, str) or not _ID_RE.match(card.id):
        return [f"不合法的 card.id：{card.id!r}"]
    try:
        ir = json.loads(Path(card.ir_path).read_text(encoding="utf-8"))
        spy = (work_dir / card.id / "strategy.py").read_text(encoding="utf-8")
    except (OSError, ValueError) as e:
        return [f"讀不到 IR 或 strategy.py：{type(e).__name__}: {e}"]

    target = strategies_dir / card.id
    if target.exists():       # 絕不覆蓋、也絕不刪除既有目錄
        if _index_has(strategies_dir, card.id):
            return [f"{card.id} 已入庫過（bundle 與索引都已有）；若卡片狀態不是 admitted，請手動設為 admitted"]
        return [f"{target} 已存在但不在索引；入庫不覆蓋，請人工處理"]

    prereg = prereg_dir / f"{card.batch_id}.yaml"
    if card.batch_id and prereg.is_file():
        prereg_ok, prereg_out = _run_check("check_preregistration.py", prereg)
    else:
        prereg_ok, prereg_out = False, f"找不到批次 preregistration：{prereg}"

    strategies_dir.mkdir(parents=True, exist_ok=True)
    try:
        bundle = write_bundle(card, ir, spy, strategies_dir, gslang_src)
    except (ValueError, OSError) as e:    # write_bundle 失敗時自己清暫存目錄；這裡不動任何既有目錄
        return [f"寫入 bundle 失敗：{type(e).__name__}: {e}"]
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
    except BaseException as e:
        shutil.rmtree(bundle, ignore_errors=True)          # bundle 是本函式剛建立的
        if isinstance(e, (ValueError, OSError)):
            return [f"入庫失敗並已回滾 bundle：{type(e).__name__}: {e}"]
        raise
    try:
        set_status(card, "admitted", "通過入庫閘門")
        if cards_dir is not None:
            save_card(card, cards_dir)
    except Exception as e:
        return [f"索引已更新、bundle 已入庫，但存卡失敗（{type(e).__name__}: {e}）："
                f"卡片需手動設為 admitted（{card.id}）"]
    return []


def _cmd_extract(a) -> int:
    try:
        date.fromisoformat(a.since)
    except ValueError:
        print(f"--since 必須是 YYYY-MM-DD：{a.since!r}", file=sys.stderr)
        return 2
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
    # 先依日期過濾，再套 limit（否則 limit 會被較舊的論文吃掉）
    recent = [r for r in st.latest(10 ** 9, a.source) if (r.published or "") >= a.since]
    for rec in recent[: (a.limit or 50)]:
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


class _FsmCardError(Exception):
    pass


def fsm_one(c, llm, root, work: Path, cards_dir: Path) -> None:
    """單卡 S3/S4。失敗時把階段與原因寫進卡片 reason 並存檔，再丟 _FsmCardError。"""
    from quant_crawler.ideas import gslang_bridge as gb
    from quant_crawler.ideas import tlc
    stage = "draft_gsl"
    try:
        gsl = gb.draft_gsl(c, llm, root)
        stage = "compile"
        r = gb.advance_card_to_fsm(c, gsl, work / c.id, root)
        if c.status == "fsm":
            stage = "tlc"
            with tempfile.TemporaryDirectory(prefix="idea_tlc_") as td:
                res = tlc.run_tlc(Path(c.ir_path), Path(td), root)
            c.verdict = res.verdict
            if res.verdict == "pass_with_bounds":
                stage = "codegen"
                (work / c.id / "strategy.py").write_text(gb.gen_python(r.ir, root), encoding="utf-8",
                                                         newline="\n")
                set_status(c, "impl", "TLC 在示範參數的有限模型內未發現違反")
            else:
                c.reason = f"TLC verdict={res.verdict}；不進 impl"
    except Exception as e:
        c.reason = f"fsm 失敗（階段 {stage}）：{type(e).__name__}: {e}"
        save_card(c, cards_dir)
        raise _FsmCardError(c.reason) from e
    save_card(c, cards_dir)


def _cmd_fsm(a) -> int:
    from quant_crawler.ideas import gslang_bridge as gb
    from quant_crawler.ideas.extract import OpenAICompatLLM
    try:
        gb._root(a.root)       # 先確認 Gslang 快照存在，避免逐卡才炸
    except gb.FormalUnavailable as e:
        print(f"無法執行 fsm：{e}（設定 GDECK_FORMAL 或 --root）", file=sys.stderr)
        return 2
    try:
        llm = OpenAICompatLLM()
    except Exception as e:
        print(f"無法草稿 Gslang：{e}（需要 GDECK_LLM_*）", file=sys.stderr)
        return 2
    work = CARDS / "_work"
    failed = 0
    for c in iter_cards(CARDS):
        if c.status != "triaged":
            continue
        try:
            fsm_one(c, llm, a.root, work, CARDS)
        except _FsmCardError as e:
            failed += 1
            print(f"[fsm 失敗] {c.id} — {e}", file=sys.stderr)
    return 1 if failed else 0


def _cmd_batch(a) -> int:
    from quant_crawler.ideas.batch import register_batch, select_batch
    sel = select_batch(list(iter_cards(CARDS)))
    print(f"候選 {len(sel)} 個：{[c.id for c in sel]}")
    if not a.register:
        print("(dry-run；加 --register 才寫登記)")
        return 0
    if not sel:
        print("沒有候選，不登記", file=sys.stderr)
        return EXIT_NO_CANDIDATE
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    info = register_batch(sel, REPO / "experiments" / "idea_batches", now, REPO / "log" / "trials.jsonl",
                          PREREG_DIR, cards_dir=CARDS)
    print(f"已登記 {info['batch_id']}，n_trials={info['n_trials']}；請補 preregistration 的 "
          "author／hypothesis／mechanism／holding_periods 等欄位後再回測")
    return 0


def _bad_id(cid) -> bool:
    if not isinstance(cid, str) or not _ID_RE.match(cid):
        print(f"不合法的卡片 id：{cid!r}", file=sys.stderr)
        return True
    return False


def _cmd_backtest(a) -> int:
    if _bad_id(a.id):
        return 2
    if not os.environ.get("TEJAPI_KEY"):
        print("回測未實跑：沒有 TEJAPI_KEY（也需已 ingest 的 tquant_future bundle）。本 CLI 不產生任何回測結果。",
              file=sys.stderr)
        return 3
    print(f"TEJAPI_KEY 已設定，但本 CLI 不代跑回測。請手動執行：scripts/run_strategy.sh {a.id}；"
          "跑完把結果檔路徑交給 admit --bt-result")
    return 0


def _cmd_admit(a) -> int:
    if _bad_id(a.id):
        return 2
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

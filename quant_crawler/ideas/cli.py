"""idea 漏斗 CLI：python -m quant_crawler.ideas <extract|triage|fsm|batch|backtest|admit|report>。

誠實性：沒有 TEJAPI_KEY 時 backtest 明確回報「未實跑」；沒有 GDECK_LLM_* 時 extract／fsm 明確報錯；
本 CLI 不代跑 S5 回測。

結束碼：0 成功；1 作業失敗／入庫被拒；2 用法或環境錯誤；3 回測未實跑；4 batch --register 沒有候選。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from quant_crawler.ideas.card import (_ID_RE, REPO, from_repo, iter_cards, load_card, rel_to_repo, safe_err,
                                      save_card, set_status)
from quant_crawler.ideas.triage import apply_triage, normalize_signature

CARDS = REPO / "ideas"
STRATEGIES = REPO / "strategies"
PREREG_DIR = REPO / "experiments" / "preregistration"
BATCH_DIR = REPO / "experiments" / "idea_batches"
LEDGER = REPO / "log" / "trials.jsonl"
GSLANG_SRC = REPO / "strategies" / "_common" / "gslang"
CHECK_TIMEOUT = 120      # 驗證腳本逾時秒數；逾時視為未通過
EXIT_NO_CANDIDATE = 4
_BATCH_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


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


def check_result_file(bt_result, cid: str | None = None) -> list[str]:
    """回測結果檔檢查：非空，且旁附 `<檔名>.sha256`（sha256sum 格式）與內容相符。

    限制：結果是 runner 輸出的 pandas pickle；不跑回測就無法驗證其結構，也不應對不受信的 pickle
    反序列化，所以這裡只做「存在、非空、內容雜湊與旁附檔一致」，防的是誤放／截斷的檔案，不是偽造。
    """
    if bt_result is None:
        return ["沒有回測結果檔（需存在且非空）"]
    p = Path(bt_result)
    if cid is not None and (p.name != f"{cid}.pkl" or p.parent.name != "results"):
        return [f"回測結果檔必須是 results/{cid}.pkl（收到 {p.parent.name}/{p.name}）"]
    side = p.with_name(p.name + ".sha256")
    try:
        if not p.is_file() or p.stat().st_size == 0:
            return ["沒有回測結果檔（需存在且非空）"]
        if not side.is_file():
            return [f"回測結果缺旁附雜湊檔 {side.name}（sha256sum {p.name} > {side.name}）"]
        want = side.read_text(encoding="utf-8").split()[0].lower()
        got = hashlib.sha256(p.read_bytes()).hexdigest()
    except (OSError, IndexError):
        return ["回測結果或其雜湊檔無法讀取"]
    if want != got:
        return ["回測結果內容與旁附 sha256 不符"]
    return []


def _ledger_config_sha(ledger: Path, cid: str) -> str | None:
    """ledger 內 t-idea-<id> 最後一行的 config_sha256（登記時的 staging bundle 雜湊）。"""
    sha = None
    if not ledger.exists():
        return None
    for line in ledger.read_bytes().decode("utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("trial_id") == f"t-idea-{cid}":
            sha = rec.get("config_sha256") or sha
    return sha


def _ledger_n_trials(ledger: Path, cid: str) -> int | None:
    """ledger 內 t-idea-<id> 登記時宣告的 n_trials（staging config 的 validation.n_trials 同源）。"""
    n = None
    if ledger.exists():
        for line in ledger.read_bytes().decode("utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("trial_id") == f"t-idea-{cid}":
                try:
                    n = int(rec["stat_decision"]["decision_path"]["n_trials"])
                except (KeyError, TypeError, ValueError):
                    pass
    return n


def _prereg_sample(prereg_dir: Path, batch_id: str) -> tuple[str, str]:
    """從批次 prereg 取 sample.start／end（回測起訖唯一來源）；缺或格式錯即 ValueError。"""
    if not batch_id or not _BATCH_ID_RE.match(batch_id):
        raise ValueError(f"沒有可用的批次 id：{batch_id!r}")
    f = prereg_dir / f"{batch_id}.yaml"
    try:
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"讀不到批次 preregistration {f}：{type(e).__name__}") from e
    smp = doc.get("sample") or {}
    start, end = str(smp.get("start") or ""), str(smp.get("end") or "")
    _check_range(start, end)
    return start, end


def _check_range(start: str, end: str) -> None:
    try:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise ValueError(f"回測起訖必須是 YYYY-MM-DD：start={start!r} end={end!r}") from None
    if e <= s:
        raise ValueError(f"回測結束日必須晚於起始日：{start}..{end}")


def stage_card(card, *, work_dir: Path, gslang_src: Path, start: str, end: str,
               n_trials: int | None = None) -> tuple[Path, str]:
    """把 impl 卡寫成 staging bundle：<work_dir>/<id>/bundle（含 vendored gslang、config、manifest）。"""
    from quant_crawler.ideas.admit import stage_bundle
    ir = json.loads(from_repo(card.ir_path).read_text(encoding="utf-8"))
    spy = (work_dir / card.id / "strategy.py").read_text(encoding="utf-8")
    return stage_bundle(card, ir, spy, work_dir / card.id / "bundle", gslang_src, start, end, n_trials)


def admit_card(card, *, strategies_dir: Path, prereg_dir: Path, bt_result: Path | None, work_dir: Path,
               ledger: Path, cards_dir: Path | None = None, admitted_at: str = "") -> list[str]:
    """入庫。回傳未通過原因（空 list = 已入庫）。失敗時只移除「自己剛建立」的 bundle。

    內容唯一來源是登記時的 staging bundle：複製到 strategies/<id>，並比對 ledger 內登記的 config_sha256。
    batch_prereg_ok／bundle_ok 都由真的執行 check_preregistration／validate_dashboard_bundle 得出。
    順序：複製 bundle → 驗證 → update_index → 存卡。update_index 之後若存卡失敗，不回滾（索引已記載），
    改回報明確訊息請人手動把卡片設為 admitted；重跑時會因 bundle 與索引都已有該 id 而給出可辨識訊息。
    """
    from quant_crawler.ideas.admit import bundle_sha256, copy_staged_bundle, gate_admission, update_index

    if not isinstance(card.id, str) or not _ID_RE.match(card.id):
        return [f"不合法的 card.id：{card.id!r}"]
    pre = _precheck(card, bt_result)
    if not pre:
        pre = check_result_file(bt_result, card.id)     # gate 只看非空；這裡再驗檔名與旁附雜湊
    if pre:
        return pre

    target = strategies_dir / card.id
    if target.exists():       # 絕不覆蓋、也絕不刪除既有目錄
        if _index_has(strategies_dir, card.id):
            return [f"{card.id} 已入庫過（bundle 與索引都已有）；若卡片狀態不是 admitted，請手動設為 admitted"]
        return [f"{target} 已存在但不在索引；入庫不覆蓋，請人工處理"]

    staged = work_dir / card.id / "bundle"
    want = _ledger_config_sha(ledger, card.id)
    if not want:
        return [f"ledger 內沒有 {card.id} 登記的 config_sha256；無法確認 bundle 內容（請走 batch --register）"]
    if not staged.is_dir():
        return [f"找不到 staging bundle：{staged}（backtest {card.id} --stage 重建後，內容須與登記雜湊一致）"]
    got = bundle_sha256(staged)
    if got != want:
        return [f"staging bundle 內容雜湊 {got[:12]} 與 ledger 登記的 {want[:12]} 不符；拒絕入庫"]

    prereg = prereg_dir / f"{card.batch_id}.yaml"
    if card.batch_id and prereg.is_file():
        prereg_ok, prereg_out = _run_check("check_preregistration.py", prereg)
    else:
        prereg_ok, prereg_out = False, f"找不到批次 preregistration：{prereg}"
    extra = []
    try:
        cfg = yaml.safe_load((staged / "config.yaml").read_text(encoding="utf-8")) or {}
        if prereg_ok and (str(cfg.get("start")), str(cfg.get("end"))) != _prereg_sample(prereg_dir, card.batch_id):
            extra.append("bundle 的回測起訖與 preregistration 的 sample.start／end 不一致")
    except (OSError, ValueError, yaml.YAMLError) as e:
        extra.append(f"無法核對回測起訖：{type(e).__name__}")

    strategies_dir.mkdir(parents=True, exist_ok=True)
    try:
        bundle = copy_staged_bundle(staged, strategies_dir, card.id, want)
    except (ValueError, OSError) as e:    # 失敗時 copy_staged_bundle 自己清暫存目錄；不動任何既有目錄
        return [f"複製 bundle 失敗：{type(e).__name__}: {e}"]
    try:
        bundle_ok, bundle_out = _run_check("validate_dashboard_bundle.py", bundle)
        why = gate_admission(card, prereg_ok, bundle_ok, bt_result) + extra
        if why:
            if not prereg_ok:
                why.append(prereg_out)
            if not bundle_ok:
                why.append(bundle_out)
            shutil.rmtree(bundle, ignore_errors=True)
            return why
        update_index(strategies_dir, {"id": card.id, "batch_id": card.batch_id, "verdict": card.verdict,
                                      "admitted_at": admitted_at, "source": card.source,
                                      "result_sha256": hashlib.sha256(Path(bt_result).read_bytes()).hexdigest()})
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


def _index_has(strategies_dir: Path, cid: str) -> bool:
    p = strategies_dir / "INDEX.yaml"
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, yaml.YAMLError):
        return False
    entries = (doc.get("entries") or []) if isinstance(doc, dict) else []
    return any(isinstance(e, dict) and e.get("id") == cid for e in entries)


def _cmd_extract(a) -> int:
    try:
        date.fromisoformat(a.since)
    except ValueError:
        print(f"--since 必須是 YYYY-MM-DD：{a.since!r}", file=sys.stderr)
        return 2
    from quant_crawler.config import DB_PATH
    from quant_crawler.ideas.extract import OpenAICompatLLM, card_id_for, extract_card
    from quant_crawler.storage.db import Storage
    try:
        llm = OpenAICompatLLM()
    except Exception as e:  # 缺 GDECK_LLM_* 時明確報錯，不假裝成功
        print(f"無法抽取：{e}（需要環境變數 GDECK_LLM_URL／GDECK_LLM_KEY／GDECK_LLM_MODEL）", file=sys.stderr)
        return 2
    st = Storage(DB_PATH)
    made = failed = 0
    # 先依日期過濾，再套 limit（否則 limit 會被較舊的論文吃掉）
    recent = [r for r in st.latest(10 ** 9, a.source) if (r.published or "") >= a.since]
    for rec in recent[: (a.limit or 50)]:
        try:
            cid = card_id_for(rec)
            if (CARDS / f"{cid}.md").exists():      # 已有卡就跳過，不再呼叫 LLM
                continue
            save_card(extract_card(rec, llm), CARDS)
            made += 1
        except Exception as e:                      # 單筆失敗不中斷；只記型別與狀態碼，不含 URL／金鑰
            failed += 1
            print(f"[extract 失敗] {getattr(rec, 'source', '?')}:{getattr(rec, 'source_id', '?')} — "
                  f"{safe_err(e)}", file=sys.stderr)
    print(f"新增 {made} 張卡、失敗 {failed} 筆 → {CARDS}")
    return 1 if failed else 0


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
    """單卡 S3/S4。失敗時把階段與例外型別（不含訊息）寫進卡片 reason 並存檔，再丟 _FsmCardError。"""
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
                res = tlc.run_tlc(from_repo(c.ir_path), Path(td), root)
            c.verdict = res.verdict
            if res.verdict == "pass_with_bounds":
                stage = "codegen"
                (work / c.id / "strategy.py").write_text(gb.gen_python(r.ir, root), encoding="utf-8",
                                                         newline="\n")
                set_status(c, "impl", "TLC 在示範參數的有限模型內未發現違反")
            else:
                c.reason = f"TLC verdict={res.verdict}；不進 impl"
    except Exception as e:
        c.reason = f"fsm 失敗（階段 {stage}）：{safe_err(e)}"
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
    from quant_crawler.ideas.batch import _selection_count, ledger_idea_card_ids, register_batch, select_batch
    if a.batch_id is not None:
        from quant_crawler.ideas.batch import check_batch_id
        try:
            check_batch_id(a.batch_id)
        except ValueError as e:
            print(f"--batch-id 不合法：{e}", file=sys.stderr)
            return 2
    cards = list(iter_cards(CARDS))
    in_ledger = ledger_idea_card_ids(LEDGER)
    for c in cards:
        if c.id in in_ledger and c.status == "impl":
            print(f"警告：{c.id} 已在 ledger，卡片需手動設為 bt_registered（本次不再選入）", file=sys.stderr)
    sel = select_batch(cards, exclude_ids=in_ledger)
    print(f"候選 {len(sel)} 個：{[c.id for c in sel]}")
    if not a.register:
        print("(dry-run；加 --register --start YYYY-MM-DD --end YYYY-MM-DD 才寫登記)")
        return 0
    if not sel:
        print("沒有候選，不登記", file=sys.stderr)
        return EXIT_NO_CANDIDATE
    try:
        _check_range(a.start or "", a.end or "")      # 回測起訖由人先押定（登記前）
    except ValueError as e:
        print(f"--register 需要 --start／--end：{e}", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        shas = {}
        n0 = _selection_count(LEDGER)       # 登記當下的 N；與 ledger 內該卡 decision_path.n_trials 同口徑
        for i, c in enumerate(sel):
            _, shas[c.id] = stage_card(c, work_dir=CARDS / "_work", gslang_src=GSLANG_SRC,
                                       start=a.start, end=a.end, n_trials=n0 + i + 1)
        info = register_batch(sel, BATCH_DIR, now, LEDGER, PREREG_DIR, batch_id=a.batch_id, cards_dir=CARDS,
                              config_shas=shas, sample=(a.start, a.end))
    except (ValueError, FileExistsError, OSError) as e:
        print(f"登記失敗：{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"已登記 {info['batch_id']}，n_trials={info['n_trials']}；staging bundle 在 ideas/_work/<id>/bundle。"
          "請補 preregistration 的 author／hypothesis／mechanism／holding_periods 等欄位後再回測")
    return 0


def _bad_id(cid) -> bool:
    if not isinstance(cid, str) or not _ID_RE.match(cid):
        print(f"不合法的卡片 id：{cid!r}", file=sys.stderr)
        return True
    return False


def backtest_command(cid: str) -> str:
    rel = f"ideas/_work/{cid}/bundle"
    return (f".venv-bt/bin/python strategies/_common/runner.py --strategy {rel}/strategy.py "
            f"--config {rel}/config.yaml --output results/{cid}.pkl "
            f"&& sha256sum results/{cid}.pkl > results/{cid}.pkl.sha256")


def _cmd_backtest(a) -> int:
    if _bad_id(a.id):
        return 2
    if a.stage:
        path = CARDS / f"{a.id}.md"
        if not path.is_file():
            print(f"找不到卡片 {path}", file=sys.stderr)
            return 2
        card = load_card(path)
        try:
            start, end = _prereg_sample(PREREG_DIR, card.batch_id)
            n = _ledger_n_trials(LEDGER, card.id)
            bundle, sha = stage_card(card, work_dir=CARDS / "_work", gslang_src=GSLANG_SRC, start=start, end=end,
                                     n_trials=n)
        except (ValueError, OSError) as e:
            print(f"staging 失敗：{type(e).__name__}: {e}", file=sys.stderr)
            return 1
        want = _ledger_config_sha(LEDGER, card.id)
        note = "" if want == sha else f"（警告：與 ledger 登記的 {str(want)[:12]} 不同，入庫會被拒絕）"
        print(f"已重建 staging bundle：{rel_to_repo(bundle)} sha256={sha[:12]}{note}")
        return 0
    if not os.environ.get("TEJAPI_KEY"):
        print("回測未實跑：沒有 TEJAPI_KEY（也需已 ingest 的 tquant_future bundle）。本 CLI 不產生任何回測結果。",
              file=sys.stderr)
        return 3
    print("TEJAPI_KEY 已設定，但本 CLI 不代跑回測。請在 repo 根目錄手動執行：")
    print(backtest_command(a.id))
    print("跑完後把 results/<id>.pkl 交給 admit --bt-result（旁附 .sha256 一併需要）")
    return 0


def _cmd_admit(a) -> int:
    if _bad_id(a.id):
        return 2
    path = CARDS / f"{a.id}.md"
    if not path.is_file():
        print(f"找不到卡片 {path}", file=sys.stderr)
        return 2
    card = load_card(path)
    why = admit_card(card, strategies_dir=STRATEGIES, prereg_dir=PREREG_DIR,
                     bt_result=Path(a.bt_result) if a.bt_result else None, work_dir=CARDS / "_work",
                     ledger=LEDGER, cards_dir=CARDS,
                     admitted_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
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
    b.add_argument("--batch-id")
    b.add_argument("--start", help="回測起日 YYYY-MM-DD（--register 必填；寫入 prereg sample 與 staging config）")
    b.add_argument("--end", help="回測迄日 YYYY-MM-DD")
    b.set_defaults(fn=_cmd_batch)
    t = sub.add_parser("backtest")
    t.add_argument("id")
    t.add_argument("--stage", action="store_true", help="依批次 prereg 的 sample 重建 staging bundle（不需 TEJAPI_KEY）")
    t.set_defaults(fn=_cmd_backtest)
    d = sub.add_parser("admit")
    d.add_argument("id")
    d.add_argument("--bt-result")
    d.set_defaults(fn=_cmd_admit)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

# tests/test_idea_funnel_review.py
"""全分支最終審查修正波：ledger 審計、id 規則、權限、重複登記、鎖、機密、路徑。"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from quant_crawler.ideas import admit, cli, tlc
from quant_crawler.ideas.batch import ledger_idea_card_ids, register_batch, select_batch
from quant_crawler.ideas.card import (_ID_RE, REPO, IdeaCard, from_repo, load_card, rel_to_repo, safe_err,
                                      save_card, set_status, slug)

GSL_SRC = REPO / "strategies" / "_common" / "gslang"
NOW = "2026-11-12T00:00:00Z"


def _impl(cid):
    c = IdeaCard(id=cid, source="s", title="t", hypothesis="站上均線做多", market="TX futures")
    for s in ("triaged", "fsm", "impl"):
        set_status(c, s)
    c.verdict = "pass_with_bounds"
    return c


# ---- I1：登記行符合 pending 慣例，並與 repo 的 audit_ledger 相容（僅缺 n_eff 這一類已知差異）----
def test_registered_trials_follow_pending_convention_and_audit(tmp_path):
    from strategies._common.validation.decision import audit_ledger
    real = REPO / "log" / "trials.jsonl"
    ledger = tmp_path / "trials.jsonl"
    ledger.write_bytes(real.read_bytes())
    base_recs = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    base = audit_ledger(base_recs)
    assert base["problems"] == []
    cards = [_impl("i1_a"), _impl("i1_b")]
    register_batch(cards, tmp_path / "b", NOW, ledger, tmp_path / "p")
    recs = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    trials = [r for r in recs if r["stat_decision"].get("status") == "pending"]
    assert len(trials) == 2
    for t in trials:
        sd = t["stat_decision"]
        assert t["purpose"] == "selection"
        assert sd["status"] == "pending" and sd["rule_id"] is None and sd["inference_available"] is False
        assert sd["preregistration_id"] == "idea-batch-2026-11-12"
        dp = sd["decision_path"]
        assert set(dp) == {"estimand", "design", "overlap", "family", "purpose", "n_trials"}
        assert "n_eff" not in dp                                     # 不捏造
    assert [t["stat_decision"]["decision_path"]["n_trials"] for t in trials] == [
        base["n_selection_recomputed"] + 1, base["n_selection_recomputed"] + 2]
    rep = audit_ledger(recs)
    assert rep["n_selection_recomputed"] == base["n_selection_recomputed"] + 2     # N 已計入
    # 既有 719 行與批次根紀錄不受影響；新增的問題只有「每個 pending trial 缺 n_eff」（等回測補完成行）
    assert len(rep["problems"]) == 2
    assert all("n_eff" in p and ("t-idea-i1_a" in p or "t-idea-i1_b" in p) for p in rep["problems"])


# ---- I2：id 規則 ----
def test_slug_long_and_non_ascii_ids_are_valid_and_distinct():
    long1 = slug("github_" + "a" * 120 + "/repo1")
    long2 = slug("github_" + "a" * 120 + "/repo2")
    assert len(long1) <= 64 and _ID_RE.match(long1) and long1 != long2
    assert slug("github_" + "a" * 120 + "/repo1") == long1               # 決定性
    h = slug("動能策略")
    assert _ID_RE.match(h) and h.startswith("x_") and slug("反轉策略") != h
    IdeaCard(id=long1, source="s", title="t")                            # 不丟例外


def test_id_regex_is_64_chars():
    assert _ID_RE.match("a" * 64) and not _ID_RE.match("a" * 65)


def test_extract_loop_isolated_skips_existing_and_hides_secrets(tmp_path, monkeypatch, capsys):
    import quant_crawler.config as config
    import quant_crawler.ideas.extract as ex
    import quant_crawler.storage.db as dbmod
    from quant_crawler.storage.models import PaperRecord

    recs = [PaperRecord(source="arxiv", source_id=f"2601.{i:05d}", title="t", published=d)
            for i, d in enumerate(["2026-06-01", "2020-01-01", "2026-05-01", "2026-04-01", "2026-03-01"])]

    class FakeStorage:
        def __init__(self, path):
            pass

        def latest(self, limit, source=None):
            return list(recs)
    monkeypatch.setattr(dbmod, "Storage", FakeStorage)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "x.db")
    monkeypatch.setattr(ex, "OpenAICompatLLM", lambda: object())
    cards = tmp_path / "ideas"
    monkeypatch.setattr(cli, "CARDS", cards)
    save_card(IdeaCard(id=ex.card_id_for(recs[0]), source="x", title="old"), cards)    # 已存在 → 不得呼叫 LLM
    calls = []

    class Boom(Exception):
        response = SimpleNamespace(status_code=502)

    def fake_extract(rec, llm):
        calls.append(rec.source_id)
        if rec.source_id == recs[2].source_id:
            raise Boom("https://llm.example/v1?key=SECRET123")
        return IdeaCard(id=ex.card_id_for(rec), source="s", title="t")
    monkeypatch.setattr(ex, "extract_card", fake_extract)
    # since 先過濾（2020 那筆被排除）再套 limit=3：剩下 recs[0](已存在)、recs[2](失敗)、recs[3]
    rc = cli.main(["extract", "--since", "2026-01-01", "--limit", "3"])
    err = capsys.readouterr().err
    assert rc == 1
    assert calls == [recs[2].source_id, recs[3].source_id]               # 已存在者沒呼叫；失敗後仍繼續
    assert "status=502" in err and "SECRET123" not in err and "llm.example" not in err
    assert (cards / f"{ex.card_id_for(recs[3])}.md").is_file()


def test_safe_err_has_type_and_status_only():
    class E(Exception):
        response = SimpleNamespace(status_code=401)
    assert safe_err(E("Bearer abc")) == "E status=401"
    assert safe_err(ValueError("secret")) == "ValueError"


# ---- I3：權限 ----
@pytest.mark.skipif(os.name == "nt", reason="POSIX 權限")
def test_bundle_and_index_permissions_follow_umask(tmp_path):
    old = os.umask(0o022)
    try:
        c = _impl("perm_x")
        d = admit.write_bundle(c, {"id": "perm_x", "instruments": [{"id": "TX"}]}, "x", tmp_path, GSL_SRC,
                               "2020-01-01", "2026-04-30")
        assert (d.stat().st_mode & 0o777) == 0o755
        assert ((d / "gslang").stat().st_mode & 0o777) == 0o755
        assert ((d / "strategy.py").stat().st_mode & 0o777) == 0o644
        admit.update_index(tmp_path, {"id": "perm_x"})
        idx = tmp_path / "INDEX.yaml"
        assert (idx.stat().st_mode & 0o777) == 0o644
        os.chmod(idx, 0o664)
        admit.update_index(tmp_path, {"id": "perm_y"})
        assert (idx.stat().st_mode & 0o777) == 0o664                   # 保留既有 mode
        f = tmp_path / "script.sh"
        f.write_text("x", encoding="utf-8")
        os.chmod(f, 0o700)
        admit._normalize_perms(tmp_path)
        assert (f.stat().st_mode & 0o777) == 0o755                      # 可執行位保留
    finally:
        os.umask(old)


# ---- I4：不重複選入 ledger 內已登記者 ----
def test_select_batch_exclude_ids():
    cards = [_impl("s_a"), _impl("s_b")]
    assert [c.id for c in select_batch(cards, exclude_ids={"s_a"})] == ["s_b"]


def test_cmd_batch_excludes_ledger_cards_warns_and_maps_errors(tmp_path, monkeypatch, capsys):
    cards_dir, ledger = tmp_path / "ideas", tmp_path / "log" / "trials.jsonl"
    ledger.parent.mkdir()
    ledger.write_text(json.dumps({"trial_id": "t-idea-dup_a"}) + "\n", encoding="utf-8")
    save_card(_impl("dup_a"), cards_dir)
    save_card(_impl("fresh_b"), cards_dir)
    monkeypatch.setattr(cli, "CARDS", cards_dir)
    monkeypatch.setattr(cli, "LEDGER", ledger)
    assert ledger_idea_card_ids(ledger) == {"dup_a"}
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"trial_id": "t-idea-batch-2026-11-12"}) + chr(10))
    assert ledger_idea_card_ids(ledger) == {"dup_a"}          # 預設批次根紀錄不算卡片
    assert cli.main(["batch"]) == 0
    cap = capsys.readouterr()
    assert "['fresh_b']" in cap.out and "dup_a" in cap.err and "bt_registered" in cap.err
    monkeypatch.setattr(cli, "stage_card", lambda *a, **k: (tmp_path, "0" * 64))
    import quant_crawler.ideas.batch as batch_mod

    def boom(*a, **k):
        raise FileExistsError("已登記")
    monkeypatch.setattr(batch_mod, "register_batch", boom)
    assert cli.main(["batch", "--register", "--start", "2020-01-01", "--end", "2026-04-30"]) == 1
    assert "登記失敗" in capsys.readouterr().err
    assert cli.main(["batch", "--register"]) == 2                      # 缺 --start／--end


# ---- I5：ledger 鎖 ----
@pytest.mark.skipif(os.name == "nt", reason="fcntl")
def test_register_waits_for_ledger_lock_file(tmp_path):
    import fcntl
    ledger = tmp_path / "log" / "trials.jsonl"
    ledger.parent.mkdir()
    lock = ledger.with_suffix(ledger.suffix + ".lock")            # = trials.jsonl.lock，與 graph/ledger.py 同路徑
    done = []

    def run():
        register_batch([_impl("lk_a")], tmp_path / "b", NOW, ledger, tmp_path / "p")
        done.append(1)
    with lock.open("a+b") as h:
        fcntl.flock(h.fileno(), fcntl.LOCK_EX)
        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.4)
        assert not done and not ledger.exists()                    # 持鎖期間不得寫入
        fcntl.flock(h.fileno(), fcntl.LOCK_UN)
    t.join(10)
    assert done and ledger.exists()
    assert lock.name == "trials.jsonl.lock"


# ---- I6：--batch-id ----
def test_register_batch_validates_batch_id(tmp_path):
    for bad in ("../x", "A", "", "a/b", "x" * 65, "-a"):
        with pytest.raises(ValueError):
            register_batch([_impl("bi_a")], tmp_path / "b", NOW, tmp_path / "l.jsonl", tmp_path / "p",
                           batch_id=bad)
    assert not (tmp_path / "l.jsonl").exists()


def test_cli_batch_id_validated_and_used(tmp_path, monkeypatch, capsys):
    cards_dir = tmp_path / "ideas"
    save_card(_impl("bi_a"), cards_dir)
    monkeypatch.setattr(cli, "CARDS", cards_dir)
    monkeypatch.setattr(cli, "LEDGER", tmp_path / "log" / "trials.jsonl")
    assert cli.main(["batch", "--register", "--batch-id", "../evil", "--start", "2020-01-01",
                     "--end", "2026-04-30"]) == 2
    assert "--batch-id" in capsys.readouterr().err


# ---- M3：路徑相對 REPO ----
def test_repo_relative_paths_roundtrip():
    p = REPO / "ideas" / "_work" / "x" / "x.ir.json"
    assert rel_to_repo(p) == "ideas/_work/x/x.ir.json"
    assert from_repo("ideas/_work/x/x.ir.json") == p
    assert rel_to_repo("/somewhere/else") == "/somewhere/else"
    assert from_repo("/somewhere/else") == Path("/somewhere/else")


def test_staged_bundle_has_no_absolute_paths(tmp_path):
    from quant_crawler.ideas.admit import bundle_sha256, stage_bundle
    c = _impl("rel_x")
    c.gsl_path = "ideas/_work/rel_x/rel_x.gsl"
    d, sha = stage_bundle(c, {"id": "rel_x", "instruments": [{"id": "TX"}]}, "x", tmp_path / "bundle",
                          GSL_SRC, "2020-01-01", "2026-04-30")
    src = (d / "gslang" / "SOURCE.txt").read_text(encoding="utf-8")
    assert "source: strategies/_common/gslang" in src and "/home" not in src
    assert sha == bundle_sha256(d) == stage_bundle(c, {"id": "rel_x", "instruments": [{"id": "TX"}]}, "x",
                                                   tmp_path / "bundle", GSL_SRC, "2020-01-01", "2026-04-30")[1]


# ---- M4：TLC 子行程只拿必要環境變數 ----
def test_tlc_subprocess_env_is_whitelisted(tmp_path, monkeypatch):
    monkeypatch.setenv("GDECK_LLM_KEY", "sekrit")
    monkeypatch.setenv("TEJAPI_KEY", "sekrit2")
    monkeypatch.setenv("GITHUB_TOKEN", "sekrit3")
    monkeypatch.setenv("GDECK_FORMAL", "/x")
    monkeypatch.setattr(tlc, "_root", lambda root=None: tmp_path)
    ir = tmp_path / "a.ir.json"
    ir.write_text(json.dumps({"id": "a", "costs": {}, "stress": []}), encoding="utf-8")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(tlc.subprocess, "run", fake_run)
    res = tlc.run_tlc(ir, tmp_path / "w")
    assert res.verdict == "inconclusive"
    assert "PATH" in seen and seen.get("GDECK_FORMAL") == "/x"
    assert not ({"GDECK_LLM_KEY", "TEJAPI_KEY", "GITHUB_TOKEN"} & set(seen))
    assert not hasattr(res, "demo_params")


def test_triage_dir_wrapper_removed():
    assert not hasattr(cli, "triage_dir")

"""用暫時的 bare repo 驗 pre_receive.py 的拒絕／放行（不需 Forgejo）。
執行：pytest deploy/gsinvest-forgejo/hooks/test_pre_receive.py
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HOOK = Path(__file__).with_name("pre_receive.py")
REPO = Path(__file__).resolve().parents[3]
ZERO = "0" * 40
IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, env={**os.environ, **IDENT}, capture_output=True,
                          text=True, encoding="utf-8", check=True)


def commit(work, rel, text, msg):
    p = work / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    sh(work, "git", "add", "-A")
    sh(work, "git", "commit", "-q", "-m", msg)
    return sh(work, "git", "rev-parse", "HEAD").stdout.strip()


def run_hook(bare, old, new, ref="refs/heads/main", pusher="a@x", roles=None):
    return subprocess.run([sys.executable, str(HOOK)], cwd=bare, input=f"{old} {new} {ref}\n",
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "GITEA_PUSHER_EMAIL": pusher, "GS_HOOKS_REPO": str(REPO),
                               "GS_ROLES_FILE": str(roles) if roles else "/nonexistent/roles.yaml"})


@pytest.fixture
def repo(tmp_path):
    work, bare = tmp_path / "w", tmp_path / "b.git"
    work.mkdir()
    sh(work, "git", "init", "-q", "-b", "main")
    c0 = commit(work, "README.md", "x", "init")
    sh(tmp_path, "git", "clone", "-q", "--bare", str(work), str(bare))
    return work, bare, c0


def fetch(bare, work, ref="HEAD"):
    sh(bare, "git", "fetch", "-q", str(work), ref)


def test_delete_main_rejected(repo):
    _, bare, c0 = repo
    assert run_hook(bare, c0, ZERO).returncode == 1


def test_force_push_rejected(repo):
    work, bare, c0 = repo
    c1 = commit(work, "a.txt", "1", "a")
    fetch(bare, work)
    sh(work, "git", "checkout", "-q", "-b", "alt", c0)
    c2 = commit(work, "b.txt", "2", "b")
    fetch(bare, work)
    assert run_hook(bare, c1, c2).returncode == 1


def test_editing_existing_prereg_rejected(repo):
    work, bare, _ = repo
    c1 = commit(work, "experiments/preregistration/001-x.yaml", "id: a\n", "reg")
    c2 = commit(work, "experiments/preregistration/001-x.yaml", "id: b\n", "edit")
    fetch(bare, work)
    assert run_hook(bare, c1, c2).returncode == 1


def test_author_mismatch_rejected(repo):
    work, bare, c0 = repo
    c1 = commit(work, "experiments/preregistration/002-y.yaml",
                "schema: preregistration/1\nid: 002-y\nauthor: someone-else@x\n", "reg")
    fetch(bare, work)
    r = run_hook(bare, c0, c1, pusher="a@x")
    assert r.returncode == 1 and "author" in r.stderr


def test_plain_commit_passes(repo):
    work, bare, c0 = repo
    c1 = commit(work, "docs/x.md", "hi", "doc")
    fetch(bare, work)
    assert run_hook(bare, c0, c1).returncode == 0


def test_non_main_ref_passes(repo):
    _, bare, c0 = repo
    assert run_hook(bare, ZERO, c0, ref="refs/heads/dev/x").returncode == 0


# ---------------------------------------------------------------------------
# 規則 4 與簽核／結案（spec §5.2、§5.3）
# ---------------------------------------------------------------------------
D = "experiments/preregistration/"


def reg(author, rid, **over):
    doc = {
        "schema": "preregistration/1", "id": rid, "title": "t", "author": author,
        "registered_at": "2026-09-01T00:00:00Z", "hypothesis": "TX 的 5 日動能延續",
        "economic_mechanism": "散戶追漲殺跌造成短期反應不足", "falsification": ["IC 不顯著"],
        "sample": {"contains_full_bear_market": True},
        "stat_plan": {"estimand": "rank_ic", "design": "one_sample", "overlap": "overlapping",
                      "holding_periods": 5, "family": "bounded_multiple", "n_trials": 9,
                      "purpose": "selection"},
        "thresholds": {"planned_trials": 9},
    }
    doc.update(over)
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


@pytest.fixture
def roles(tmp_path):
    p = tmp_path / "roles.yaml"
    p.write_text("signers: [s@x, k@x]\n", encoding="utf-8")
    return p


def push(repo, files, pusher, roles=None, msg="c"):
    work, bare, _ = repo
    old = sh(work, "git", "rev-parse", "HEAD").stdout.strip()
    new = old
    for rel, text in files.items():
        new = commit(work, D + rel, text, msg)
    fetch(bare, work)
    return run_hook(bare, old, new, pusher=pusher, roles=roles)


def test_valid_registration_is_accepted(repo):
    assert push(repo, {"001-x.yaml": reg("a@x", "001-x")}, "a@x").returncode == 0


def test_planned_trials_over_10_rejected_unless_open_mining(repo):
    r = push(repo, {"001-x.yaml": reg("a@x", "001-x", thresholds={"planned_trials": 11})}, "a@x")
    assert r.returncode == 1 and "planned_trials" in r.stderr


def test_fourth_active_registration_rejected(repo):
    for i in (1, 2, 3):
        assert push(repo, {f"00{i}-x.yaml": reg("a@x", f"00{i}-x")}, "a@x").returncode == 0
    r = push(repo, {"004-x.yaml": reg("a@x", "004-x")}, "a@x")
    assert r.returncode == 1 and "上限" in r.stderr


def test_closure_frees_a_slot(repo):
    for i in (1, 2, 3):
        push(repo, {f"00{i}-x.yaml": reg("a@x", f"00{i}-x")}, "a@x")
    close = yaml.safe_dump({"reason": "done", "closed_by": "a@x", "closed_at": "2026-09-02"})
    assert push(repo, {"001-x.closure.yaml": close}, "a@x").returncode == 0
    assert push(repo, {"004-x.yaml": reg("a@x", "004-x")}, "a@x").returncode == 0


def signoff(rid, regtext, signer):
    return yaml.safe_dump({"schema": "preregistration-signoff/1", "prereg_id": rid,
                           "prereg_sha256": hashlib.sha256(regtext.encode("utf-8")).hexdigest(),
                           "signer": signer, "signed_at": "2026-09-02"})


def test_signoff_accepted_from_other_signer(repo, roles):
    t = reg("a@x", "001-x")
    push(repo, {"001-x.yaml": t}, "a@x")
    assert push(repo, {"001-x.signoff.yaml": signoff("001-x", t, "s@x")}, "s@x", roles).returncode == 0


def test_self_signoff_rejected(repo, roles):
    t = reg("s@x", "001-x")
    push(repo, {"001-x.yaml": t}, "s@x")
    r = push(repo, {"001-x.signoff.yaml": signoff("001-x", t, "s@x")}, "s@x", roles)
    assert r.returncode == 1 and "author" in r.stderr


def test_signoff_by_non_signer_rejected(repo, roles):
    t = reg("a@x", "001-x")
    push(repo, {"001-x.yaml": t}, "a@x")
    r = push(repo, {"001-x.signoff.yaml": signoff("001-x", t, "z@x")}, "z@x", roles)
    assert r.returncode == 1 and "signers" in r.stderr


def test_signoff_signer_must_equal_pusher(repo, roles):
    t = reg("a@x", "001-x")
    push(repo, {"001-x.yaml": t}, "a@x")
    r = push(repo, {"001-x.signoff.yaml": signoff("001-x", t, "s@x")}, "k@x", roles)
    assert r.returncode == 1 and "推送者" in r.stderr


def test_signoff_hash_mismatch_rejected(repo, roles):
    t = reg("a@x", "001-x")
    push(repo, {"001-x.yaml": t}, "a@x")
    r = push(repo, {"001-x.signoff.yaml": signoff("001-x", t + "x", "s@x")}, "s@x", roles)
    assert r.returncode == 1 and "sha256" in r.stderr


def test_signoff_without_roles_file_fails_closed(repo):
    t = reg("a@x", "001-x")
    push(repo, {"001-x.yaml": t}, "a@x")
    r = push(repo, {"001-x.signoff.yaml": signoff("001-x", t, "s@x")}, "s@x", None)
    assert r.returncode == 1 and "signers" in r.stderr

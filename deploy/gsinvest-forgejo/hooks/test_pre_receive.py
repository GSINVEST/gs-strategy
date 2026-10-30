"""用暫時的 bare repo 驗 pre_receive.py 的拒絕／放行（不需 Forgejo）。
執行：pytest deploy/gsinvest-forgejo/hooks/test_pre_receive.py
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def run_hook(bare, old, new, ref="refs/heads/main", pusher="a@x"):
    return subprocess.run([sys.executable, str(HOOK)], cwd=bare, input=f"{old} {new} {ref}\n",
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "GITEA_PUSHER_EMAIL": pusher, "GS_HOOKS_REPO": str(REPO)})


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

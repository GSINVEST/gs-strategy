"""排程相關：該跑哪一個交易日、cron 安裝腳本的 dry-run 行為。"""
import subprocess
import sys
from pathlib import Path

import pytest

from strategies._common.dryrun.executor import last_completed_session

REPO = Path(__file__).resolve().parents[1]
SESS = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02"]


def test_morning_of_a_session_targets_previous_session():
    # 11-02（週一）早上 07:00：當天還沒收盤，目標是 10-30
    assert last_completed_session(SESS, today="2026-11-02", hhmm="07:00") == "2026-10-30"


def test_after_close_buffer_targets_today():
    assert last_completed_session(SESS, today="2026-11-02", hhmm="14:30") == "2026-11-02"
    assert last_completed_session(SESS, today="2026-11-02", hhmm="13:45") == "2026-11-02"
    assert last_completed_session(SESS, today="2026-11-02", hhmm="13:44") == "2026-10-30"


def test_weekend_targets_last_session():
    assert last_completed_session(SESS, today="2026-10-31", hhmm="07:00") == "2026-10-30"


def test_no_completed_session_is_an_error():
    with pytest.raises(ValueError):
        last_completed_session(["2026-11-02"], today="2026-11-02", hhmm="07:00")


def _sh(*args):
    bash = "bash"
    return subprocess.run([bash, "scripts/dryrun_schedule.sh", *args],
                          capture_output=True, text=True, encoding="utf-8", cwd=REPO, timeout=30)


def test_schedule_default_only_prints_and_does_not_install():
    r = _sh("install", "--repo", "/srv/gs-strategy-dryrun")
    assert r.returncode == 0, r.stderr
    assert "dry-run" in r.stdout and "--apply" in r.stdout
    assert "# >>> gs-strategy dryrun >>>" in r.stdout
    assert "30 7 * * 2-6" in r.stdout             # 資料每日 06:00 更新，隔天早上跑前一個交易日
    assert "crontab" not in r.stderr


def test_schedule_remove_default_is_also_dry():
    r = _sh("remove")
    assert r.returncode == 0 and "--apply" in r.stdout


def test_schedule_rejects_unknown_args():
    assert _sh("install", "--bogus").returncode != 0

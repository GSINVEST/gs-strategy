"""容器層隔離（規格 §10 第 3 項）：設定的靜態檢查，加上有 docker 時的實跑驗證。"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

D = Path(__file__).resolve().parents[1] / "dockerfile" / "dryrun"


def compose():
    return yaml.safe_load((D / "compose.yml").read_text(encoding="utf-8"))


def test_dryrun_service_has_only_the_internal_network():
    c = compose()
    assert c["services"]["dryrun"]["networks"] == ["dry_internal"]
    assert c["networks"]["dry_internal"]["internal"] is True


def test_only_the_proxy_touches_the_egress_network():
    c = compose()
    on_egress = [n for n, s in c["services"].items() if "dry_egress" in s.get("networks", [])]
    assert on_egress == ["egress-proxy"]


def test_no_published_ports_no_host_mode_no_privileges():
    for name, svc in compose()["services"].items():
        assert "ports" not in svc, name
        assert svc.get("network_mode") not in ("host",), name
        assert not svc.get("privileged"), name
        assert svc.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name


def test_dryrun_mounts_are_exactly_repo_ro_bundle_ro_and_ledger_volume():
    vols = compose()["services"]["dryrun"]["volumes"]
    assert len(vols) == 3
    assert any(v.endswith(":/app:ro") for v in vols)
    assert any(v.endswith(":/zipline/data:ro") for v in vols)
    assert any(v.endswith(":/dryrun-ledger") and "DRYRUN_LEDGER_DIR" in v for v in vols)
    assert not [v for v in vols if "docker.sock" in v or v.startswith(("/mnt", "/c/", "~"))]
    assert compose()["services"]["dryrun"]["read_only"] is True


def test_squid_allows_only_tej_and_ends_with_deny_all():
    lines = [x.strip() for x in (D / "squid.conf").read_text(encoding="utf-8").splitlines()
             if x.strip() and not x.strip().startswith("#")]
    assert "acl allowed_domains dstdomain api.tej.com.tw" in lines
    allows = [x for x in lines if x.startswith("http_access allow")]
    assert allows == ["http_access allow connect_method tls_port allowed_domains"]
    assert [x for x in lines if x.startswith("http_access")][-1] == "http_access deny all"


def test_dockerfile_non_root_and_no_secrets_baked_in():
    text = (D / "Dockerfile").read_text(encoding="utf-8")
    assert "USER dry" in text
    assert "TEJAPI" not in text and "ENV " in text
    for bad in ("shioaji", "fubon", "ib_insync"):
        assert bad not in (D / "requirements.lock").read_text(encoding="utf-8").lower()


def test_lock_file_does_not_pin_zipline_itself_or_broker_sdks():
    names = {l.split("==")[0].lower().replace("_", "-") for l in
             (D / "requirements.lock").read_text(encoding="utf-8").splitlines() if "==" in l}
    assert "zipline-tej" not in names
    assert not names & {"shioaji", "fubon-neo", "ib-insync", "ibapi", "ccxt", "alpaca-trade-api"}


@pytest.mark.skipif(os.environ.get("DRYRUN_DOCKER_TESTS") != "1" or not shutil.which("docker"),
                    reason="needs docker and DRYRUN_DOCKER_TESTS=1 (and a built gs-dryrun:local)")
def test_live_egress_probe():
    r = subprocess.run(["bash", "verify-egress.sh"], cwd=D, capture_output=True, text=True,
                       encoding="utf-8", timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1500:]


def test_daily_script_runs_backup_and_health_on_host():
    s = (D.parents[1] / "scripts" / "dryrun_run_daily.sh").read_text(encoding="utf-8")
    assert "dryrun.ops backup" in s and "dryrun.ops health" in s
    assert "docker compose run" in s and s.index("dryrun.ops backup") > s.index("docker compose run")


def test_cron_entry_scripts_are_executable_in_git():
    """cron 直接執行腳本；git 裡沒有 +x 的話排程會 Permission denied（Windows 建檔最常漏）。"""
    if shutil.which("git") is None:
        pytest.skip("no git")
    root = D.parents[1]
    out = subprocess.run(["git", "ls-files", "-s", "scripts/dryrun_run_daily.sh", "scripts/dryrun_schedule.sh",
                          "dockerfile/dryrun/verify-egress.sh"], cwd=root, capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        pytest.skip("not a git checkout")
    modes = {line.split()[3]: line.split()[0] for line in out.stdout.splitlines()}
    assert all(m == "100755" for m in modes.values()), modes


def test_daily_script_picks_up_default_notify_command():
    s = (D.parents[1] / "scripts" / "dryrun_run_daily.sh").read_text(encoding="utf-8")
    assert ".config/gs-dryrun/notify" in s and "DRYRUN_ALERT_CMD" in s

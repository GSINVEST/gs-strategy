"""scripts/regime_labels.py：只用標準函式庫（cron 主機的 python3 沒有 pandas），解析 QUANTDATA /sql 的回應。"""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("regime_labels_script", REPO / "scripts" / "regime_labels.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def test_script_imports_no_third_party_modules():
    src = (REPO / "scripts" / "regime_labels.py").read_text(encoding="utf-8")
    for bad in ("import pandas", "import requests", "from strategies._common import qd", "import numpy"):
        assert bad not in src


def test_parse_closes_handles_rfc1123_dates_sorts_and_drops_bad_rows():
    data = {"columns": ["trading_date", "close"], "rows": [
        ["Tue, 03 Nov 2026 00:00:00 GMT", 43376.0], ["Mon, 02 Nov 2026 00:00:00 GMT", 45256.0],
        ["Fri, 30 Oct 2026 00:00:00 GMT", None], ["Thu, 29 Oct 2026 00:00:00 GMT", 0]]}
    assert M.parse_closes(data) == [("2026-11-02", 45256.0), ("2026-11-03", 43376.0)]


def test_parse_closes_accepts_iso_dates_and_rejects_unknown_shape():
    assert M.parse_closes({"columns": ["trading_date", "close"], "rows": [["2026-11-03", 1.5]]}) == [("2026-11-03", 1.5)]
    import pytest
    with pytest.raises(ValueError):
        M.parse_closes({"columns": ["a", "b"], "rows": []})


def test_year_ranges_chunks_by_calendar_year():
    r = M.year_ranges("2016-01-04", "2018-03-01")
    assert r == [("2016-01-04", "2016-12-31"), ("2017-01-01", "2017-12-31"), ("2018-01-01", "2018-03-01")]
    assert M.year_ranges("2026-05-01", "2026-06-01") == [("2026-05-01", "2026-06-01")]


def test_daily_script_wires_regime_with_last_good_fallback():
    s = (REPO / "scripts" / "dryrun_run_daily.sh").read_text(encoding="utf-8")
    assert "DRYRUN_REGIME" in s and "regime_labels.py" in s and "--regime-labels" in s
    assert 'DRYRUN_OBSERVE:-0}" = "1"' in s
    # 更新失敗沿用上次成功的檔案；快照帶標籤失敗再退回不帶標籤，不能因為 regime 讓整份快照推不出去
    assert ".new" in s and "沿用" in s
    assert s.count("dryrun.snapshot") >= 2

from pathlib import Path

from quant_crawler.config import SourceConfig
from quant_crawler.crawlers.github_search import GithubSearchCrawler
from quant_crawler.crawlers.rss import RssCrawler
from quant_crawler.storage.db import Storage

FIX = Path(__file__).parent / "fixtures" / "rss_sample.xml"


class _Resp:
    def __init__(self, text="", js=None, status=200):
        self.text, self._js, self.status_code = text, js, status

    def json(self):
        return self._js


def test_rss_records(tmp_db):
    cfg = SourceConfig(name="rss", extras={"feeds": [{"name": "inst", "url": "https://x/feed"}]})
    c = RssCrawler(cfg, Storage(tmp_db))
    c.session.get = lambda url, **kw: _Resp(text=FIX.read_text(encoding="utf-8"))
    recs = list(c.fetch())
    assert len(recs) == 2
    r = recs[0]
    assert r.source == "rss" and r.title.startswith("Trend following")
    assert r.published == "2026-11-02" and r.raw_extra["feed"] == "inst"
    assert recs[0].source_id != recs[1].source_id


def test_rss_failed_feed_is_skipped(tmp_db):
    cfg = SourceConfig(name="rss", extras={"feeds": [{"name": "a", "url": "u"}]})
    c = RssCrawler(cfg, Storage(tmp_db))
    c.session.get = lambda url, **kw: _Resp(status=503)
    assert list(c.fetch()) == []


ITEM = {"full_name": "o/alpha-lob", "description": "limit order book alpha factors",
        "html_url": "https://github.com/o/alpha-lob", "pushed_at": "2026-10-30T01:02:03Z",
        "stargazers_count": 321, "topics": ["quantitative-finance"], "id": 99}


def test_github_record():
    rec = GithubSearchCrawler._repo_to_record(ITEM)
    assert rec.source == "github" and rec.source_id == "o/alpha-lob"
    assert rec.published == "2026-10-30" and rec.raw_extra["stars"] == 321
    assert rec.categories == ["quantitative-finance"]


def test_github_fetch_sends_token_only_in_header(tmp_db, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "SECRET")
    seen = {}

    def fake_get(url, **kw):
        seen["url"], seen["headers"] = url, kw.get("headers", {})
        return _Resp(js={"items": [ITEM, {**ITEM, "full_name": "o/empty", "description": None}]})

    cfg = SourceConfig(name="github", extras={"queries": ["topic:quantitative-finance"]})
    c = GithubSearchCrawler(cfg, Storage(tmp_db))
    c.session.get = fake_get
    recs = list(c.fetch())
    assert [r.source_id for r in recs] == ["o/alpha-lob"]        # 沒描述的跳過
    assert "SECRET" not in seen["url"]
    assert seen["headers"]["Authorization"] == "Bearer SECRET"


def test_github_no_token_ok(tmp_db, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cfg = SourceConfig(name="github", extras={"queries": ["q"]})
    c = GithubSearchCrawler(cfg, Storage(tmp_db))
    c.session.get = lambda url, **kw: _Resp(js={"items": []}) if "Authorization" not in kw.get("headers", {}) else None
    assert list(c.fetch()) == []

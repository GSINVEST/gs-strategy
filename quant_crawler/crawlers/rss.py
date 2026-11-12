"""通用 RSS／Atom 來源（機構部落格等）。feeds 由 config extras 給定。官方訂閱源，不硬爬頁面。"""
from __future__ import annotations

import hashlib
from datetime import datetime
from time import struct_time
from typing import Iterator

import feedparser

from quant_crawler.crawlers.base import BaseCrawler
from quant_crawler.storage.models import PaperRecord


def _date(t: struct_time | None) -> str | None:
    if not t:
        return None
    return datetime(*t[:6]).strftime("%Y-%m-%d")


class RssCrawler(BaseCrawler):
    name = "rss"
    bypass_relevance = True  # feeds 由我們手選，整批都算相關

    def fetch(self) -> Iterator[PaperRecord]:
        for feed in self.config.extras.get("feeds", []):
            resp = self.session.get(feed["url"])
            if resp.status_code != 200:
                self.log.error("rss %s returned %s", feed["name"], resp.status_code)
                continue
            parsed = feedparser.parse(resp.text)
            for e in parsed.entries:
                link = getattr(e, "link", "") or ""
                sid = hashlib.sha256((getattr(e, "id", "") or link).encode()).hexdigest()[:16]
                title = (getattr(e, "title", "") or "").strip()
                if not title:
                    continue
                yield PaperRecord(
                    source="rss", source_id=sid, title=title,
                    abstract=(getattr(e, "summary", "") or "").strip(),
                    published=_date(getattr(e, "published_parsed", None)),
                    url=link, raw_extra={"feed": feed["name"]},
                )

"""GitHub REST Search：以 topic／關鍵字找量化相關 repo。token 只放 Authorization header。"""
from __future__ import annotations

import os
from typing import Iterator
from urllib.parse import urlencode

from quant_crawler.crawlers.base import BaseCrawler
from quant_crawler.storage.models import PaperRecord

API = "https://api.github.com/search/repositories"


class GithubSearchCrawler(BaseCrawler):
    name = "github"
    bypass_relevance = True   # 查詢字串本身就是相關性條件

    @staticmethod
    def _repo_to_record(item: dict) -> PaperRecord:
        return PaperRecord(
            source="github", source_id=item["full_name"], title=item["full_name"],
            abstract=item.get("description") or "",
            published=(item.get("pushed_at") or "")[:10] or None,
            url=item.get("html_url", ""), categories=list(item.get("topics") or []),
            raw_extra={"stars": item.get("stargazers_count", 0), "gh_id": item.get("id")},
        )

    def fetch(self) -> Iterator[PaperRecord]:
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        for q in self.config.extras.get("queries", []):
            url = f"{API}?" + urlencode({"q": q, "sort": "stars", "order": "desc",
                                         "per_page": min(self.config.max_items_per_run, 30)})
            resp = self.session.get(url, headers=headers)
            if resp.status_code != 200:
                self.log.error("github search %r returned %s", q, resp.status_code)
                continue
            for item in resp.json().get("items", []):
                if not (item.get("description") or "").strip():
                    continue
                yield self._repo_to_record(item)

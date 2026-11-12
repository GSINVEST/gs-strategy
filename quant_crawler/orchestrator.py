"""Orchestrator — registers all crawler classes and runs them."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from quant_crawler.config import SOURCES, EXPERIMENT_LOG, SourceConfig
from quant_crawler.crawlers.aqr import AQRCrawler
from quant_crawler.crawlers.arxiv import ArxivCrawler
from quant_crawler.crawlers.base import BaseCrawler
from quant_crawler.crawlers.fed import FedFedsCrawler
from quant_crawler.crawlers.github_search import GithubSearchCrawler
from quant_crawler.crawlers.nber import NBERCrawler
from quant_crawler.crawlers.repec import RepecCrawler
from quant_crawler.crawlers.rss import RssCrawler
from quant_crawler.crawlers.ssrn import SSRNCrawler
from quant_crawler.crawlers.wiley import WileyCrawler
from quant_crawler.storage.db import Storage
from quant_crawler.utils.logging import get_logger

log = get_logger("orchestrator")


REGISTRY: dict[str, Callable[[SourceConfig, Storage], BaseCrawler]] = {
    "arxiv": ArxivCrawler,
    "nber": NBERCrawler,
    "repec": RepecCrawler,
    "fed_feds": FedFedsCrawler,
    "aqr": AQRCrawler,
    "wiley": WileyCrawler,
    "ssrn": SSRNCrawler,  # disabled by default
    "rss": RssCrawler,  # disabled by default
    "github": GithubSearchCrawler,  # disabled by default
}


def run_all(
    storage: Optional[Storage] = None,
    only: Optional[list[str]] = None,
    include_disabled: bool = False,
) -> list[dict]:
    storage = storage or Storage()
    results: list[dict] = []
    for name, cls in REGISTRY.items():
        if only and name not in only:
            continue
        cfg = SOURCES.get(name)
        if cfg is None:
            log.warning("no config for %s", name)
            continue
        if not cfg.enabled and not include_disabled:
            log.info("skipping disabled source: %s", name)
            continue
        log.info("=== running %s ===", name)
        crawler = cls(cfg, storage)
        results.append(crawler.run())
    return results


def append_experiment_log(results: list[dict]) -> None:
    """Append a run summary section to docs/EXPERIMENT_LOG.md."""
    if not results:
        return
    EXPERIMENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"\n## Run @ {ts}\n",
        "| source | seen | kept | error |",
        "|--------|-----:|-----:|-------|",
    ]
    for r in results:
        err = (r.get("error") or "—").replace("|", "\\|")[:80]
        lines.append(
            f"| {r['source']} | {r['items_seen']} | {r['items_kept']} | {err} |"
        )
    lines.append("")
    with open(EXPERIMENT_LOG, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

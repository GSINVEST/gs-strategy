"""IdeaCard：一張 idea 卡 = 一個 Markdown 檔（YAML front-matter + 備註本文）。"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Iterator

import yaml

STATUSES = ("new", "triaged", "defer", "drop", "fsm", "impl",
            "bt_registered", "bt_done", "admitted")

# 只准往前走；defer／drop／admitted 是終點。
_ALLOWED = {
    "new": {"triaged", "defer", "drop"},
    "triaged": {"fsm", "defer", "drop"},
    "fsm": {"impl", "defer", "drop"},
    "impl": {"bt_registered", "defer"},
    "bt_registered": {"bt_done"},
    "bt_done": {"admitted", "drop"},
    "defer": set(),
    "drop": set(),
    "admitted": set(),
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,95}$")
_FM_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


@dataclass
class IdeaCard:
    id: str
    source: str
    title: str
    hypothesis: str = ""
    mechanism: str = ""
    market: str = ""
    horizon: str = ""
    data_needs: list = field(default_factory=list)
    flaws: list = field(default_factory=list)
    signature: str = ""
    quotes: list = field(default_factory=list)      # [{"field": ..., "text": ...}]
    fsm_expressible: bool | None = None
    status: str = "new"
    reason: str = ""
    resurrect_when: str = ""
    gsl_path: str = ""
    ir_path: str = ""
    verdict: str = ""        # TLC verdict（見 tlc.py）
    batch_id: str = ""
    notes: str = ""          # 本文

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ValueError(f"id 必須符合 {_ID_RE.pattern}：{self.id!r}")
        if self.status not in STATUSES:
            raise ValueError(f"未知狀態 {self.status!r}")


def set_status(card: IdeaCard, new: str, reason: str = "") -> None:
    if new not in STATUSES:
        raise ValueError(f"未知狀態 {new!r}")
    if new not in _ALLOWED[card.status]:
        raise ValueError(f"不允許 {card.status} → {new}")
    card.status = new
    if reason:
        card.reason = reason


def save_card(card: IdeaCard, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    data = asdict(card)
    notes = data.pop("notes")
    fm = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    path = root / f"{card.id}.md"
    path.write_text(f"---\n{fm}---\n{notes}", encoding="utf-8", newline="\n")
    return path


def load_card(path: Path) -> IdeaCard:
    m = _FM_RE.match(path.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{path} 沒有 front-matter")
    data = yaml.safe_load(m.group(1)) or {}
    data["notes"] = m.group(2)
    known = {f.name for f in fields(IdeaCard)}
    return IdeaCard(**{k: v for k, v in data.items() if k in known})


def iter_cards(root: Path) -> Iterator[IdeaCard]:
    for p in sorted(root.glob("*.md")):
        yield load_card(p)

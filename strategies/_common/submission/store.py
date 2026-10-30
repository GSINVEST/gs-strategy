"""append-only JSONL 讀寫與跨行程鎖。

鎖直接重用 graph/ledger.py 的 ``_exclusive``（Windows msvcrt／Linux flock），不另寫一把：
兩個入口（跑完記帳的 SelectionLedger、核准即記帳的佇列）要序列化在同一個 ledger 上。
注意 ``_exclusive`` 不可重入（同行程第二次 flock 會死鎖），所以持鎖區塊內不得再呼叫
另一個會取鎖的公開方法。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from strategies._common.graph.ledger import _exclusive  # noqa: F401  (re-export)


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def append_jsonl(path: Path, record: dict) -> None:
    """追加一行並 fsync。檔尾缺 LF 時先補一個，不動既有位元組。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    prefix = b""
    if path.is_file() and path.stat().st_size:
        with path.open("rb") as reader:
            reader.seek(-1, os.SEEK_END)
            if reader.read(1) != b"\n":
                prefix = b"\n"
    with path.open("ab") as handle:
        handle.write(prefix + data)
        handle.flush()
        os.fsync(handle.fileno())

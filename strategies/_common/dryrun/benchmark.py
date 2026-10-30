"""D2（benchmark 包凍結）的介面 stub。

D3 不依賴 D2 的產出，也不實作它：D2 合併前一律回報「不可用」，帳本的
``benchmark_status`` 會寫 ``D2_UNAVAILABLE``，不能被讀成「已與 benchmark 比較」。
"""
from __future__ import annotations

from typing import Optional, Protocol


class BenchmarkPackProvider(Protocol):
    def get_pack(self, *, framework_version: str, data_snapshot: str,
                 cost_model_version: str) -> Optional[str]:
        """回傳 benchmark 包版本號；沒有可比的包就回傳 None。"""


class NoBenchmarkPack:
    def get_pack(self, *, framework_version: str, data_snapshot: str,
                 cost_model_version: str) -> Optional[str]:
        return None

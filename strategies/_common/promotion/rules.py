"""晉升門檻。**只准向上收緊，不准下調**（決策樹 §7 第 4 條）。

改動任何數字都要同步改 tests/test_promotion_gate.py::test_thresholds_are_pinned。
"""
RULES_VERSION = "1"

DSR_MIN = 0.95        # Bailey & López de Prado (2014)，與決策樹 best_of_M 一致
PBO_MAX = 0.5         # PBO 必須小於此值
N_OK = 250            # 有效觀測數達此值才算 ok（決策樹 §6 事件研究判決門檻）
N_PROVISIONAL = 50    # 達此值只算 provisional
SPA_ALPHA = 0.05      # Hansen SPA 的顯著水準

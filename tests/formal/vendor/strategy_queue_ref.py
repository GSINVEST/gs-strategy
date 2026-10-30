"""【參考實作，不是產品程式】策略提交佇列＋人工核准＋trial 計數。

gs-strategy／StratyUI 目前沒有提交佇列（規格 §6 明寫「另開 PR 於該 repo」）。這個檔案只是把
docs/superpowers/specs/2026-10-29-strategy-trader-platform-design.md §6 的條文寫成最小的 Python，
讓 specs/tla/strategy-queue/Queue.tla 的 TLC 軌跡有東西可以回放。它**不**證明任何真實系統；
等真正的佇列實作出現，這份要被換成對真程式的 adapter，並在 manifest 的 actions 補上 file:line。
"""


class StrategyQueue:
    def __init__(self, approvers=("kevin", "admin")):
        self.approvers = set(approvers)
        self.prereg_ids = set()
        self.status = {}       # id -> pending / approved / retired；沒提交過的不在裡面
        self.runs = {}         # id -> 實際執行回測的次數（觀測值，由 _execute_backtest 累加）
        self.trials = {}       # id -> trial 帳本（selection trial 計數）
        self.approved_by = {}
        self.signed = set()
        self.pool = set()

    def add_prereg(self, sid):
        self.prereg_ids.add(sid)

    # 規格：必須已有該點子的 prereg 草稿；提交不執行回測。冪等：重送什麼都不改
    def submit(self, sid):
        if sid in self.status:
            return "noop"
        if sid not in self.prereg_ids:
            return "rejected"
        self.status[sid] = "pending"
        return "queued"

    # 規格：人按核准才執行；核准同時簽核；每次執行計入 trial。冪等：重複核准什麼都不改
    def approve(self, sid, who):
        if who not in self.approvers:
            return "rejected"
        st = self.status.get(sid)
        if st == "approved":
            return "noop"
        if st != "pending":
            return "rejected"
        self.status[sid] = "approved"
        self.approved_by[sid] = who
        self._execute_backtest(sid)
        self.signed.add(sid)
        self.pool.add(sid)
        return "approved"

    # 規格未寫；退出池不得讓 trial 減少（trial 是歷史）
    def retire(self, sid):
        if self.status.get(sid) != "approved":
            return "rejected"
        self.status[sid] = "retired"
        self.pool.discard(sid)
        return "retired"

    def _execute_backtest(self, sid):
        self.runs[sid] = self.runs.get(sid, 0) + 1
        self.trials[sid] = self.trials.get(sid, 0) + 1

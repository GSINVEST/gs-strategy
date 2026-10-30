------------------------- MODULE Queue -------------------------
(***************************************************************************)
(* 策略提交佇列＋人工核准＋trial 計數（子專案 C，規格 §6）。                 *)
(* 這份模型對應的是「設計規格」，不是已存在的程式：gs-strategy／StratyUI    *)
(* 現在沒有提交佇列，所以沒有「實作對照」，只有規格條文對照。               *)
(*                                                                         *)
(* 動作 ↔ 規格條文（docs/superpowers/specs/2026-10-29-...-design.md §6）：   *)
(*   AddPrereg ↔ prereg 草稿存在（2026-10-21 ideaflow 規格）               *)
(*   Submit    ↔ 提交：必須已有 prereg 草稿；提交不執行回測（line 121）     *)
(*   Approve   ↔ 核准：人按核准才執行；同時簽核；每次執行計入 trial（122）  *)
(*   Retire    ↔ 策略退出池（規格未寫；trial 是歷史，不得因此減少）         *)
(*   SubmitAgain／ApproveAgain ↔ 重送／重複核准（規格未寫，以冪等建模）      *)
(* 每個動作視為原子。                                                       *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets
CONSTANTS Ids, Approvers, Buggy
\* Buggy 是負控制開關："none" 才是正式檢查；其餘故意破壞一處，TLC 必須報對應性質違反。
ASSUME Buggy \in {"none", "skiptrial", "trialdec", "noprereg", "submitruns", "dupapprove", "resubmit", "nosign"}
VARIABLES prereg, status, runs, trials, floor, approvedBy, signed, pool
vars == <<prereg, status, runs, trials, floor, approvedBy, signed, pool>>

Init == /\ prereg = {}
        /\ status = [s \in Ids |-> "none"]
        /\ runs = [s \in Ids |-> 0]
        /\ trials = [s \in Ids |-> 0]
        /\ floor = [s \in Ids |-> 0]
        /\ approvedBy = [s \in Ids |-> "none"]
        /\ signed = {}
        /\ pool = {}

AddPrereg(s) == /\ s \notin prereg
                /\ prereg' = prereg \cup {s}
                /\ floor' = trials
                /\ UNCHANGED <<status, runs, trials, approvedBy, signed, pool>>

\* Buggy = "noprereg"：略過 prereg 檢查；"submitruns"：提交時就跑回測；"resubmit"：已核准的策略可以被重送回待審。
Submit(s) == /\ (status[s] = "none" \/ (Buggy = "resubmit" /\ status[s] = "approved"))
             /\ (s \in prereg \/ Buggy = "noprereg")
             /\ status' = [status EXCEPT ![s] = "pending"]
             /\ runs' = IF Buggy = "submitruns" THEN [runs EXCEPT ![s] = runs[s] + 1] ELSE runs
             /\ trials' = IF Buggy = "submitruns" THEN [trials EXCEPT ![s] = trials[s] + 1] ELSE trials
             /\ floor' = trials
             /\ UNCHANGED <<prereg, approvedBy, signed, pool>>

\* 重送：已經提交過就什麼都不改（冪等）。
SubmitAgain(s) == /\ status[s] # "none"
                  /\ floor' = trials
                  /\ UNCHANGED <<prereg, status, runs, trials, approvedBy, signed, pool>>

\* Buggy = "skiptrial"：執行了回測卻沒記 trial；"dupapprove"：已核准的再核准一次又跑一次；"nosign"：入池卻沒簽核。
Approve(s,h) == /\ (status[s] = "pending" \/ (Buggy = "dupapprove" /\ status[s] = "approved"))
                /\ status' = [status EXCEPT ![s] = "approved"]
                /\ approvedBy' = [approvedBy EXCEPT ![s] = h]
                /\ runs' = [runs EXCEPT ![s] = runs[s] + 1]
                /\ trials' = IF Buggy = "skiptrial" THEN trials ELSE [trials EXCEPT ![s] = trials[s] + 1]
                /\ signed' = IF Buggy = "nosign" THEN signed ELSE signed \cup {s}
                /\ pool' = pool \cup {s}
                /\ floor' = trials
                /\ UNCHANGED prereg

\* 重複核准：已核准就什麼都不改（冪等，不再執行、不再計 trial）。
ApproveAgain(s,h) == /\ status[s] = "approved"
                     /\ floor' = trials
                     /\ UNCHANGED <<prereg, status, runs, trials, approvedBy, signed, pool>>

\* Buggy = "trialdec"：退出時把 trial 扣回去。
Retire(s) == /\ status[s] = "approved"
             /\ status' = [status EXCEPT ![s] = "retired"]
             /\ pool' = pool \ {s}
             /\ trials' = IF Buggy = "trialdec" THEN [trials EXCEPT ![s] = trials[s] - 1] ELSE trials
             /\ floor' = trials
             /\ UNCHANGED <<prereg, runs, approvedBy, signed>>

Next == \/ \E s \in Ids: AddPrereg(s) \/ Submit(s) \/ SubmitAgain(s) \/ Retire(s)
        \/ \E s \in Ids, h \in Approvers: Approve(s,h) \/ ApproveAgain(s,h)

TrialMonotone == \A s \in Ids: trials[s] >= floor[s]
TrialLedger == \A s \in Ids: trials[s] = runs[s]
PreregBeforeSubmit == \A s \in Ids: status[s] # "none" => s \in prereg
RunNeedsApproval == \A s \in Ids: runs[s] > 0 => approvedBy[s] # "none"
NoDoubleRun == \A s \in Ids: runs[s] <= 1
SignedBeforePool == pool \subseteq signed
TypeOK == /\ prereg \subseteq Ids
          /\ status \in [Ids -> {"none", "pending", "approved", "retired"}]
          /\ runs \in [Ids -> Nat] /\ trials \in [Ids -> Nat] /\ floor \in [Ids -> Nat]
          /\ approvedBy \in [Ids -> Approvers \cup {"none"}]
          /\ signed \subseteq Ids /\ pool \subseteq Ids

Spec == Init /\ [][Next]_vars
=============================================================================

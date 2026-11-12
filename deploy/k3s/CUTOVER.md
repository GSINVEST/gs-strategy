# 策略圖：WSL → jsonhuang k3s 切換與回退手冊（P3）

排定：**2026-11-14（六）10:00**，預計停機 15–20 分鐘（台股休市日）。切換由人確認後才執行。

## 現況（P0–P2 已完成，2026-11-12）
- pod `strategy-graph`（gs-dash）已在 jsonhuang 執行：資料快照、gs-zipline-tej、TEJ／LLM／proxy-token Secret、QuantData 副本位址皆就緒；Service 為 **ClusterIP**（不對外）。
- 身分驗證：Host 為 `strategy.gsinvest.com` 的請求必須帶 `X-Gs-Proxy-Token`，否則 403（已驗證：無 token／偽造 email／錯 token 皆 403，正確 token 與 kubelet 探測 200）。
- 每日快照：jsonhuang root crontab 03:20 跑 `/srv/gs-data/strategy-graph-backup/backup.sh`，保留 14 份（僅防誤操作／壞檔；**異地複本尚未建立**，jsonhuang 目前連不到 gsinvest 的 SSH）。
- WSL 的 `gs-strategy-graph-ui`（9112）仍是正式環境。

## T-1　切換前（週五完成）
1. merge PR #90（內含 --bind、proxy token、manifest、本手冊）；以 merge 後的 commit 重建映像並固定 tag。
2. 公告員工：週六 10:00–10:30 策略圖暫停，之後需重新登入一次。
3. 在 gb10、gs-linux 準備好設定檔但**先不啟用**（見 T2）；gs-linux 的 ufw 放行 gb10（192.168.11.90）到 18453——**需由使用者輸入 sudo**。
4. 確認 WSL ledger 筆數：`wc -l ~/gs-strategy-deploy/log/trials.jsonl`（11/12 為 726）。

## T0　停舊（10:00）
1. 公告員工「開始停機」。
2. 停 WSL 排程工作（只停不刪，保留回退）：
   `schtasks /End /TN gs-strategy-graph-ui` 並 `schtasks /Change /TN gs-strategy-graph-ui /DISABLE`
3. 確認 9112 已無回應：`curl -m 3 http://127.0.0.1:9112/`（應連線失敗）。**從這一刻起 WSL 不得再寫入 ledger。**

## T1　最後一次同步（10:05）
從 WSL 覆蓋 pod 資料（**覆蓋 11/12 快照，包含 ledger**），先備份 pod 現況：
1. `ssh jsonhuang 'sudo /srv/gs-data/strategy-graph-backup/backup.sh'`
2. WSL 打包 `log/trials.jsonl`、`data/backtests.sqlite`、`.graph-runs/`，scp 到 jsonhuang，解到 `/srv/gs-data/strategy-graph/` 後 `chown -R 10001:10001`。
3. 核對：兩邊 `sha256sum log/trials.jsonl` 與 `wc -l` 一致。
4. `kubectl -n gs-dash rollout restart deploy/strategy-graph`，等 Ready。

## T2　接上新路徑（10:10）
1. gs-linux：`k3s-dash-forward.service` 加一條 `-L 18453:127.0.0.1:30112`（比照 18450–18452），重啟該 user service。
2. jsonhuang：Service 由 ClusterIP 改回 NodePort 30112（改 `strategy-graph.yaml` 的 type／nodePort 後 apply）。
   proxy token 擋住直連者，所以 NodePort 對區網開放是可接受的。
3. gb10：`/etc/caddy/conf.d/strategy-graph.caddy`
   ```
   http://:8112 {
   	reverse_proxy 192.168.11.141:18453
   }
   ```
   `caddy reload`。
4. wiki-caddy（192.168.11.197，`Caddyfile.oidc`，**原地覆寫並保持 CRLF**，Git Bash 下 `MSYS_NO_PATHCONV=1`）：把 `strategy.gsinvest.com` 的 upstream 由 `host.docker.internal:9112` 改成 `192.168.11.90:8112`，並在 OIDC 驗證後的 proxy 區塊加：
   `header_up X-Gs-Proxy-Token <token>`
   token 取得（不要寫進 git、不要貼到聊天）：
   `ssh jsonhuang "sudo k3s kubectl -n gs-dash get secret strategy-graph-proxy -o jsonpath='{.data.GS_PROXY_TOKEN}' | base64 -d"`
   reload：`MSYS_NO_PATHCONV=1 docker exec <wiki-caddy> caddy reload --config /etc/caddy/Caddyfile`

## T3　驗收（10:15）
- 員工帳號經 OIDC 登入 `strategy.gsinvest.com`，Deck 桌面版面板重新整理後可見。
- 節點 metadata 顯示為中文欄位表；策略池列出 19 個策略。
- 實際按一次「執行」：ledger 筆數 +1、actor 為登入者 email、回測完成。
- 直連旁路測試：`curl -H 'Host: strategy.gsinvest.com' -H 'X-Auth-Request-Email: x@y' http://192.168.0.x:30112/api/session` 必須 403。
- 通過後公告「恢復」。

## 回退（任一步驗收失敗）
1. wiki-caddy upstream 改回 `host.docker.internal:9112` 並 reload。
2. **反向同步 ledger**：若 pod 期間已有新的「執行」，先把 pod 的 `log/trials.jsonl`、`data/backtests.sqlite`、`.graph-runs` 拷回 WSL（否則 N 倒退、統計誠信破功）。若 pod 期間沒有新執行（T3 的那一次除外，需一併拷回），可直接沿用 WSL 原檔。
3. `schtasks /Change /TN gs-strategy-graph-ui /ENABLE` 並 `/Run`。
4. 確認 9112 回 200、`wc -l` 與預期一致。

## T4　觀察與退役（1–2 週後）
- 每日確認 `backup.log` 正常、pod 無重啟。
- 退役：刪除 WSL 排程工作與 `~/gs-strategy-deploy`，更新 `docs/deploy-intranet.md`。

## 尚未解決（不阻擋切換，但要追）
- **異地備份**：jsonhuang 目前連不到 gsinvest（22 埠被拒）。需要決定目的地並開通，或改由別台定期 pull。
- **LLM 金鑰**：`strategy-graph-llm` 目前沿用 `genesis-deck-kevin.key`（個人金鑰，用量記在 Kevin 名下）。需有人在 gateway 核發專用服務金鑰後重建 Secret：`kubectl -n gs-dash create secret generic strategy-graph-llm --from-literal=GS_LLM_API_KEY=… --dry-run=client -o yaml | kubectl apply -f -`，再 `rollout restart`。
- **QuantData 副本新鮮度**：副本與 gb10 主庫的差距未量測；回測多走 `qd_cache`，影響有限。

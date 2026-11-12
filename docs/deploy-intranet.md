# 內網部署：strategy.gsinvest.com

> 2026-10-06 上線；主機 kevin-windows（192.168.11.197）

## 拓樸

```
同仁瀏覽器 ──http://strategy.gsinvest.com──► wiki-caddy（Docker，:80）
                                              │  oidc_guard：未登入導去 Forgejo 登入（與 obsidian／dashboard 共用 session）
                                              ▼
                                   host.docker.internal:9112
                                              │  Docker Desktop 轉進 WSL 的 loopback
                                              ▼
                     WSL Ubuntu-24.04：graph server，只綁 127.0.0.1:9112
                     （~/gs-strategy-deploy，--public-host strategy.gsinvest.com）
```

- **唯一入口是 Caddy**：graph server 只綁 loopback。從區網直連 `192.168.11.197:9112` 是不通的（已實測），所以沒有辦法繞過登入。
- **執行者歸屬**：oauth2-proxy 驗證通過後，會把身分放在 `X-Auth-Request-Email` 傳下來，graph server 會把它寫進結果庫 `runs.actor`。
  - Caddy 的 `oidc_guard` 會先剝掉客戶端自己帶的同名 header，所以這個值無法偽造。
  - graph server 也只在請求是經由 `--public-host` 那個名稱進來時，才採信這個 header。
- vhost 設定在 wiki-poc 的 `deploy/Caddyfile.oidc`（`http://strategy.gsinvest.com` 那一段）。

## 常駐

Windows 排程工作 `gs-strategy-graph-ui`：使用者登入時啟動，失敗時每分鐘重試，最多 999 次。

```
wsl.exe -d Ubuntu-24.04 -e bash -lc "cd ~/gs-strategy-deploy && exec ../gs-strategy/.venv/bin/python \
  -m strategies._common.graph ui --graph strategies/llm_view_tx/graph.json --port 9112 \
  --public-host strategy.gsinvest.com"
```

- `~/gs-strategy-deploy` 是獨立的 worktree（detached），與開發用的工作目錄分開。PR 合併後，在這個 worktree 執行 `git checkout --detach origin/main`，再重啟排程工作即可更新。
- UI 的 `dist/` 不進版控。WSL 裡沒有 node，所以要在 Windows 上 build，再把 dist 複製進來。
- `.env`（TEJ 金鑰）與 `.venv-bt` 共用主 checkout `~/gs-strategy` 的那一份。

## 使用前要知道

- 這是**真實模式**：同仁在網頁上按「執行」，會跑真實回測、計入正式的試驗次數 N（這個 worktree 的 `log/trials.jsonl`），而且 LLM 呼叫用的是設定在本機的 Genesis Deck 金鑰。
- 結果庫在 `~/gs-strategy-deploy/data/backtests.sqlite`；「結果」面板會顯示每次執行的執行者。
- DNS：內網 UniFi 需要一筆本地紀錄 `strategy.gsinvest.com → 192.168.11.197`，和 obsidian／wiki 相同。

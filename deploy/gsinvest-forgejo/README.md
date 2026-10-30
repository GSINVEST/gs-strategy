# gsinvest 的 Forgejo 部署（權威 bare repo + pre-receive）

對應 `docs/spec/host-migration-gsinvest.md` §5、§7、§11 階段 A。**這個目錄只是部署材料，尚未在 gsinvest 上部署。**

gsinvest 現況（2026-10-30 唯讀探查）：docker 29.4 + compose v5.1、磁碟剩 6.5T、無 sudo、`:2222` 已被占用
（所以本部署 SSH 用 `2223`）、已有 chipk／qd／vllm 等容器網路，沒有 gs-strategy 相關容器。

## 一、你要手動做的設定步驟

1. **匯出固定版本的驗證腳本**（在任一 clone，commit 要是你審核過的 main）：
   ```bash
   git checkout <審核過的 main commit>
   mkdir -p deploy/gsinvest-forgejo/pinned-repo
   cp -r scripts deploy/gsinvest-forgejo/pinned-repo/
   mkdir -p deploy/gsinvest-forgejo/pinned-repo/strategies/_common
   cp -r strategies/__init__.py deploy/gsinvest-forgejo/pinned-repo/strategies/ 2>/dev/null || true
   cp -r strategies/_common/validation strategies/_common/__init__.py deploy/gsinvest-forgejo/pinned-repo/strategies/_common/ 2>/dev/null || true
   ```
   `pinned-repo/` 刻意不進版控；記下 commit hash，之後升級就是重做這步並重 build。
2. **複製到 gsinvest**：`rsync -a deploy/gsinvest-forgejo/ gsinvest:~/gs-forgejo/`
3. **起容器**：`ssh gsinvest 'cd ~/gs-forgejo && docker compose up -d --build'`
4. **建管理員**（首次）：
   `docker compose exec -u git forgejo forgejo admin user create --admin --username kevin --email <kevin 的 email> --random-password`
   （密碼只顯示一次，存進密碼管理器，不要貼進對話或 log。）
5. **建 org `gs`、repo `gs-strategy`**（網頁介面，經 SSH tunnel：`ssh -L 3301:127.0.0.1:3301 gsinvest`，開 http://127.0.0.1:3301）。
6. **建使用者** joker、jason（`--random-password`，各自登入後改密碼並上傳 SSH 公鑰）。signers = kevin、joker；executor = jason。
7. **掛 pre-receive hook**：把 `/opt/gs-hooks/pre-receive` 設為 repo 的 server-side hook
   （Forgejo 管理後台 → Repository → Git Hooks，內容寫 `exec /opt/gs-hooks/pre-receive`；或在容器內把它連結到
   `/data/git/repositories/gs/gs-strategy.git/hooks/pre-receive.d/`）。
8. **分支保護**：`main` 禁止 force push、禁止刪除，只允許 push 白名單。
9. **首次匯入**：GitHub 當權威，`git push --mirror` 到 `ssh://git@gsinvest:2223/gs/gs-strategy.git`。
   注意：匯入 `main` 的第一次推送 `old = 0000…`，目前 hook 對此情況只跑 append-only、不驗登記（見待做）。
10. **備份通道**（spec §7）：jsonhuang 端建 `/backup/gs-strategy/`；gsinvest 端 cron／user-systemd timer 每日
    `docker compose exec forgejo git -C <repo> bundle create - --all | ssh jsonhuang 'cat > …'`。備份內容不得含 TEJ 金鑰。
11. **還原演練**：在 gsinvest 起空 volume，從 jsonhuang 的 bundle `git clone`，確認 `main` HEAD 一致。
12. **DNS／Cloudflare Access hostname／內網轉埠**：規格 §11 指定由你處理，我不動。

## 二、驗證

本機（不需 Forgejo）：`pytest deploy/gsinvest-forgejo/hooks/test_pre_receive.py` ——用暫時 bare repo 驗：
刪 main、force push、改既有登記檔、author 與推送者不符，皆被拒；一般 commit 與非 main ref 放行。

## 三、待做／未驗證

- **規則 4**（同作者進行中登記 ≤ 3）與 **§5.3 簽核檢查**（signer ∈ signers、signer ≠ author、sha256 相符、單一 signoff）**尚未實作**。
- 「合法登記檔被放行」的正向案例尚無測試；`planned_trials`、`open_mining` 配額規則尚未接上。
- 容器內 `apk` 的 numpy／scipy／pandas 版本能否讓 `strategies/_common/validation` import 成功，**未在容器內驗證**；
  首次 build 後請跑：`docker compose exec forgejo python3 -c "import sys; sys.path.insert(0,'/opt/gs-hooks/repo'); import strategies._common.validation.decision"`。
- Forgejo 單點（spec §10 風險）：掛掉時不能新登記，靠每日 mirror 復原。

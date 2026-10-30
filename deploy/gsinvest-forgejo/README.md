# gsinvest 的 Forgejo 部署（權威 bare repo + pre-receive）

對應 `docs/spec/host-migration-gsinvest.md` §5、§7、§11 階段 A。本目錄是部署材料；2026-10-30 已依下列步驟在 gsinvest 部署（現況見「部署狀態」）。

gsinvest 環境（2026-10-30）：docker 29.4 + compose v5.1、磁碟剩 6.5T、無 sudo、`:2222` 是 gsinvest 自己的 sshd
（所以本部署 SSH 用 `2223`）、同機另有 chipk／qd／vllm 等容器網路。

## 一、設定步驟（2026-10-30 已在 gsinvest 實際走過 1–9）

> 路徑與指令以 Windows PowerShell 為準。注意 PowerShell 裡的 `ssh` 可能是 **WSL 的 Linux ssh**（它的 `~/.ssh/config` 與 Windows 的不同），
> 需要時明確用 `C:\Windows\System32\OpenSSH\ssh.exe`。

1. **匯出固定版本的驗證腳本**（目前鎖定 `deed37166bd52d5b30882cb39b498e00b656dac7`；升級＝換 commit 後重做此步並重 build）：
   ```powershell
   New-Item -ItemType Directory -Force deploy\gsinvest-forgejo\pinned-repo | Out-Null
   git archive <commit> scripts strategies/_common/validation strategies/_common/__init__.py -o $env:TEMP\pinned.tar
   tar -xf $env:TEMP\pinned.tar -C deploy\gsinvest-forgejo\pinned-repo
   ```
   `strategies/` 是 namespace package（沒有 `strategies/__init__.py`），不要加進去。`pinned-repo/` 不進版控。
2. **複製到 gsinvest**（先匯出再傳，否則 build 時 `COPY pinned-repo/` 會失敗）：`scp -r deploy\gsinvest-forgejo gsinvest:~/gs-forgejo`
3. **起容器**（在 gsinvest）：`cd ~/gs-forgejo && docker compose up -d --build`，接著確認相依性：
   `docker compose exec forgejo python3 -c "import sys; sys.path.insert(0,'/opt/gs-hooks/repo'); import strategies._common.validation.decision; print('ok')"`
4. **建管理員**（在 gsinvest，首次）：
   `docker compose exec -u git forgejo forgejo admin user create --admin --username kevin --email <登記檔 author 要用的 email> --random-password`
   密碼只顯示一次，存進密碼管理器，不要貼進對話或 log。**email 必須與你在登記檔寫的 `author` 一致**（hook 用它比對推送者）。
5. **開 tunnel、建 org `gs` 與 repo `gs-strategy`**。Windows 版 ssh、背景執行：
   ```powershell
   Start-Process -WindowStyle Hidden C:\Windows\System32\OpenSSH\ssh.exe -ArgumentList '-N','-L','3302:127.0.0.1:3301','gsinvest'
   ```
   瀏覽器開 http://127.0.0.1:3302。**repo 要建在 org `gs` 底下**；若建在個人帳號底下，用 repo 設定 → Danger Zone → 轉移擁有權。
   repo 保持空的（不初始化 README）。用完關 tunnel：`Get-Process ssh | Stop-Process`。
6. **建使用者** joker、jason（`--random-password`，各自登入後改密碼並上傳 SSH 公鑰）。signers = kevin、joker；executor = jason。（**尚未做**）
7. **掛 pre-receive hook**（在 gsinvest；Forgejo 自動生成的 `hooks/pre-receive` 會依序執行 `pre-receive.d/*`，不要改它）：
   ```bash
   docker compose exec -T -u git forgejo ln -s /opt/gs-hooks/pre-receive /data/git/repositories/gs/gs-strategy.git/hooks/pre-receive.d/gs-prereg
   ```
8. **分支保護**：repo 為空時設定頁**沒有「分支」項目**，要等第一次推送之後（步驟 9）才會出現。規則 `main`：
   選「使用白名單控管推送」並加入使用者（Forgejo 沒有獨立的 force push 勾選：啟用推送或白名單後 force push 一律被擋，受保護分支也不能刪）。
9. **首次匯入**（GitHub 當權威；用全新 bare clone，不用 `--mirror`，避免推 `refs/pull/*`）：
   ```powershell
   git clone --bare https://github.com/GSINVEST/gs-strategy.git gs-strategy-import.git
   cd gs-strategy-import.git
   $env:GIT_SSH_COMMAND='C:/Windows/System32/OpenSSH/ssh.exe -o ProxyJump=gs-linux'
   git push ssh://git@gsinvest:2223/gs/gs-strategy.git 'refs/heads/*:refs/heads/*' --tags
   ```
   第一次推送 `main` 時 `old = 0000…`，hook 只跑 append-only、不驗登記（見待做）。匯入後可刪除該暫存 clone。
10. **備份通道**（spec §7）：jsonhuang 端建 `/backup/gs-strategy/`；gsinvest 端 cron／user-systemd timer 每日
    `docker compose exec forgejo git -C <repo> bundle create - --all | ssh jsonhuang 'cat > …'`。備份內容不得含 TEJ 金鑰。
11. **還原演練**：在 gsinvest 起空 volume，從 jsonhuang 的 bundle `git clone`，確認 `main` HEAD 一致。
12. **DNS／Cloudflare Access hostname／內網轉埠**：規格 §11 指定由你處理，我不動。

## 部署狀態（2026-10-30）

- gsinvest 上 `gs-forgejo` 已起（`127.0.0.1:3301` HTTP、`2223` SSH），`gs/gs-strategy` 已匯入（37 分支、13 tag，`main` 與 GitHub 一致）。
- hook 以符號連結掛在 `pre-receive.d/gs-prereg`；容器內直接餵假資料驗過「刪 main 被拒、非 main ref 放行」。
- `main` 已設白名單分支保護（目前白名單只有 kevin）。
- **尚未實測**：真實推送時 Forgejo 是否呼叫 hook 並帶入 `GITEA_PUSHER_EMAIL`（建議用拋棄式 repo 測，不要拿 `gs-strategy` 的 `main`）。
- **尚未做**：joker／jason 帳號、備份通道（步驟 10）、還原演練（步驟 11）、DNS／CF Access（步驟 12）。

## 二、驗證

本機（不需 Forgejo）：`pytest deploy/gsinvest-forgejo/hooks/test_pre_receive.py` ——用暫時 bare repo 驗：
刪 main、force push、改既有登記檔、author 與推送者不符，皆被拒；一般 commit 與非 main ref 放行。

## 三、待做／未驗證

- **規則 4**（同作者進行中登記 ≤ 3）與 **§5.3 簽核檢查**（signer ∈ signers、signer ≠ author、sha256 相符、單一 signoff）**尚未實作**。
- 「合法登記檔被放行」的正向案例尚無測試；`planned_trials`、`open_mining` 配額規則尚未接上。
- 容器內 `apk` 的 numpy／scipy／pandas 版本能否讓 `strategies/_common/validation` import 成功，**未在容器內驗證**；
  首次 build 後請跑：`docker compose exec forgejo python3 -c "import sys; sys.path.insert(0,'/opt/gs-hooks/repo'); import strategies._common.validation.decision"`。
- Forgejo 單點（spec §10 風險）：掛掉時不能新登記，靠每日 mirror 復原。

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
10. **備份通道**（2026-10-30 已建，檔案在 `backup/`）：gsinvest 的 user-systemd timer 每日 03:30 執行 `gs-forgejo-backup.sh`，
    把 Forgejo 的 `gs/gs-strategy` 打成 `git bundle`，經專用 write-only 金鑰（`~/.ssh/id_gsbackup`）送到 jsonhuang `/backup/gs-strategy/`。
    jsonhuang 端 `authorized_keys` 該金鑰加 `restrict,command="/home/jsonhuang/bin/gs-backup-receive.sh"`：只能把 stdin 存成帶時間戳的新檔
    （先 `git bundle verify`，不覆蓋既有檔）。備份內容不含 TEJ 金鑰。安裝：把 `backup/` 的 `.service`/`.timer` 放到 `~/.config/systemd/user/`，
    `systemctl --user enable --now gs-forgejo-backup.timer`。
11. **還原演練**：在 gsinvest 起空 volume，從 jsonhuang 的 bundle `git clone`，確認 `main` HEAD 一致。
12. **DNS／Cloudflare Access hostname／內網轉埠**：規格 §11 指定由你處理，我不動。

## 部署狀態（2026-10-30）

- gsinvest 上 `gs-forgejo` 已起（`127.0.0.1:3301` HTTP、`2223` SSH），`gs/gs-strategy` 已匯入（37 分支、13 tag，`main` 與 GitHub 一致）。
- hook 以符號連結掛在 `pre-receive.d/gs-prereg`；容器內直接餵假資料驗過「刪 main 被拒、非 main ref 放行」。
- `main` 已設白名單分支保護（目前白名單只有 kevin）。
- **尚未實測**：真實推送時 Forgejo 是否呼叫 hook 並帶入 `GITEA_PUSHER_EMAIL`（建議用拋棄式 repo 測，不要拿 `gs-strategy` 的 `main`）。
- 備份通道已建並實跑一次成功；還原演練已做（bundle 還原後 `origin/main` 與 Forgejo 一致、fsck 通過）。
- **尚未做**：joker／jason 帳號、備份保留／輪替策略、DNS／CF Access（步驟 12）。

## GitHub → Forgejo 單向同步（2026-10-30）

GitHub 是程式碼的權威。gsinvest 的 user-systemd timer 每 15 分鐘執行 `sync/gs-forgejo-sync.sh`，在 Forgejo 容器內以 **read-only deploy key**
對 `GSINVEST/gs-strategy` 做 fetch：`main` 只允許 fast-forward（GitHub 的 main 被改寫時會失敗並留下紀錄，不靜默覆蓋）；其他分支與 GitHub 對齊；不刪分支；tag 只新增。
此同步刻意繞過 pre-receive（管理員同步）。不使用 Forgejo 內建 pull mirror，因為它會讓 repo 唯讀，使登記推送與 hook 失去意義。

設定：容器內 `/data/gs-sync/`（0700，git 擁有）放 `id_deploy`、`known_hosts`；公鑰以 **Deploy key（不勾 write）** 加到 **GitHub** 的 `GSINVEST/gs-strategy`
（注意不是 Forgejo 自己的部署金鑰頁）。安裝 `.service`/`.timer` 到 `~/.config/systemd/user/` 後 `systemctl --user enable --now gs-forgejo-sync.timer`。

已知限制：繞過 hook 的同步不會觸發 Forgejo 的 post-receive，網頁分支清單可能落後 git 實際 refs（內容與 `main` 正確）；重啟 Forgejo 或其內建分支同步可補齊。

## 備份保留策略（spec §7：永久保存、不輪出）

規格明定每日快照**永久保存**，所以不做輪替與刪除。用量：每個 bundle 約 2.3MB，一年約 0.8GB，jsonhuang 剩 6.4T，可以忽略。
jsonhuang 的接收腳本只新增檔案、不覆蓋，write-only 金鑰也無法刪除既有備份。真要清理，只能由人在 jsonhuang 上手動執行。

## 每週加密離線備份（腳本已備，**尚未啟用**）

`backup/gs-weekly-offline.sh`（在 jsonhuang 上跑）：每週日 04:30 把 `/backup/gs-strategy` 打包，用 `age` **公鑰**加密，`scp` 到離線備份機（public-pc）並附 sha256。
私鑰只由 Kevin 離線保管，任何伺服器都不持有。啟用前需要你提供／確認三樣東西：

1. **age 金鑰對**：在你信任的電腦自己產生（`age-keygen -o gs-backup.key`），私鑰離線保存；只把 `age1…` 公鑰給我或寫進 jsonhuang 的設定。**不要把私鑰貼進對話。**
2. **離線機目的地**：`public-pc`（192.168.11.120，Windows）的 F 槽路徑，以及 jsonhuang 到 public-pc 的 SSH 金鑰（建議另建專用金鑰、只放該目錄）。
3. **jsonhuang 安裝 age**：`sudo apt install age`（jsonhuang 有免密 sudo）。

設定檔 `~/.config/gs-weekly-offline.env`（0600，不進 repo）：
```
AGE_RECIPIENT=age1...
OFFLINE_DEST=public-pc:/F:/gs-backup/
```
安裝：腳本放 `~/bin/`，`.service`／`.timer` 放 `~/.config/systemd/user/`，`systemctl --user enable --now gs-weekly-offline.timer`。
**還原要點**：用私鑰 `age -d -i gs-backup.key gs-strategy-YYYY-Www.tar.age | tar -x`，再對 bundle 做 `git clone`；請在啟用後做一次實際解密演練。

## DNS 與 Cloudflare Access（**步驟，尚未執行**）

這一塊屬於**研究服務（StratyUI）上線**，不是 Forgejo：Forgejo 目前只綁 `127.0.0.1:3301` 與內網 SSH `2223`，沒有對外。規格 §8.2 的限制：
**Cloudflare API token 只放在 dgx-a4d7，不得帶到其他機器**；gsinvest 只放 tunnel 的 connector token。所以以下由你在 dgx-a4d7（或 Cloudflare 網頁）操作：

1. **內網 DNS**：`strategy.gsinvest.com` 指向 gs-linux（wiki-caddy 所在，OIDC），並在 gs-linux 設轉埠到 gsinvest 的 app 埠（app 容器上線後才有）。
2. **外網 hostname**：另取一個不同的名稱（例如 `strategy-ext.gsinvest.com`，spec §13 Q3），避免同名雙路徑造成 Host／Origin 檢查混淆。
3. **Tunnel**：在 dgx-a4d7 以 API token 建 tunnel，取得 connector token，只把 connector token 交給 gsinvest 的 `cloudflared` 容器。
4. **Access application**：外網 hostname 建 application，policy 只含三位使用者 email（One-time PIN），session 24 小時。
5. **app 端第二層驗證**：app 驗 `Cf-Access-Jwt-Assertion`（RS256、aud、iss、exp），email 必須在 `roles.yaml` 才有寫入權；`--public-host` 同時列兩個 hostname。
6. **驗收**：未登入者看不到；email 通過但不在 roles 名單者唯讀；兩條入口各測一次。

**前置條件**：app 容器（`gs-strategy-app`）尚未建置，這幾步目前沒有可以指向的服務。

## 二、驗證

本機（不需 Forgejo）：`pytest deploy/gsinvest-forgejo/hooks/test_pre_receive.py` ——用暫時 bare repo 驗：
刪 main、force push、改既有登記檔、author 與推送者不符，皆被拒；一般 commit 與非 main ref 放行。

## 三、待做／未驗證

- **規則 4**（同作者進行中登記 ≤ 3）與 **§5.3 簽核檢查**（signer ∈ signers、signer ≠ author、sha256 相符、單一 signoff）**尚未實作**。
- 「合法登記檔被放行」的正向案例尚無測試；`planned_trials`、`open_mining` 配額規則尚未接上。
- 容器內 `apk` 的 numpy／scipy／pandas 版本能否讓 `strategies/_common/validation` import 成功，**未在容器內驗證**；
  首次 build 後請跑：`docker compose exec forgejo python3 -c "import sys; sys.path.insert(0,'/opt/gs-hooks/repo'); import strategies._common.validation.decision"`。
- Forgejo 單點（spec §10 風險）：掛掉時不能新登記，靠每日 mirror 復原。

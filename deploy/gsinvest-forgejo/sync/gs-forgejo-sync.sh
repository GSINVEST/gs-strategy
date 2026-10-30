#!/bin/bash
# 把 GitHub（程式碼權威）的 refs 單向同步進 Forgejo 的 bare repo（read-only deploy key）。
# - main：只允許 fast-forward；若 GitHub 的 main 被改寫，fetch 失敗並留下紀錄，不靜默覆蓋。
# - 其他分支：與 GitHub 對齊（可強制更新）；不刪除分支；tag 只新增。
# 刻意繞過 pre-receive（管理員同步）。
set -euo pipefail
cd "$HOME/gs-forgejo"
docker compose exec -T -u git forgejo sh -ec '
  cd /data/git/repositories/gs/gs-strategy.git
  export GIT_SSH_COMMAND="ssh -i /data/gs-sync/id_deploy -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile=/data/gs-sync/known_hosts -o StrictHostKeyChecking=yes"
  U=git@github.com:GSINVEST/gs-strategy.git
  git fetch --quiet "$U" refs/heads/main:refs/heads/main
  git fetch --quiet "$U" "+refs/heads/*:refs/heads/*" "^refs/heads/main"
  git fetch --quiet --no-tags "$U" "refs/tags/*:refs/tags/*" || true
  echo "sync ok main=$(git rev-parse main)"
'

#!/bin/bash
# 每日把 Forgejo 的 gs/gs-strategy 打成 git bundle，經 write-only key 送到 jsonhuang（spec §7）。
set -euo pipefail
cd "$HOME/gs-forgejo"
docker compose exec -T -u git forgejo git -C /data/git/repositories/gs/gs-strategy.git bundle create - --all \
  | ssh -i "$HOME/.ssh/id_gsbackup" -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts_gsbackup" \
        -o StrictHostKeyChecking=yes jsonhuang@192.168.0.73

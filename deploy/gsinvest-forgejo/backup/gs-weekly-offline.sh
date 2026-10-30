#!/bin/bash
# 每週把 jsonhuang 的 /backup/gs-strategy 打包，用 age 公鑰加密後送到離線備份機（spec §7「離線」列）。
# 在 jsonhuang 上執行。只用公鑰：私鑰由 Kevin 離線保管，任何伺服器都不得持有。
#
# 需要先設定（見 README「每週加密離線備份」）：
#   AGE_RECIPIENT   age 公鑰（age1...），放在 ~/.config/gs-weekly-offline.env
#   OFFLINE_DEST    scp 目的地，例如 public-pc:/F:/gs-backup/（Windows OpenSSH 路徑）
set -euo pipefail
ENV_FILE="${GS_WEEKLY_ENV:-$HOME/.config/gs-weekly-offline.env}"
[ -r "$ENV_FILE" ] && . "$ENV_FILE"
: "${AGE_RECIPIENT:?未設定 AGE_RECIPIENT（age 公鑰）}"
: "${OFFLINE_DEST:?未設定 OFFLINE_DEST}"
case "$AGE_RECIPIENT" in age1*) ;; *) echo "AGE_RECIPIENT 必須是公鑰（age1…）" >&2; exit 2;; esac

out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT
name="gs-strategy-$(date +%G-W%V).tar.age"
tar -C /backup -cf - gs-strategy | age -r "$AGE_RECIPIENT" -o "$out/$name"
sha256sum "$out/$name" | awk '{print $1}' > "$out/$name.sha256"
scp -q "$out/$name" "$out/$name.sha256" "$OFFLINE_DEST"
echo "ok $name $(stat -c %s "$out/$name") bytes sha256=$(cat "$out/$name.sha256")"

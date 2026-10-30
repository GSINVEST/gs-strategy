#!/usr/bin/env bash
# dry run 告警通知範例（Slack Incoming Webhook）。告警訊息從 stdin 進來，由 ops.py 呼叫。
#
# 安裝（在跑排程的 WSL 主機上，一次性）：
#   mkdir -p ~/.config/gs-dryrun
#   cp scripts/dryrun_notify_slack.example.sh ~/.config/gs-dryrun/notify && chmod 700 ~/.config/gs-dryrun/notify
#   (umask 077; read -rs -p "webhook URL: " u && printf '%s' "$u" > ~/.config/gs-dryrun/slack_webhook)
#
# dryrun_run_daily.sh 偵測到 ~/.config/gs-dryrun/notify 可執行時，會自動把它設成 DRYRUN_ALERT_CMD。
# webhook URL 是機密：只放在上面那個 600 權限的檔案，不進 repo、不進 cron 行、不印出來。
# 這個腳本在主機執行（不在容器內），容器的出口白名單不受影響。
set -eu
hook_file="${DRYRUN_SLACK_WEBHOOK_FILE:-$HOME/.config/gs-dryrun/slack_webhook}"
[ -r "$hook_file" ] || { echo "dryrun notify: 找不到 $hook_file" >&2; exit 1; }
payload="$(python3 -c 'import json,sys; print(json.dumps({"text": "[gs-strategy dry run]\n" + sys.stdin.read()}))')"
curl -fsS --max-time 20 -X POST -H 'Content-Type: application/json' --data "$payload" "$(cat "$hook_file")" >/dev/null

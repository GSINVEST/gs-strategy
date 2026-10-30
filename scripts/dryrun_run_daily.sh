#!/usr/bin/env bash
# cron 進入點：在隔離容器內為每支策略跑一次「最近已收盤的交易日」。
# 需要環境變數 DRYRUN_REPO、DRYRUN_ENV_FILE、ZIPLINE_DATA（由 dryrun_schedule.sh 寫進 cron）。
set -u
: "${DRYRUN_REPO:?}" "${DRYRUN_ENV_FILE:?}" "${ZIPLINE_DATA:?}"
export DRYRUN_REPO DRYRUN_ENV_FILE ZIPLINE_DATA
export DRYRUN_LEDGER_DIR="${DRYRUN_LEDGER_DIR:-$HOME/.local/share/gs-dryrun}"   # 名稱含 dry（自檢要求）
export DRYRUN_UID="$(id -u)" DRYRUN_GID="$(id -g)"
mkdir -p "$DRYRUN_LEDGER_DIR"
STRATEGIES="${DRYRUN_STRATEGIES:-vgrsi_tx tsmom_tx_mtx cubic_momentum_tx}"

cd "$DRYRUN_REPO/dockerfile/dryrun" || exit 1
echo "== $(date -Is) dryrun daily =="
rc=0
if docker compose up -d egress-proxy; then
for s in $STRATEGIES; do
  # 尚未啟動（沒有 promoted 記錄）的策略會失敗，記錄後繼續下一支
  docker compose run --rm dryrun run --strategy-id "$s" || { echo "FAILED: $s"; rc=1; }
done
else
  echo "FAILED: egress-proxy 起不來"; rc=1
fi

# 主機端營運檢查（標準函式庫，不連網）：先備份再檢查健康；失敗只告警，不影響已寫入的帳本
LEDGER="$DRYRUN_LEDGER_DIR/dry_ledger.sqlite"
BACKUP_DIR="${DRYRUN_BACKUP_DIR:-$HOME/.local/share/gs-dryrun-backup}"
ALERT_LOG="${DRYRUN_ALERT_LOG:-$HOME/.local/state/gs-dryrun/alerts.log}"
# 通知通道：呼叫端沒指定 DRYRUN_ALERT_CMD 時，若 ~/.config/gs-dryrun/notify 可執行就用它（範例見 dryrun_notify_slack.example.sh）
if [ -z "${DRYRUN_ALERT_CMD:-}" ] && [ -x "$HOME/.config/gs-dryrun/notify" ]; then
  export DRYRUN_ALERT_CMD="$HOME/.config/gs-dryrun/notify"
fi
cd "$DRYRUN_REPO" || exit 1
python3 -m strategies._common.dryrun.ops backup --ledger "$LEDGER" --dest "$BACKUP_DIR" || rc=2
python3 -m strategies._common.dryrun.ops health --ledger "$LEDGER" --alert-log "$ALERT_LOG"
hrc=$?
[ "$hrc" -gt "$rc" ] && rc=$hrc

# 快照（給 gateway 的面板用）：產生後推送；未設定 push_url／push_key 時只警告。推送失敗併入結束碼
SNAP="$(mktemp)"; trap 'rm -f "$SNAP"' EXIT
if python3 -m strategies._common.dryrun.snapshot --ledger "$LEDGER" --strategies-dir "$DRYRUN_REPO/strategies" --out "$SNAP"; then
  python3 -m strategies._common.dryrun.push --file "$SNAP"
  prc=$?
else
  echo "[CRIT] 快照產生失敗"; prc=2
fi
[ "$prc" -gt "$rc" ] && rc=$prc
exit $rc

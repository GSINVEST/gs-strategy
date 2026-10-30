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
docker compose up -d egress-proxy || exit 1
rc=0
for s in $STRATEGIES; do
  # 尚未啟動（沒有 promoted 記錄）的策略會失敗，記錄後繼續下一支
  docker compose run --rm dryrun run --strategy-id "$s" || { echo "FAILED: $s"; rc=1; }
done
exit $rc

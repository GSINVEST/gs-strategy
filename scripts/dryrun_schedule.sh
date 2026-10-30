#!/usr/bin/env bash
# 安裝／移除 dry run 的 WSL cron。預設只印出將變更的內容（dry-run）；加 --apply 才真的改 crontab。
#
#   scripts/dryrun_schedule.sh install [--repo DIR] [--env-file F] [--zipline-data DIR] [--apply]
#   scripts/dryrun_schedule.sh remove [--apply]
#
# 排程時間：週二至週六 07:30（台北時間，WSL 時區須為 Asia/Taipei）。
# 為什麼不是收盤後：bundle 由 Windows 工作排程「gs-zipline-tej-bundle-daily」每天 06:00 更新，
# 當天收盤的資料要到隔天早上才進 bundle；收盤後立刻跑只會把當天標成 MISSING。
set -euo pipefail

BEGIN="# >>> gs-strategy dryrun >>>"
END="# <<< gs-strategy dryrun <<<"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DEFAULT="$(dirname "$SELF_DIR")"

cmd="${1:-}"; shift || true
repo="$REPO_DEFAULT"; env_file="$HOME/.config/gs-dryrun/env"; zdata="$HOME/.zipline/data"; ledger_dir="$HOME/.local/share/gs-dryrun"; apply=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="$2"; shift 2 ;;
    --env-file) env_file="$2"; shift 2 ;;
    --zipline-data) zdata="$2"; shift 2 ;;
    --ledger-dir) ledger_dir="$2"; shift 2 ;;
    --apply) apply=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

block() {
  cat <<EOF
$BEGIN
# 由 scripts/dryrun_schedule.sh 安裝；移除請用 scripts/dryrun_schedule.sh remove --apply
30 7 * * 2-6 DRYRUN_REPO=$repo DRYRUN_ENV_FILE=$env_file ZIPLINE_DATA=$zdata DRYRUN_LEDGER_DIR=$ledger_dir $repo/scripts/dryrun_run_daily.sh >> \$HOME/.local/state/gs-dryrun/cron.log 2>&1
$END
EOF
}

current() { crontab -l 2>/dev/null || true; }
strip()   { awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1} !skip{print} $0==e{skip=0}'; }

case "$cmd" in
  install) new="$( { current | strip; block; } )" ;;
  remove)  new="$(current | strip)" ;;
  *) echo "usage: $0 install|remove [--apply]" >&2; exit 2 ;;
esac

if [ "$apply" -eq 1 ]; then
  [ "$cmd" = install ] && mkdir -p "$HOME/.local/state/gs-dryrun"
  printf '%s\n' "$new" | crontab -
  echo "applied: crontab 已更新（$cmd）"
else
  echo "== dry-run（未改動 crontab）；加 --apply 才會生效 =="
  if [ "$cmd" = install ]; then echo "將安裝："; block; else echo "將移除 dryrun 區塊；其餘 crontab 內容保留。"; fi
fi

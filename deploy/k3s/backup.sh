#!/usr/bin/env bash
# 策略圖資料的每日快照（在 jsonhuang 主機上跑，不在 pod 內）。保留 14 份。
# 範圍：ledger（log/trials.jsonl，統計誠信的依據）、結果庫（data/backtests.sqlite）、.graph-runs。
# 限制：只防誤操作／壞檔，不防整顆碟損毀；異地複本尚未建立（jsonhuang 目前連不到 gsinvest 的 SSH）。
# 安裝（jsonhuang 使用者 crontab）：20 3 * * * /srv/gs-data/strategy-graph-backup/backup.sh
set -euo pipefail
SRC=/srv/gs-data/strategy-graph
DST=/srv/gs-data/strategy-graph-backup
KEEP=14
mkdir -p "$DST"
STAMP="$(date +%Y%m%d-%H%M%S)"
TMP="$(mktemp -d "$DST/.tmp.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/data" "$TMP/log"
if command -v sqlite3 >/dev/null 2>&1 && [ -f "$SRC/data/backtests.sqlite" ]; then
  sqlite3 "$SRC/data/backtests.sqlite" ".backup '$TMP/data/backtests.sqlite'"
else
  cp "$SRC/data/backtests.sqlite" "$TMP/data/" 2>/dev/null || true
fi
cp "$SRC/log/trials.jsonl" "$TMP/log/" 2>/dev/null || true
cp -r "$SRC/graph-runs" "$TMP/graph-runs" 2>/dev/null || true
( cd "$TMP" && sha256sum log/trials.jsonl 2>/dev/null > SHA256SUMS; wc -l < log/trials.jsonl > LEDGER_LINES )
tar czf "$DST/strategy-graph-$STAMP.tgz" -C "$TMP" .
ls -1t "$DST"/strategy-graph-*.tgz | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "$(date -Is) backup ok: strategy-graph-$STAMP.tgz ledger_lines=$(cat "$TMP/LEDGER_LINES")"

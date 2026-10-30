#!/usr/bin/env bash
# 實跑驗證容器層網路出口封鎖：需要 docker（WSL／Linux）與已建好的 gs-dryrun:local。
#   bash dockerfile/dryrun/verify-egress.sh
# 用的是假的 TEJAPI_KEY（探針不呼叫 TEJ API，只測 TLS 隧道能否建立），不會碰真實金鑰。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DRYRUN_REPO="${DRYRUN_REPO:-$(cd "$HERE/../.." && pwd)}"
export ZIPLINE_DATA="${ZIPLINE_DATA:-$(mktemp -d)}"
export DRYRUN_UID="$(id -u)" DRYRUN_GID="$(id -g)"
export DRYRUN_LEDGER_DIR="$(mktemp -d)"
envf="$(mktemp)"; echo "TEJAPI_KEY=probe-not-a-real-key" > "$envf"
export DRYRUN_ENV_FILE="$envf"
trap 'rm -f "$envf"; rmdir "$DRYRUN_LEDGER_DIR" 2>/dev/null || true' EXIT

cd "$HERE"
docker compose up -d --build egress-proxy
docker compose run --rm --entrypoint python dryrun /app/dockerfile/dryrun/egress_probe.py

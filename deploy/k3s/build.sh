#!/usr/bin/env bash
# 建映像並（可選）匯入 jsonhuang 的 k3s。用法（在 gs-strategy 根目錄）：
#   bash deploy/k3s/build.sh            # 只在本機建
#   IMPORT=1 bash deploy/k3s/build.sh   # 建完 docker save | ssh jsonhuang k3s ctr images import
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SHA="$(git -C "$ROOT" rev-parse --short HEAD)"
IMG="gs/strategy-graph:$SHA${TAG_SUFFIX:-}"
cp "$ROOT/deploy/k3s/.dockerignore" "$ROOT/.dockerignore"
trap 'rm -f "$ROOT/.dockerignore"' EXIT
docker build -t "$IMG" -f "$ROOT/deploy/k3s/Dockerfile" "$ROOT"
if [ "${IMPORT:-0}" = 1 ]; then
  docker save "$IMG" | gzip | ssh jsonhuang "gunzip | sudo k3s ctr images import -"
fi
echo "映像 $IMG；manifest 內的 image tag 要同步"

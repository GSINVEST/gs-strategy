#!/bin/bash
# forced command：只接收 stdin 的 git bundle，寫成帶日期的新檔；不覆蓋既有檔、不執行任何其他指令。
set -euo pipefail
d=/backup/gs-strategy
f="$d/gs-strategy-$(date +%F-%H%M%S).bundle"
umask 027
cat > "$f.part"
GIT_DIR=/backup/.verify.git git bundle verify "$f.part" >/dev/null 2>&1 || { rm -f "$f.part"; echo "bundle 驗證失敗" >&2; exit 1; }
mv -n "$f.part" "$f"
echo "ok $(basename "$f") $(stat -c %s "$f") bytes"

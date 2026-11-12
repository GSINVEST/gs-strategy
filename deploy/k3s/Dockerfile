# 策略圖（Live Strategy Graph / StratyUI）on k3s。build context = gs-strategy 的 checkout 根目錄，見 build.sh。
# 資料（data/ .graph-runs/ log/ ~/.zipline）與 gs-zipline-tej 一律掛 hostPath，不進映像；金鑰走 k8s Secret。

# --- 前端：在容器內建置，不再依賴 Windows 端手動建置後複製 dist ---
FROM node:22-slim AS ui
WORKDIR /ui
COPY strategies/_common/graph/ui/package.json strategies/_common/graph/ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY strategies/_common/graph/ui/ ./
RUN npx vite build

# --- 執行環境 ---
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 TZ=Asia/Taipei \
    PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# 圖伺服器本體所需套件（requirements.txt＋test 清單中的科學計算套件）
COPY requirements.txt ./
RUN pip install -r requirements.txt "pandas>=2.2,<3.0" "numpy>=2.0,<3.0" "scipy>=1.11" \
    "statsmodels>=0.14" "pyarrow>=15" "pyyaml>=6.0" "jsonschema>=4"

# 回測用 .venv-bt（pool.py 會自動找 /app/.venv-bt/bin/python）。層級獨立，zipline-tej 變動才重建。
COPY requirements-bt.txt ./
RUN python -m venv /app/.venv-bt && /app/.venv-bt/bin/pip install -r requirements-bt.txt

COPY . .
COPY --from=ui /ui/dist /app/strategies/_common/graph/ui/dist
RUN useradd -u 10001 -m app && mkdir -p /app/data /app/.graph-runs /app/.cache /app/log && chown 10001 /app/data /app/.graph-runs /app/.cache /app/log
USER 10001

EXPOSE 9112
# --bind 0.0.0.0 只在容器內使用：前面一定要有 OIDC 反向代理（見 manifest 註解）。
CMD ["python", "-m", "strategies._common.graph", "ui", "--graph", "strategies/llm_view_tx/graph.json", "--port", "9112", "--bind", "0.0.0.0", "--public-host", "strategy.gsinvest.com"]

# dry run 執行容器：沒有券商憑證、沒有主機掛載（除了唯讀 repo／bundle 與帳本卷）、
# 只能經 egress-proxy 連 TEJ。見 docs/spec/dryrun-deploy.md。
#
# 建置（zipline-tej 原始碼以具名 context 傳入，固定在 fork 的某個 commit）：
#   docker build -f dockerfile/dryrun/Dockerfile --build-context zl=<zipline-tej 乾淨原始碼目錄> -t gs-dryrun:local .
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc g++ make \
 && rm -rf /var/lib/apt/lists/*

COPY dockerfile/dryrun/requirements.lock /tmp/requirements.lock
COPY --from=zl . /tmp/zl
# 版本鎖定來自已驗證可跑的環境（pandas 2.2.3、sqlalchemy<2 等限制見 docs/experiments）
RUN pip install --no-cache-dir -r /tmp/requirements.lock
# 乾淨原始碼沒有 .git，setuptools-scm 取不到版本；與已驗證環境一致用 0.0.0
# （所以框架版本識別靠 zl_adapter 對安裝的程式碼算雜湊，不靠這個版本號）
RUN SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ZIPLINE_TEJ=0.0.0 pip install --no-cache-dir --no-deps /tmp/zl  && rm -rf /tmp/zl /tmp/requirements.lock  && apt-get purge -y gcc g++ make && apt-get autoremove -y

RUN useradd --create-home --uid 10001 dry
USER dry
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ZIPLINE_ROOT=/zipline \
    PYTHONPATH=/app

ENTRYPOINT ["python", "-m", "strategies._common.dryrun"]

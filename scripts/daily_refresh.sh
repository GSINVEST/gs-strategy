#!/usr/bin/env bash
# Daily pipeline: refresh papers + auto-generate strategy bundles.
#
# Steps (all idempotent — safe to re-run on the same day):
#   1. quant-crawl run --log-run                 # update data/papers.db
#   2. python -m quant_crawler.strategy_gen.generate --since YYYY-MM-DD
#                                                  # emit skeleton bundles
#                                                  # under strategies/_generated/
#   3. validate every newly-generated bundle      # halt-on-failure
#   4. write a summary line to data/logs/daily_refresh.log
#
# Designed to run as a cron job; see scripts/install_daily_refresh.sh.
#
# Exit codes:
#   0  success
#   1  unexpected error in step 1-3
#   2  no .env or missing TEJAPI_KEY
#   3  one or more generated bundles failed validation
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

LOG_DIR="${ROOT_DIR}/data/logs"
mkdir -p "${LOG_DIR}"
TODAY="$(date -u +%Y-%m-%d)"
RUN_LOG="${LOG_DIR}/daily_refresh_${TODAY}.log"
SUMMARY_LOG="${LOG_DIR}/daily_refresh.log"

log() {
    # to both stdout (cron captures) and the run log
    printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "${RUN_LOG}"
}

# .env is required for TEJAPI_KEY which is import-time mandatory for zipline.
if [[ ! -f "${ROOT_DIR}/.env" ]]; then
    log "ERROR: ${ROOT_DIR}/.env not found"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FAIL no-env" >> "${SUMMARY_LOG}"
    exit 2
fi
# shellcheck disable=SC1091
set -a; source "${ROOT_DIR}/.env"; set +a
if [[ -z "${TEJAPI_KEY:-}" ]]; then
    log "ERROR: TEJAPI_KEY not set in .env"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FAIL no-tejkey" >> "${SUMMARY_LOG}"
    exit 2
fi

VENV_CRAWL="${ROOT_DIR}/.venv/bin/python"
VENV_BT="${ROOT_DIR}/.venv-bt/bin/python"

log "=== daily_refresh START ==="
log "repo=${ROOT_DIR}  log=${RUN_LOG}"

# Pick the cutoff for new papers — fetched_at >= yesterday (UTC). Cron runs
# at 06:00 local; using "yesterday" UTC means we catch everything since the
# previous run regardless of cron drift.
SINCE_DATE="$(date -u -d 'yesterday' +%Y-%m-%d 2>/dev/null \
             || date -u -v-1d +%Y-%m-%d)"
log "selecting papers fetched_at >= ${SINCE_DATE}"

# --- Step 1: refresh papers.db ---------------------------------------------
log "[step 1/4] quant-crawl run"
if ! "${VENV_CRAWL}" -m quant_crawler.cli run --log-run >> "${RUN_LOG}" 2>&1; then
    log "ERROR: quant-crawl exited non-zero"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FAIL crawl" >> "${SUMMARY_LOG}"
    exit 1
fi

# --- Step 2: download new PDFs to data/pdfs/ (best-effort) -----------------
# Non-fatal: a PDF host being down must not fail the whole refresh.
log "[step 2/4] quant-crawl fetch-pdfs"
if ! "${VENV_CRAWL}" -m quant_crawler.cli fetch-pdfs >> "${RUN_LOG}" 2>&1; then
    log "WARN: fetch-pdfs exited non-zero (continuing)"
fi

# --- Step 2b: index new PDFs into the RAG store (best-effort) --------------
log "[step 2b] quant-crawl rag-ingest"
if ! "${VENV_CRAWL}" -m quant_crawler.cli rag-ingest >> "${RUN_LOG}" 2>&1; then
    log "WARN: rag-ingest exited non-zero (continuing)"
fi

# --- Step 3: emit skeleton bundles -----------------------------------------
log "[step 3/4] strategy_gen.generate --since ${SINCE_DATE}"
GEN_OUT="${LOG_DIR}/daily_refresh_gen_${TODAY}.out"
if ! "${VENV_CRAWL}" -m quant_crawler.strategy_gen \
        --since "${SINCE_DATE}" \
        > "${GEN_OUT}" 2>> "${RUN_LOG}"; then
    log "ERROR: strategy_gen.generate exited non-zero (see ${GEN_OUT})"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FAIL strategy-gen" >> "${SUMMARY_LOG}"
    exit 1
fi
cat "${GEN_OUT}" >> "${RUN_LOG}"
BUNDLES_EMITTED=$(grep -c '\[OUT\]' "${GEN_OUT}" || true)
log "emitted ${BUNDLES_EMITTED} bundle(s)"

# --- Step 4: validate every generated bundle -------------------------------
log "[step 4/4] validate strategies/_generated/*"
shopt -s nullglob
GEN_DIRS=("${ROOT_DIR}"/strategies/_generated/*/)
shopt -u nullglob
if [[ ${#GEN_DIRS[@]} -eq 0 ]]; then
    log "no generated bundles to validate (probably first run / no new papers)"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) OK emitted=${BUNDLES_EMITTED} validated=0" >> "${SUMMARY_LOG}"
    log "=== daily_refresh DONE (no bundles) ==="
    exit 0
fi
if ! "${VENV_BT}" "${ROOT_DIR}/scripts/validate_dashboard_bundle.py" \
        "${GEN_DIRS[@]}" >> "${RUN_LOG}" 2>&1; then
    log "ERROR: one or more bundles failed validation"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FAIL validate (see ${RUN_LOG})" >> "${SUMMARY_LOG}"
    exit 3
fi

# --- Step 5: LLM codegen from papers (best effort, never fails the run) -----
# Writes gated bundles to strategies/_llm_generated/ and a codegen experiment to
# data/backtests.sqlite. Skipped when no LLM key is configured.
CODEGEN_LIMIT="${CODEGEN_LIMIT:-3}"
CODEGEN_MODEL="${CODEGEN_MODEL:-qwen3-235b-2507}"
if [[ "${CODEGEN_LIMIT}" -gt 0 ]]; then
    log "[step 5] llm_codegen --limit ${CODEGEN_LIMIT} --model ${CODEGEN_MODEL}"
    if ! "${VENV_CRAWL}" -m quant_crawler.strategy_gen.llm_codegen \
            --limit "${CODEGEN_LIMIT}" --model "${CODEGEN_MODEL}" >> "${RUN_LOG}" 2>&1; then
        log "WARN: llm_codegen failed (see ${RUN_LOG}); continuing"
    fi
fi

log "=== daily_refresh DONE ==="
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) OK emitted=${BUNDLES_EMITTED} validated=${#GEN_DIRS[@]}" >> "${SUMMARY_LOG}"
exit 0

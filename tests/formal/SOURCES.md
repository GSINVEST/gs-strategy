# tests/formal 的來源

`traces/queue-*.json`（9 個）、`vendor/strategy_queue_ref.py`、`vendor/Queue.tla` 複製自
genesis-deck（GSINVEST/genesis-deck）`origin/main` @ `9440b3d40934fc33b4009e87cdb7bc8403ca5ab1`：

- `tests/formal/traces/queue-*.json`（不含 `poll-leaks-queue-bytes.json`，那是別的模型）
- `tests/formal/strategy_queue_ref.py`
- `specs/tla/strategy-queue/Queue.tla`

雜湊記在 `SOURCES.sha256`，`test_vendored_files_match_recorded_hashes` 會檢查。
**不要在這裡改這些檔案**：要更新就從 genesis-deck 重新複製、更新 commit 與雜湊。
`Queue.tla` 只是留底供對照，這個 repo 不跑 TLC。

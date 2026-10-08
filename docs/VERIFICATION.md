# Verification — 2026-10-08

Environment: macOS, Python 3.14, Codex CLI 0.154.0. Tests use fictional Cedar service data.

| Capability | Evidence | Boundary |
|---|---|---|
| Extract conversation through Codex | Actual `flush.py` call produced a daily entry preserving SQLite/WAL, seven-day retention, and pending restore testing | Synthetic conversation |
| Compile articles | Actual `compile.py` produced two concept articles, index, build log and source hash | Synthetic memory root |
| Query | Actual query returned cited facts and preserved uncertainty | Same generated articles |
| File answer back | Actual `query.py --file-back` created Q&A and updated index/log | Same synthetic root |
| Structural lint | 0 errors, 1 orphan warning, 5 link/length suggestions | Small synthetic corpus; recommendations were not hidden |
| Codex lifecycle | Native app-server emitted completed SessionStart with memory context, completed Stop handlers, and completed turn | Ephemeral native test; no persisted user conversation |
| User-level installation | Native `hooks/list`: four memory handlers enabled/trusted; no configuration errors. Existing non-memory handlers unchanged against backup | One Mac; trust set through normal Codex UI |
| Duplicate/retry/partial-line behavior | 16 unit/integration tests passed, including local fake CLI, failed exit, empty result, capture crash window, separate root, legacy replacement, redaction | No model required |
| End-of-day automation | Code invokes compilation of the changed current-day log after local 18:00 | Time-bound scheduling not observed at 18:00 in the live run |
| PreCompact / SessionEnd | Registered trusted hooks share the tested capture entrypoint | Natural compaction and session-end capture not separately observed |
| Windows | Platform-aware command quoting, process creation, file locking are implemented | Not executed on Windows |

Memory code, trust registration, and actual model behavior are separate evidence boundaries.
This report does not claim every lifecycle event or every platform was exercised.

# Codex Memory Compiler

A Codex adaptation of [coleam00/claude-memory-compiler](https://github.com/coleam00/claude-memory-compiler).
Conversations become daily Markdown notes, which compile into linked knowledge articles.
Preserves the upstream `daily/ → knowledge/` layout, compiler, query, and seven lint checks.

## Requirements

- Python 3.12+ (no Python runtime dependencies; `uv` is optional).
- An authenticated `codex` CLI on PATH with `exec --ephemeral`, `--output-last-message`,
  and lifecycle hooks. Developed against CLI 0.154.0.
- Codex uses your configured provider, model, and authentication. Model calls consume that
  provider's quota or billing; this project neither reads credentials nor estimates dollar costs.

## Set up

```sh
git clone https://github.com/lyomagit/codex-memory-compiler.git
cd codex-memory-compiler
codex login
python3 scripts/install_hooks.py --project /absolute/path/to/your/project
```

On Windows use `python` instead of `python3`. The installer records the current Python
executable and absolute script paths, including quoting for spaces. Keep this checkout in
place. It merges `.codex/hooks.json`, backs up an existing file, and is safe to rerun.
Without `--project`, it only prints the configuration. It never changes global settings.
Review/trust the hooks through your Codex client's normal flow, then start a new session.
If the project has inline `[hooks]` configuration, reconcile that with `hooks.json` as
explained in the [Codex hooks documentation](https://learn.chatgpt.com/docs/hooks).

**Installing capture opts the selected project's conversations into model processing.**
The hook itself performs local I/O; its worker sends extracted conversation text to your
configured Codex provider. Inspect sensitive inputs before enabling capture. Prompt-level
secret exclusion is not a reliable redactor. Do not enable on secret-bearing conversations.
Do not point this checkout at an existing shared Wiki without a separate migration.

## Use

```sh
python3 scripts/compile.py --dry-run
python3 scripts/compile.py
python3 scripts/compile.py --all
python3 scripts/compile.py --file daily/2026-10-08.md
python3 scripts/query.py "What did we decide about deployment?"
python3 scripts/query.py "What did we decide about deployment?" --file-back
python3 scripts/lint.py --structural-only
python3 scripts/lint.py
```

With uv, prefix any command with `uv run`. Structural lint and dry-run need no model calls.
For manual capture or retry:

```sh
python3 scripts/flush.py /absolute/path/to/rollout.jsonl session-id
```

## How it works

1. `Stop`, `PreCompact`, and `SessionEnd` queue incremental capture. Repeated events reuse
   byte cursors; response messages are read once, excluding their `event_msg` mirrors.
2. `codex exec` summarizes new messages into append-only `daily/YYYY-MM-DD.md`.
3. After 18:00 local time, capture triggers compilation; manual compilation works anytime.
4. The compiler writes `knowledge/concepts/`, `connections/`, `index.md`, and `log.md`.
5. `SessionStart` injects a bounded index and recent daily excerpt, labeled as recall.
6. Queries read the index and articles; `--file-back` stores an answer under `knowledge/qa/`.

All installed projects share the memory in this checkout. Use separate checkouts for separate
memory scopes. The upstream query implementation supplies all articles in the prompt; this
is intended for small bases, not unlimited retrieval.

## Failure behavior and boundaries

- Nonzero CLI exits, missing answers, and timeouts are errors, not successful captures.
  Retry the same transcript after fixing the cause; the failed batch retains its cursor.
- A lock serializes writers. Interrupted workers release the OS lock automatically.
- Capture markers cover the append-before-cursor-save crash window. A power loss during
  the physical write is not transactional storage; inspect damaged files before retrying.
- Compiler failures can leave partial article edits; inspect and rerun. Successful child
  completion plus index presence and a changed build log is a mechanical check, not a semantic accuracy guarantee.
- Writing model runs use a `knowledge/`-rooted workspace sandbox; other runs are read-only.
  Codex user configuration, global instructions, hooks, and external tools still apply.
  The recursion guard suppresses only this project's hooks.
- The JSONL rollout format is version-sensitive. Only `response_item` message text is
  supported; tools, reasoning, images, and audio are not captured.
- Generated memory, reports, and operational state are ignored by Git. Existing upstream
  Markdown knowledge remains compatible. Claude SDK and Claude hook settings are replaced.

To disconnect, remove this checkout's commands from the selected project's `.codex/hooks.json`
(or restore its installer backup if no later changes occurred). Preserve your memory files.

## Verification

```sh
python3 -m unittest discover -s tests -v
python3 scripts/compile.py --dry-run
python3 scripts/lint.py --structural-only
```

Automated tests use synthetic transcripts and a simulated Codex process. Real model output,
Windows execution, and hook activation in a trusted desktop session require separate live checks.
See [AGENTS.md](AGENTS.md) for the article schema and runtime contract and the official
[non-interactive mode reference](https://learn.chatgpt.com/docs/non-interactive-mode) for Codex CLI.

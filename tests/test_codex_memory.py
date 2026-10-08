"""Contract tests use only synthetic messages and a local fake Codex executable."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import codex_runner
import flush
import install_hooks
from transcript import read_messages
from redact import redact


def record(text, role="user", **extra):
    return json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": role,
        "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
        **extra,
    }}) + "\n"


class TranscriptTests(unittest.TestCase):
    def test_messages_exclude_mirrors_tools_reasoning_and_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            data = record("Use SQLite") + record("Agreed", "assistant")
            data += record("hidden", "assistant", phase="analysis")
            data += record("policy", "developer") + record("# AGENTS.md instructions\nsecret")
            data += json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "Use SQLite"}}) + "\n"
            data += json.dumps({"type": "response_item", "payload": {"type": "function_call_output", "output": "secret"}}) + "\n"
            path.write_text(data, encoding="utf-8")
            rows = list(read_messages(path))
            self.assertEqual([t for _, t in rows if t], ["**User:** Use SQLite", "**Assistant:** Agreed"])
            self.assertEqual(list(read_messages(path, rows[-1][0])), [])

    def test_nested_and_string_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            path.write_text(json.dumps({"type": "response_item", "payload": {"item": {
                "type": "message", "role": "user", "content": "Use WAL"}}}) + "\n")
            self.assertEqual(list(read_messages(path))[0][1], "**User:** Use WAL")

    def test_secret_filter_before_model_and_storage(self):
        for secret in ('password="example-value"', 'API_KEY=examplevalue',
                       'Bearer abcdefghijklmnop', 'ghp_' + 'a' * 30,
                       '-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----'):
            self.assertEqual(redact(secret), "[REDACTED]")

    def test_utf8_cursor_and_partial_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            first = record("Привет")
            second = record("Мир")
            path.write_text(first + second[:-1], encoding="utf-8")
            rows = list(read_messages(path))
            self.assertEqual(len(rows), 1)
            with path.open("a") as handle:
                handle.write("\n")
            self.assertEqual(list(read_messages(path, rows[-1][0]))[0][1], "**User:** Мир")
            with self.assertRaises(ValueError):
                list(read_messages(path, path.stat().st_size + 1))


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "rollout.jsonl"
        self.path.write_text(record("Use SQLite"), encoding="utf-8")
        self.state = self.root / "last-flush.json"
        self.daily = self.root / "daily"
        self.patches = [patch.object(flush, "STATE_FILE", self.state), patch.object(flush, "DAILY_DIR", self.daily)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temporary.cleanup()

    def test_duplicate_and_incremental_capture(self):
        with patch.object(flush, "run_flush", AsyncMock(return_value="**Decisions:** SQLite")) as model:
            self.assertTrue(flush.capture(self.path, "session"))
            self.assertFalse(flush.capture(self.path, "session"))
            self.assertEqual(model.call_count, 1)
            with self.path.open("a") as handle:
                handle.write(record("Use WAL", "assistant"))
            self.assertTrue(flush.capture(self.path, "session"))
            self.assertEqual(model.call_count, 2)
            self.assertNotIn("Use SQLite", model.call_args.args[0])

    def test_failure_can_retry_without_loss(self):
        with patch.object(flush, "run_flush", AsyncMock(side_effect=RuntimeError("offline"))):
            with self.assertRaises(RuntimeError):
                flush.capture(self.path, "session")
        self.assertFalse(self.state.exists())
        with patch.object(flush, "run_flush", AsyncMock(return_value="saved")):
            self.assertTrue(flush.capture(self.path, "session"))

    def test_append_before_state_failure_is_not_duplicated(self):
        with patch.object(flush, "run_flush", AsyncMock(return_value="saved")) as model:
            with patch.object(Path, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    flush.capture(self.path, "session")
            flush.capture(self.path, "session")
            self.assertEqual(model.call_count, 1)
            self.assertEqual(next(self.daily.glob("*.md")).read_text().count("saved"), 1)

    def test_noop_and_malformed_input(self):
        with patch.object(flush, "run_flush", AsyncMock(return_value="FLUSH_OK")):
            self.assertFalse(flush.capture(self.path, "session"))
        self.assertEqual(list(self.daily.glob("*.md")), [])
        before = self.state.read_bytes()
        with self.path.open("a") as handle:
            handle.write("not json\n")
        with self.assertRaises(json.JSONDecodeError):
            flush.capture(self.path, "session")
        self.assertEqual(self.state.read_bytes(), before)


class RunnerTests(unittest.TestCase):
    def test_timeout_kills_and_reaps_child(self):
        class Process:
            returncode = None
            killed = False
            calls = 0

            async def communicate(self, data=None):
                self.calls += 1
                if self.calls == 1:
                    raise asyncio.TimeoutError()
                return b"", b""

            def kill(self):
                self.killed = True

        process = Process()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(codex_runner, "KNOWLEDGE_DIR", Path(directory)):
                with patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
                    with self.assertRaises(asyncio.TimeoutError):
                        asyncio.run(codex_runner.run_codex("synthetic"))
        self.assertTrue(process.killed)
        self.assertEqual(process.calls, 2)


class InstallerTests(unittest.TestCase):
    def test_user_install_replaces_only_legacy_memory_and_dry_run_is_readonly(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / "hooks.json"
            old = {"hooks": {"Stop": [{"hooks": [
                {"type": "command", "command": "/old/bin/codex-memory capture --hook-mode"},
                {"type": "command", "command": "python safety-guard.py"}]}]}}
            target.write_text(json.dumps(old))
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
                install_hooks.install(user=True, memory_root=home / "data", replace_legacy=True, dry_run=True)
                self.assertEqual(json.loads(target.read_text()), old)
                install_hooks.install(user=True, memory_root=home / "data", replace_legacy=True)
            commands = [h["command"] for g in json.loads(target.read_text())["hooks"]["Stop"] for h in g["hooks"]]
            self.assertEqual(len(commands), 2)
            self.assertIn("python safety-guard.py", commands)
            self.assertIn(str(home / "data"), commands[0])
            self.assertNotIn("/old/bin", " ".join(commands))

    def test_merge_backup_idempotence_and_quoted_paths(self):
        with tempfile.TemporaryDirectory(prefix="memory test ") as directory:
            project = Path(directory)
            target = project / ".codex/hooks.json"
            target.parent.mkdir()
            original = '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"echo existing"}]}]}}'
            target.write_text(original)
            install_hooks.install(project)
            install_hooks.install(project)
            data = json.loads(target.read_text())
            self.assertEqual(len(data["hooks"]["Stop"]), 2)
            backups = list(target.parent.glob("hooks.json.backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)
            self.assertEqual(data["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"], 3)


FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
if os.environ.get("FAKE_FAIL"):
    raise SystemExit(7)
output = pathlib.Path(args[args.index("--output-last-message") + 1])
root = pathlib.Path(args[args.index("--cd") + 1])
if os.environ.get("FAKE_REFUSE"):
    output.write_text("Cannot complete operation")
    raise SystemExit(0)
if os.environ.get("FAKE_EMPTY"):
    raise SystemExit(0)
assert args[0] == "exec" and "--ephemeral" in args
assert os.environ["CODEX_MEMORY_COMPILER_ACTIVE"] == "1"
if "You are a knowledge compiler" in prompt:
    assert args[args.index("--sandbox") + 1] == "workspace-write"
    (root / "concepts").mkdir(exist_ok=True)
    (root / "concepts/sqlite.md").write_text("---\ntitle: SQLite\nsources: [daily/2026-10-08.md]\ncreated: 2026-10-08\nupdated: 2026-10-08\n---\n# SQLite\nUse SQLite.\n")
    (root / "index.md").write_text("# Index\n[[concepts/sqlite]]\n")
    with (root / "log.md").open("a") as f: f.write("Compiled SQLite\n")
    answer = "Compiled"
elif "query engine" in prompt:
    answer = "Use SQLite [[concepts/sqlite]]"
    if "File Back Instructions" in prompt:
        (root / "qa").mkdir(exist_ok=True)
        (root / "qa/database.md").write_text(answer)
        with (root / "index.md").open("a") as f: f.write("[[qa/database]]\n")
        with (root / "log.md").open("a") as f: f.write("query filed\n")
    else:
        assert args[args.index("--sandbox") + 1] == "read-only"
elif "contradictions" in prompt:
    answer = "NO_ISSUES"
else:
    assert args[args.index("--sandbox") + 1] == "read-only"
    answer = "**Decisions Made:**\n- Use SQLite."
output.write_text(answer)
'''


@unittest.skipIf(os.name == "nt", "POSIX executable fixture; Windows requires a live CLI check")
class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="codex memory test ")
        self.root = Path(self.temporary.name)
        for folder in ("scripts", "hooks"):
            shutil.copytree(ROOT / folder, self.root / folder,
                            ignore=shutil.ignore_patterns("__pycache__", "*.json", "*.log", "*.tmp", ".memory.lock"))
        shutil.copy2(ROOT / "AGENTS.md", self.root / "AGENTS.md")
        binary = self.root / "bin"
        binary.mkdir()
        executable = binary / "codex"
        executable.write_text(FAKE_CODEX)
        executable.chmod(0o700)
        self.env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"])
        self.env.pop("CODEX_MEMORY_COMPILER_ACTIVE", None)

    def tearDown(self):
        self.temporary.cleanup()

    def run_script(self, name, *args, fail=False, **env):
        result = subprocess.run([sys.executable, str(self.root / name), *args],
                                cwd=self.root, env=dict(self.env, **env),
                                capture_output=True, text=True, timeout=30)
        if not fail:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_capture_hook_spawns_worker_from_another_cwd(self):
        import time
        transcript = self.root / "rollout.jsonl"
        transcript.write_text(record("Use SQLite"))
        payload = json.dumps({"session_id": "hook-fixture", "transcript_path": str(transcript)})
        result = subprocess.run([sys.executable, str(self.root / "hooks/session-end.py")],
                                cwd=self.root.parent, env=self.env, input=payload,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        deadline = time.monotonic() + 10
        state = self.root / ".codex-memory-compiler/last-flush.json"
        while not state.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(state.exists())
        self.assertIn(transcript.stat().st_size, json.loads(state.read_text()).values())
        self.assertIn("SQLite", next((self.root / "daily").glob("*.md")).read_text())
        # Auto-compilation, if due, must finish before disposing its fixture directory.
        with subprocess.Popen([sys.executable, str(self.root / "scripts/compile.py")],
                              cwd=self.root, env=self.env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE) as child:
            _, errors = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, errors)

    def test_separate_memory_root(self):
        memory = self.root / "separate memory"
        transcript = self.root / "rollout.jsonl"
        transcript.write_text(record("Use SQLite"))
        self.run_script("scripts/flush.py", str(transcript), "separate", CODEX_MEMORY_ROOT=str(memory))
        self.assertTrue((memory / ".codex-memory-compiler/last-flush.json").exists())
        self.assertFalse((self.root / "daily").exists())
        self.run_script("scripts/compile.py", CODEX_MEMORY_ROOT=str(memory))
        self.assertTrue((memory / "knowledge/index.md").exists())
        context = self.run_script("hooks/dispatch.py", "start", "--root", str(memory))
        self.assertIn("SQLite", context.stdout)

    def test_complete_synthetic_workflow(self):
        transcript = self.root / "rollout.jsonl"
        transcript.write_text(record("Use SQLite"))
        self.run_script("scripts/flush.py", str(transcript), "fixture")
        self.run_script("scripts/compile.py")
        state = json.loads((self.root / ".codex-memory-compiler/state.json").read_text())
        self.assertEqual(len(state["ingested"]), 1)
        self.assertTrue((self.root / "knowledge/concepts/sqlite.md").is_file())
        self.assertIn("Nothing to compile", self.run_script("scripts/compile.py").stdout)
        self.assertIn("SQLite", self.run_script("scripts/query.py", "Database?").stdout)
        self.run_script("scripts/query.py", "Database?", "--file-back")
        self.assertTrue((self.root / "knowledge/qa/database.md").is_file())
        self.run_script("scripts/lint.py")
        context = json.loads(self.run_script("hooks/session-start.py").stdout)
        self.assertIn("SQLite", context["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.run_script("hooks/session-start.py", CODEX_MEMORY_COMPILER_ACTIVE="1").stdout, "")
        self.assertEqual(self.run_script("hooks/session-end.py", CODEX_MEMORY_COMPILER_ACTIVE="1").stdout, "")

    def test_successful_exit_without_artifacts_is_not_success(self):
        daily = self.root / "daily"
        daily.mkdir()
        (daily / "2026-10-08.md").write_text("Use SQLite")
        self.run_script("scripts/compile.py", fail=True, FAKE_REFUSE="1")
        self.assertFalse((self.root / ".codex-memory-compiler/state.json").exists())
        self.run_script("scripts/compile.py")
        before = (self.root / ".codex-memory-compiler/state.json").read_bytes()
        self.run_script("scripts/compile.py", "--all", fail=True, FAKE_REFUSE="1")
        self.assertEqual((self.root / ".codex-memory-compiler/state.json").read_bytes(), before)
        self.run_script("scripts/query.py", "Database?", "--file-back", fail=True, FAKE_REFUSE="1")
        self.assertEqual((self.root / ".codex-memory-compiler/state.json").read_bytes(), before)

    def test_failure_and_empty_answer_do_not_mark_compiled(self):
        daily = self.root / "daily"
        daily.mkdir()
        (daily / "2026-10-08.md").write_text("Use SQLite")
        self.run_script("scripts/compile.py", fail=True, FAKE_FAIL="1")
        self.assertFalse((self.root / ".codex-memory-compiler/state.json").exists())
        self.run_script("scripts/compile.py", fail=True, FAKE_EMPTY="1")
        self.assertFalse((self.root / ".codex-memory-compiler/state.json").exists())
        self.run_script("scripts/query.py", "Database?", fail=True, FAKE_FAIL="1")
        self.assertFalse((self.root / ".codex-memory-compiler/state.json").exists())
        self.run_script("scripts/lint.py", fail=True, FAKE_FAIL="1")


if __name__ == "__main__":
    unittest.main()

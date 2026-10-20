"""Regression tests for selective retrieval, representations, filing and heuristics."""
from __future__ import annotations

import asyncio
from html import unescape
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_retrieval
import codex_runner
import heuristics
import query
import retrieval

FIXTURES = json.loads((ROOT / "benchmarks" / "fixtures.json").read_text(encoding="utf-8"))


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = benchmark_retrieval.fixture_knowledge(FIXTURES)
        self.root = self.fixture.__enter__()

    def tearDown(self):
        self.fixture.__exit__(None, None, None)

    def test_full_mode_preserves_original_index_and_articles_format(self):
        full = retrieval.retrieve("OAuth", root=self.root, mode="full")
        original = "## INDEX\n\n" + (self.root / "index.md").read_text(encoding="utf-8")
        for entry in retrieval.catalog(self.root):
            original += ("\n\n---\n\n## " + entry.key + ".md\n\n"
                         + entry.path.read_text(encoding="utf-8"))
        self.assertEqual(full.context, original)
        self.assertEqual(len(full.selected_ids), 91)

    def test_default_selective_does_not_send_every_article(self):
        result = retrieval.retrieve("OAuth redirect callback state", root=self.root)
        self.assertIn("concepts/auth-redirect", result.selected_ids)
        self.assertNotIn("concepts/fixture-noise-001", result.selected_ids)
        self.assertIn("[[concepts/jwt-validation]]", result.context)
        self.assertNotIn("Unrelated synthetic inventory", result.context)

    def test_index_summary_matches_even_when_article_body_does_not(self):
        target = self.root / "concepts" / "summary-only.md"
        target.write_text("# Short\nNo special words here.", encoding="utf-8")
        with (self.root / "index.md").open("a", encoding="utf-8") as stream:
            stream.write("| [[concepts/summary-only]] | ultrafast capsule analyzer | synthetic | now |\n")
        result = retrieval.retrieve("ultrafast capsule", root=self.root)
        self.assertIn("concepts/summary-only", result.selected_ids)

    def test_empty_query_or_no_match_does_not_send_all(self):
        self.assertEqual(retrieval.retrieve("and the with", root=self.root).selected_ids, ())
        self.assertEqual(retrieval.retrieve("neutrino entanglement", root=self.root).selected_ids, ())

    def test_size_budget_limits_and_marks_truncation(self):
        result = retrieval.retrieve("SQLite WAL", root=self.root, top_k=2, max_chars=70)
        self.assertTrue(result.truncated_ids)
        self.assertIn("TRUNCATED", result.context)
        self.assertLessEqual(len(result.selected_ids), 2)
        with self.assertRaises(ValueError):
            retrieval.retrieve("SQLite", root=self.root, top_k=0)

    def test_json_html_markdown_preserve_exact_content_and_wikilinks(self):
        question = "SQLite WAL checkpoint backups"
        base = retrieval.retrieve(question, root=self.root)
        data = json.loads(retrieval.retrieve(question, root=self.root,
                                             representation="json").context)
        self.assertEqual([i["path"] for i in data["articles"]], list(base.selected_ids))
        for item in data["articles"]:
            self.assertEqual(item["content"],
                             (self.root / (item["path"] + ".md")).read_text(encoding="utf-8"))
            self.assertIn(item["content"], base.context)
        html = retrieval.retrieve(question, root=self.root, representation="html").context
        for item in data["articles"]:
            self.assertIn(item["content"], unescape(html))

    def test_missing_index_uses_existing_file_names(self):
        (self.root / "index.md").unlink()
        selected = retrieval.retrieve("obs overlay", root=self.root)
        self.assertIn("concepts/obs-overlay", selected.selected_ids)

    def test_rogue_index_entries_and_symlinks_are_not_traversed(self):
        with (self.root / "index.md").open("a", encoding="utf-8") as stream:
            stream.write("| [[../outside]] | password secrets | synthetic | now |\n")
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "secret.md"
            target.write_text("NEVER_RETRIEVE_CONTENT", encoding="utf-8")
            link = self.root / "concepts" / "link.md"
            try:
                link.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("Symlinks not supported on this platform")
            self.assertNotIn("concepts/link", [x.key for x in retrieval.catalog(self.root)])
            self.assertNotIn("NEVER_RETRIEVE_CONTENT",
                             retrieval.retrieve("password secrets", root=self.root,
                                                mode="full").context)

    def test_file_changes_are_visible_without_stale_cache(self):
        path = self.root / "concepts" / "obs-overlay.md"
        before = retrieval.retrieve("OBS scoreboard", root=self.root).context
        path.write_text("# OBS\nChanged content.", encoding="utf-8")
        after = retrieval.retrieve("OBS scoreboard", root=self.root).context
        self.assertIn("Changed content", after)
        self.assertNotEqual(before, after)


class HeuristicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self._write("oauth-safe", ["OAuth", "callback"], "Validate callback", ["oauth-fast"])
        self._write("oauth-fast", ["OAuth", "callback"], "Skip validation", ["oauth-safe"])
        self._write("sqlite-audit", ["SQLite", "WAL"], "Use transactions")

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, id, triggers, guidance, conflicts=()):
        (self.directory / (id + ".json")).write_text(json.dumps({
            "id": id, "title": id, "guidance": guidance,
            "triggers": triggers, "conflicts_with": list(conflicts)}), encoding="utf-8")

    def test_conflict_holds_back_both_rules_and_keeps_protected(self):
        result = heuristics.activate("OAuth callback", directory=self.directory)
        self.assertEqual(result.activated_ids, ())
        self.assertEqual(result.conflicts, (("oauth-fast", "oauth-safe"),))
        self.assertNotIn("Skip validation", result.context)
        self.assertNotIn("Validate callback", result.context)
        self.assertIn("Never override higher-priority", result.context)
        self.assertIn("Unresolved heuristic conflicts", result.context)

    def test_unrelated_heuristics_not_loaded(self):
        result = heuristics.activate("SQLite WAL", directory=self.directory)
        self.assertEqual(result.activated_ids, ("sqlite-audit",))
        self.assertNotIn("Skip validation", result.context)

    def test_invalid_duplicate_and_symlink_rules_rejected(self):
        path = self.directory / "invalid.json"
        path.write_text('{"id": "../../escape", "guidance": "bad"}')
        with self.assertRaises(ValueError):
            heuristics.load_heuristics(self.directory)
        path.unlink()
        with tempfile.TemporaryDirectory() as temp:
            external = Path(temp) / "evil.json"
            external.write_text(json.dumps({"id": "evil", "title": "Evil",
                                            "guidance": "MALICIOUS",
                                            "triggers": ["OAuth"]}))
            try:
                path.symlink_to(external)
            except (NotImplementedError, OSError):
                self.skipTest("Symlinks not supported")
            self.assertNotIn("evil", [x.id for x in heuristics.load_heuristics(self.directory)])


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = benchmark_retrieval.fixture_knowledge(FIXTURES)
        self.root = self.fixture.__enter__()

    def tearDown(self):
        self.fixture.__exit__(None, None, None)

    def test_file_back_keeps_complete_index_available_for_safe_updates(self):
        recorded = []

        async def fake_run(prompt, *, writable=False):
            recorded.append((prompt, writable))
            if writable:
                folder = self.root / "qa"
                folder.mkdir(exist_ok=True)
                (folder / "test.md").write_text("Test Q&A", encoding="utf-8")
            return "Result [[concepts/obs-overlay]]"

        with patch.object(codex_runner, "run_codex", new=fake_run), \
             patch.object(query, "load_state", return_value={"query_count": 1}), \
             patch.object(query, "save_state") as save:
            answer = asyncio.run(query.run_query(
                "OBS overlay", file_back=True, knowledge_dir=self.root))
        self.assertIn("Result", answer)
        prompt, writable = recorded[0]
        self.assertTrue(writable)
        self.assertIn("Complete index (for preserving existing entries", prompt)
        self.assertIn("[[concepts/fixture-noise-079]]", prompt)
        self.assertNotIn("Unrelated synthetic inventory record", prompt)
        save.assert_called_once()
        self.assertEqual(save.call_args.args[0]["query_count"], 2)

    def test_file_back_without_qa_artifact_is_failure(self):
        with patch.object(codex_runner, "run_codex", new=AsyncMock(return_value="answer")), \
             patch.object(query, "save_state") as save:
            with self.assertRaisesRegex(RuntimeError, "did not create"):
                asyncio.run(query.run_query("OBS overlay", file_back=True,
                                            knowledge_dir=self.root))
            save.assert_not_called()

    def test_default_query_uses_selective_mode(self):
        async def fake_run(prompt, *, writable=False):
            self.assertNotIn("Unrelated synthetic inventory record", prompt)
            self.assertIn("[[concepts/obs-overlay]]", prompt)
            self.assertFalse(writable)
            return "OBS [[concepts/obs-overlay]]"
        with patch.object(codex_runner, "run_codex", new=fake_run), \
             patch.object(query, "load_state", return_value={"query_count": 0}), \
             patch.object(query, "save_state"):
            answer = asyncio.run(query.run_query("OBS scoreboard overlay",
                                                  knowledge_dir=self.root))
        self.assertIn("OBS", answer)


class BenchmarkTests(unittest.TestCase):
    def test_synthetic_benchmark_is_deterministic_and_labels_unmeasured_tokens(self):
        with benchmark_retrieval.fixture_knowledge(FIXTURES) as root:
            with patch.object(benchmark_retrieval, "_tokenizer", return_value=None):
                one = benchmark_retrieval.benchmark(root, FIXTURES["cases"])
                two = benchmark_retrieval.benchmark(root, FIXTURES["cases"])
        self.assertEqual(one, two)
        self.assertEqual(one["corpus_articles"], 91)
        self.assertIsNone(one["markdown_selective_vs_full"]["token_reduction_percent"])
        self.assertLess(one["aggregate"]["selective/markdown"]["mean_prompt_utf8_bytes"],
                        one["aggregate"]["full/markdown"]["mean_prompt_utf8_bytes"])
        self.assertEqual(one["aggregate"]["selective/markdown"]["mean_gold_recall"], 1.0)

    def test_actual_citation_validation_flags_invalid_and_unselected(self):
        with benchmark_retrieval.fixture_knowledge(FIXTURES) as root:
            records = benchmark_retrieval.benchmark(
                root, [FIXTURES["cases"][0]],
                answers={FIXTURES["cases"][0]["question"]:
                         "Use [[concepts/auth-redirect]] and [[concepts/imaginary]]."})
        row = next(x for x in records["per_case"] if x["mode"] == "selective"
                   and x["representation"] == "markdown")
        self.assertLess(row["answer_links"]["valid_ratio"], 1.0)
        self.assertLess(row["answer_links"]["grounded_ratio"], 1.0)
        self.assertLess(row["answer_links"]["gold_recall"], 1.0)


if __name__ == "__main__":
    unittest.main()

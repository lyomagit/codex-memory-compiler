"""Offline, reproducible baseline-vs-selective / Markdown-vs-HTML-vs-JSON benchmark.

No Codex calls or user memory are required. Actual BPE token counts are only
reported if the optional tiktoken package is available; no character heuristic
is ever mislabeled a token count.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import statistics
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from query import make_prompt
from retrieval import catalog, retrieve

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "benchmarks" / "fixtures.json"
ARTICLE_ID = re.compile(r"^(?:concepts|connections|qa)/[a-z0-9][a-z0-9-]*$")
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def _tokenizer(encoding: str):
    try:
        import tiktoken
    except ImportError:
        return None
    return tiktoken.get_encoding(encoding).encode


@contextmanager
def fixture_knowledge(dataset: dict):
    with tempfile.TemporaryDirectory(prefix="cmc-retrieval-benchmark-") as directory:
        root = Path(directory) / "knowledge"
        rows = ["# Synthetic Knowledge Base Index", "",
                "| Article | Summary | Compiled From | Updated |",
                "|---------|---------|---------------|---------|"]
        for entry in dataset["documents"]:
            key = entry["id"]
            if not ARTICLE_ID.fullmatch(key):
                raise ValueError(f"Unsafe fixture article id: {key}")
            path = root / f"{key}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entry["content"] + "\n", encoding="utf-8")
            rows.append(f"| [[{key}]] | {entry['summary']} | synthetic | 2026-10-20 |")
        # Distinct, unrelated records create a realistic *scaling* test for
        # all-article inclusion while keeping the relevant corpus stable.
        for i in range(dataset.get("distractors", 0)):
            key = f"concepts/fixture-noise-{i:03d}"
            path = root / f"{key}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            body = ("Unrelated synthetic inventory record with a fixed opaque field. "
                    "It contains no query-answering claims.\n") * 16
            path.write_text(f"# Fixture noise {i:03d}\n{body}", encoding="utf-8")
            rows.append(f"| [[{key}]] | Unrelated synthetic inventory number {i:03d} | synthetic | 2026-10-20 |")
        (root / "index.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
        yield root


def score_links(text: str, valid_ids: set[str], supplied: set[str],
                expected: set[str]) -> dict:
    links = [_normalize_link(x) for x in WIKILINK.findall(text)]
    citations = [s for s in links if not s.startswith("daily/")]
    return {
        "citations": citations,
        "valid_ratio": sum(k in valid_ids for k in citations) / len(citations)
        if citations else None,
        "grounded_ratio": sum(k in supplied for k in citations) / len(citations)
        if citations else None,
        "gold_precision": sum(k in expected for k in citations) / len(citations)
        if citations else None,
        "gold_recall": (len(set(citations) & expected) / len(expected)
                        if expected else None),
        "abstention_correct": (not citations if not expected else None),
    }


def _normalize_link(s: str) -> str:
    result = s.split("|", 1)[0].split("#", 1)[0].strip()
    return result[:-3] if result.endswith(".md") else result


def benchmark(root: Path, cases: list[dict], *, top_k: int = 5,
              max_chars: int = 30000, encoding: str = "o200k_base",
              answers: dict[str, str] | None = None) -> dict:
    encode = _tokenizer(encoding)
    known = {entry.key for entry in catalog(root)}
    results = []
    for case in cases:
        question = case["question"]
        expected = set(case["expected"])
        if not expected.issubset(known):
            raise ValueError(f"Gold references are not present in corpus: {sorted(expected - known)}")
        for mode in ("full", "selective"):
            for representation in ("markdown", "html", "json"):
                retrieval = retrieve(question, root=root, mode=mode, top_k=top_k,
                                     max_chars=max_chars, representation=representation)
                prompt = make_prompt(question, retrieval.context, mode=mode)
                selected = set(retrieval.selected_ids)
                # For negative cases report selection/abstention separately;
                # source recall has no denominator when gold is empty.
                recall = (len(expected & selected) / len(expected) if expected else None)
                all_links = []
                for entry in catalog(root):
                    if entry.key in selected:
                        all_links.extend(_normalize_link(s) for s in
                                         WIKILINK.findall(entry.path.read_text(encoding="utf-8")))
                checked_links = [k for k in all_links if not k.startswith("daily/")]
                record = {
                    "question": question, "mode": mode, "representation": representation,
                    "articles_selected": len(selected), "selected_ids": list(retrieval.selected_ids),
                    "gold_recall": recall,
                    "negative_query_activated_articles": bool(selected) if not expected else None,
                    "truncated_ids": list(retrieval.truncated_ids),
                    "wikilink_integrity": (sum(k in known for k in checked_links) /
                                            len(checked_links) if checked_links else None),
                    "prompt_utf8_bytes": len(prompt.encode("utf-8")),
                    "prompt_characters": len(prompt),
                    "prompt_tokens": len(encode(prompt)) if encode else None,
                    "answer_links": (score_links(answers[question], known, selected, expected)
                                     if answers is not None and question in answers else None),
                }
                results.append(record)
    aggregates = {}
    for mode in ("full", "selective"):
        for representation in ("markdown", "html", "json"):
            values = [r for r in results if r["mode"] == mode
                      and r["representation"] == representation]
            key = f"{mode}/{representation}"
            aggregates[key] = {
                "mean_prompt_utf8_bytes": round(statistics.mean(
                    x["prompt_utf8_bytes"] for x in values), 1),
                "mean_prompt_tokens": (round(statistics.mean(
                    x["prompt_tokens"] for x in values), 1) if encode else None),
                "mean_gold_recall": round(statistics.mean(
                    x["gold_recall"] for x in values if x["gold_recall"] is not None), 4)
                if any(x["gold_recall"] is not None for x in values) else None,
                "negative_query_activations": sum(
                    x["negative_query_activated_articles"] is True for x in values),
                "mean_selected_articles": round(statistics.mean(
                    x["articles_selected"] for x in values), 2),
                "truncated_cases": sum(bool(x["truncated_ids"]) for x in values),
                "broken_article_wikilinks": sum(
                    int(x["wikilink_integrity"] < 1) for x in values
                    if x["wikilink_integrity"] is not None),
            }
    full = aggregates["full/markdown"]
    selective = aggregates["selective/markdown"]
    def reduction(field):
        base, new = full[field], selective[field]
        return (round(100 * (1 - new / base), 2)
                if base is not None and new is not None and base else None)
    return {
        "corpus_articles": len(known),
        "questions": len(cases),
        "top_k": top_k,
        "max_chars": max_chars,
        "tokenizer": f"tiktoken:{encoding}" if encode else None,
        "tokenizer_note": ("BPE encoding is a proxy for the configured Codex model "
                           "and is not provider-billed usage." if encode
                           else "No real tokenizer available; token counts intentionally null."),
        "answer_quality_note": ("Answer citation metrics use supplied answers, not a model call."
                                if answers else
                                "No model answers supplied; gold recall and structural link checks "
                                "do not establish final answer quality."),
        "markdown_selective_vs_full": {
            "utf8_byte_reduction_percent": reduction("mean_prompt_utf8_bytes"),
            "token_reduction_percent": reduction("mean_prompt_tokens"),
            "gold_recall_selective": selective["mean_gold_recall"],
        },
        "aggregate": aggregates,
        "per_case": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--knowledge-dir", type=Path,
                    help="Optional existing knowledge/ directory; never written")
    ap.add_argument("--cases", type=Path, default=DEFAULT_CASES,
                    help="Gold questions + expected wikilinks in JSON")
    ap.add_argument("--answers", type=Path,
                    help="Optional JSON mapping question to answer text for citation evaluation")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--max-chars", type=int, default=30_000)
    ap.add_argument("--encoding", default="o200k_base")
    ap.add_argument("--require-tokens", action="store_true",
                    help="Fail if real tiktoken BPE cannot be measured")
    ap.add_argument("--out", type=Path, help="Optional JSON report output path")
    args = ap.parse_args()
    if args.require_tokens and _tokenizer(args.encoding) is None:
        ap.error("tiktoken is unavailable; install it, then rerun --require-tokens")
    dataset = json.loads(args.cases.read_text(encoding="utf-8"))
    answers = (json.loads(args.answers.read_text(encoding="utf-8"))
               if args.answers is not None else None)
    if args.knowledge_dir:
        report = benchmark(args.knowledge_dir, dataset["cases"], top_k=args.top_k,
                           max_chars=args.max_chars, encoding=args.encoding, answers=answers)
    else:
        with fixture_knowledge(dataset) as root:
            report = benchmark(root, dataset["cases"], top_k=args.top_k,
                               max_chars=args.max_chars, encoding=args.encoding, answers=answers)
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

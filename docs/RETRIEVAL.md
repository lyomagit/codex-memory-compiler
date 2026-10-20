# Bounded retrieval and reproducible experiment

Date: 2026-10-20. Project: Codex Memory Compiler.

## Root cause and unchanged contracts

Previously, scripts/query.py called utils.read_all_wiki_content(), which joined
the index and every Markdown file in concepts/, connections/, and qa/ into each
query prompt. Its cost grows with the entire knowledge base, even for a narrow
question. The original function remains available to lint and other callers;
the query CLI has a --mode full escape hatch whose Markdown payload matches
that original serialization.

The knowledge root is still CODEX_MEMORY_ROOT/knowledge (separate from code).
Existing Markdown, frontmatter, index rows, QA artifacts, and [[wikilinks]]
are untouched. No migration, database, embedding model or runtime package is
needed. SessionStart injection is unchanged.

## Selective query algorithm

1. Read knowledge/index.md locally and extract summary text from ordinary
   wikilink table rows. Inventory only real non-symlink Markdown files directly
   within concepts/, connections/, and qa/.
2. Tokenize the question and index summaries / article filenames with Unicode
   lexical terms. Apply weighted TF-IDF; deterministic tie-break by article ID.
3. Include only positive-scoring articles, up to --top-k (default 5) and
   --max-chars (default 30000 combined article characters).
4. Include a compact index containing only those entries and each selected
   article's original source body. When a body does not fit, append an
   explicit TRUNCATED notice rather than silently implying completeness.
5. The Codex prompt permits citing only supplied source article IDs.
   Selection and rendering occur **before** the model invocation.

No-match behavior is fail-open regarding truthful uncertainty, not file access:
the model receives no article body and is told to say retrieval did not find
supporting evidence. For sparse or inaccurate summaries, retry with --mode full.
This lexical system cannot guarantee recall on paraphrases, synonyms, spelling
errors, multilingual inflection or poorly indexed content. A future extension
may add local full-text indexing, re-ranking and semantic retrieval, with the
same safety and evaluation interface.

## File-back boundary

--file-back uses the selected articles for the question but additionally sends
the complete existing index to support conservative index editing. It tells
the model to append new rows and preserve existing entries. The original
postcondition (a new or modified QA file must exist) remains intact.

This does NOT establish transactional correctness: a model could still corrupt
an index while editing it. Existing file-back failure behavior is preserved.
Token savings from ordinary queries must not be assumed for --file-back.

## Representation comparison

Storage stays Markdown in all cases. Three wire encodings use identical article
IDs, index text and *source bytes before encoding*:

- markdown: original headings, separators and raw Markdown article content;
- html: lossless HTML section/article/pre envelope, escaping the unchanged
  source text (NOT semantic Markdown-to-HTML rendering);
- json: a UTF-8 JSON object with index and articles {path, content}.

The experiment isolates transport/wrapper overhead. It cannot establish
whether a model would reason more accurately from native rendered HTML or JSON.
Answer quality requires separately labeled LLM evaluations.

## Optional heuristic selection

An independent, opt-in library lives under CODEX_MEMORY_ROOT/heuristics/*.json,
not under the model-managed knowledge/ directory. Example:

~~~json
{
  "id": "auth-redirect-review",
  "title": "Review OAuth callback redirects",
  "triggers": ["OAuth", "redirect", "callback"],
  "guidance": "Check explicit callback allowlists and document uncertainty.",
  "conflicts_with": ["auth-redirect-relaxed"],
  "enabled": true
}
~~~

Using --heuristics selects up to 3 rules by query/trigger overlap; rules not
selected are never sent to the model. Identifiers are restricted and duplicate
IDs, malformed schema, symlinked files and oversized files are rejected or
ignored as specified in scripts/heuristics.py. If two selected rules reference
each other (or one references the other) as incompatible, BOTH are withheld and
an explicit unresolved-conflict diagnostic is included. There is no automatic
override based on priority.

Protected boundaries are **fixed code**, always inserted before any selected
advisory rules. Retrieved memory and heuristics remain *untrusted reference
data*, not authoritative instructions. The actual enforceable boundaries are
the higher-priority Codex host instructions, codex_runner sandbox mode, and
filesystem permissions; a prompt or Python constant is not a security proof.
Do not put secrets or privileged operational instructions in this library.

This is a minimal extension point, not a self-learning or auto-mutating policy
engine. Changes to hard constraints require reviewed code changes, not
retrieval-score changes.

## Benchmark and interpretation

~~~sh
python3 -m unittest discover -s tests -v
python3 scripts/benchmark_retrieval.py --out benchmark.json
python3 -m pip install tiktoken                   # optional test-only dependency
python3 scripts/benchmark_retrieval.py --require-tokens --encoding o200k_base --out benchmark-bpe.json
python3 scripts/benchmark_retrieval.py --knowledge-dir /path/to/knowledge --cases custom-cases.json
python3 scripts/benchmark_retrieval.py --answers question_to_answer.json
~~~

The bundled benchmark builds a deterministic synthetic corpus in a temporary
directory (11 relevant, 80 unrelated filler documents: 91 total) and uses 8
fixed queries (7 with gold sources, 1 intentionally irrelevant). No user data
or model API calls are needed.

Measured on a Mac running Python 3.14.6 and tiktoken 0.14.0 (encoding
o200k_base), mean tokens **per full query prompt**:

| Mode | Markdown | Lossless HTML wrapper | JSON |
|------|---------:|----------------------:|-----:|
| Full (91 articles) | 28,915.8 | 29,744.8 | 30,576.8 |
| Selective | 251.4 | 274.0 | 267.2 |

Markdown selective versus Markdown full: **99.13% fewer BPE tokens** in this
synthetic stress test. Average source count selected: 1.5 vs 91. The 80 filler
documents deliberately exaggerate the value of excluding irrelevant content.
Never generalize this percentage to real repositories or provider billing.
The bytes-only run uses measured UTF-8 size and leaves token fields null.

For positive queries, both modes retrieve 100% of expected gold articles in
this fixture; one no-gold question shows whether selection can abstain. The
benchmark records source recall, zero-match behavior, truncation and internal
wikilink integrity separately. Optional --answers scores actual answer
wikilinks for corpus validity, whether a citation was in retrieved context,
and gold precision/recall. Without model answers, these metrics are
**retrieval-quality proxies**, not evidence of model answer correctness.

Re-running against a real, annotated, consented knowledge corpus with
representative queries is required before claiming general quality or
production token savings.

## Regression boundaries

tests/test_retrieval.py covers full-mode byte-compatible serialization,
index-only retrieval, missing index behavior, empty lexical matches, local
file changes, Unicode-safe source retention, JSON/HTML article equivalence,
max budget, invalid path/symlink exclusion, file-back prompts, no-QA errors,
opt-in heuristic conflicts, duplicate and invalid schemas, reproducible
benchmark output, and rejection of fabricated citations.

Existing tests/test_codex_memory.py continues testing synthetic fake-Codex
query filing, capture, lifecycle hooks, locking and failure cases.

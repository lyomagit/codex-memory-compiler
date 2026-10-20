"""Deterministic, bounded, index-guided retrieval for the Markdown wiki.

Only existing knowledge files are eligible. Article text is read into model
context *after* metadata scoring; no remote index, embeddings, or new runtime
dependencies are required.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from html import escape as html_escape
import json
import math
from pathlib import Path
import re

from config import KNOWLEDGE_DIR

TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
INDEX_ROW = re.compile(r"^\|\s*\[\[([^\]]+)\]\]\s*\|(.*)\|\s*$")
STOP = frozenset(
    "a an and are as at be by can do does for from how in is it of on or the "
    "to what when where which why with should i we you about my our use using "
    "а в во для до и из к как на не но о об от по при про с со у что чем это "
    "этот эта эти ли или нужно надо мне мы вы он она они".split()
)
SECTIONS = ("concepts", "connections", "qa")
SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True)
class Article:
    key: str
    path: Path
    summary: str = ""


@dataclass(frozen=True)
class Selected:
    article: Article
    content: str
    truncated: bool = False


@dataclass(frozen=True)
class Retrieval:
    context: str
    selected_ids: tuple[str, ...]
    truncated_ids: tuple[str, ...]
    catalog_size: int


def terms(value: str) -> Counter[str]:
    return Counter(t for t in TOKEN.findall(value.casefold()) if t not in STOP and len(t) > 1)


def index_content(root: Path = KNOWLEDGE_DIR) -> str:
    path = root / "index.md"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ("# Knowledge Base Index\n\n"
            "| Article | Summary | Compiled From | Updated |\n"
            "|---------|---------|---------------|---------|")


def _link_key(raw: str) -> str:
    """Normalize permitted wiki identifiers; never resolve an arbitrary link path."""
    key = raw.split("|", 1)[0].split("#", 1)[0].strip()
    return key[:-3] if key.endswith(".md") else key


def _summaries(raw_index: str) -> dict[str, str]:
    summaries: dict[str, str] = {}
    for line in raw_index.splitlines():
        match = INDEX_ROW.match(line.strip())
        if match:
            # The first table column is the wikilink, the second is its summary.
            key, columns = match.groups()
            summary = re.split(r"(?<!\\)\|", columns, maxsplit=1)[0].strip()
            summaries[_link_key(key)] = summary.replace(r"\|", "|")
    return summaries


def catalog(root: Path = KNOWLEDGE_DIR) -> list[Article]:
    """Inventory only direct Markdown files in the three existing wiki directories."""
    root = Path(root)
    summaries = _summaries(index_content(root))
    entries = []
    for section in SECTIONS:
        folder = root / section
        if not folder.is_dir() or folder.is_symlink():
            continue
        for path in sorted(folder.glob("*.md")):
            if not path.is_file() or path.is_symlink():
                continue
            key = f"{section}/{path.stem}"
            entries.append(Article(key, path, summaries.get(key, "")))
    return entries


def ranked_articles(question: str, entries: list[Article]) -> list[Article]:
    """Weighted TF-IDF over index summary, identifier, and filename.

    Unknown queries yield no article instead of silently sending the entire KB.
    This is lexical retrieval, not a semantic relevance guarantee.
    """
    query = terms(question)
    if not query:
        return []
    features = []
    frequencies = Counter()
    for entry in entries:
        title = terms(entry.path.stem.replace("-", " "))
        path = terms(entry.key.replace("/", " ").replace("-", " "))
        summary = terms(entry.summary)
        features.append((title, path, summary))
        frequencies.update((title | path | summary).keys())
    scores = []
    size = len(entries)
    for entry, (title, path, summary) in zip(entries, features):
        score = 0.0
        for token, q_count in query.items():
            if token not in frequencies:
                continue
            idf = math.log(1 + (size - frequencies[token] + .5) / (frequencies[token] + .5))
            score += idf * min(q_count, 2) * (
                4 * min(title[token], 2) + 2 * min(path[token], 2)
                + 1.5 * min(summary[token], 3)
            )
        if score > 0:
            scores.append((score, entry.key, entry))
    return [entry for _, _, entry in sorted(scores, key=lambda item: (-item[0], item[1]))]


def choose(question: str, entries: list[Article], *, top_k: int = 5,
           max_chars: int = 30_000) -> list[Selected]:
    if top_k < 1 or max_chars < 1:
        raise ValueError("top_k and max_chars must be positive")
    remaining = max_chars
    picked = []
    for entry in ranked_articles(question, entries)[:top_k]:
        if remaining < 1:
            break
        source = entry.path.read_text(encoding="utf-8")
        if len(source) <= remaining:
            picked.append(Selected(entry, source))
            remaining -= len(source)
            continue
        notice = "\n\n[TRUNCATED: read original article for remaining content]"
        if remaining <= len(notice):
            break
        limit = remaining - len(notice)
        end = source.rfind("\n", 0, limit)
        snippet = source[:end if end > limit // 2 else limit]
        picked.append(Selected(entry, snippet + notice, True))
        break
    return picked


def serialize(raw_index: str, articles: list[Selected], representation: str) -> str:
    """Same index and full article payload across three *lossless* envelopes.

    HTML intentionally uses <pre> to keep original Markdown unchanged; this
    compares representation overhead, not the quality of HTML conversion.
    """
    if representation == "markdown":
        parts = [f"## INDEX\n\n{raw_index}"]
        parts.extend(f"## {item.article.key}.md\n\n{item.content}" for item in articles)
        return SEPARATOR.join(parts)
    if representation == "html":
        parts = [f'<section data-role="index"><pre>{html_escape(raw_index)}</pre></section>']
        for item in articles:
            parts.append(f'<article data-path="{html_escape(item.article.key, quote=True)}">'
                         f'<pre>{html_escape(item.content)}</pre></article>')
        return "\n".join(parts)
    if representation == "json":
        return json.dumps({
            "index": raw_index,
            "articles": [{"path": item.article.key, "content": item.content}
                         for item in articles],
        }, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    raise ValueError(f"Unsupported representation: {representation}")


def retrieve(question: str, *, root: Path = KNOWLEDGE_DIR, mode: str = "selective",
             top_k: int = 5, max_chars: int = 30_000,
             representation: str = "markdown") -> Retrieval:
    entries = catalog(root)
    full_index = index_content(root)
    if mode == "full":
        picked = [Selected(entry, entry.path.read_text(encoding="utf-8"))
                  for entry in entries]
        rendered_index = full_index
    elif mode == "selective":
        picked = choose(question, entries, top_k=top_k, max_chars=max_chars)
        # A compact index summary travels with the selected articles. The complete
        # index remains on disk and is never rewritten by this read operation.
        rows = ["| Article | Summary |", "|---------|---------|"]
        for item in picked:
            summary = item.article.summary.replace("|", r"\|")
            rows.append(f"| [[{item.article.key}]] | {summary} |")
        rendered_index = (f"# Selected Knowledge Index ({len(picked)} of {len(entries)} articles)\n\n"
                          + "\n".join(rows))
    else:
        raise ValueError(f"Unsupported retrieval mode: {mode}")
    return Retrieval(
        context=serialize(rendered_index, picked, representation),
        selected_ids=tuple(item.article.key for item in picked),
        truncated_ids=tuple(item.article.key for item in picked if item.truncated),
        catalog_size=len(entries),
    )

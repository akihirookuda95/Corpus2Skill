"""Serving agent for Corpus2Skill — hybrid Skills API + document tool.

Skill hierarchy (SKILL.md files only) is uploaded to the Anthropic
native Skills API. The agent navigates the hierarchy via code execution,
then retrieves full documents via a custom get_document tool.

No retrieval system (no embeddings, no BM25, no FAISS) at serve time.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import anthropic

from corpus2skill.config import ServeConfig

SKILLS_BETA = "skills-2025-10-02"

SYSTEM_PROMPT = """\
# Corpus-Grounded Support Agent (Hierarchical Navigation)

You are a knowledge agent that answers questions by navigating a hierarchical
skill directory. You explore a structured file tree where documents are organized
into topic clusters.

## Hard Rules

- Every factual claim must trace to a document you retrieved via get_document.
- SKILL.md files are NAVIGATION AIDS — they tell you where to look, not what to say.
- Never fabricate steps, URLs, prices, or specifics not found in documents.
- Never guess. If you cannot find relevant content after thorough exploration, say so.

## Navigation Strategy

Your skills directory has this structure:
```
skill-XX-topic/
  SKILL.md            ← top-level summary + index of contents
  group-YY-subtopic/
    INDEX.md          ← summary + index of sub-contents
    group-ZZ/
      INDEX.md        ← summary + document ID listings
```

Skill names and descriptions are already available to you. Follow this workflow:

1. **Read the SKILL.md** of the 1-2 most relevant skills for your query.
2. **Drill into the most relevant sub-group**: Read its INDEX.md.
3. **At the leaf level, INDEX.md lists document IDs** with brief titles.
   Pick the most relevant document IDs.
4. **Call get_document** with each relevant doc_id to retrieve the full text.
5. **Read at least one full document** before answering.

## Tools

- **Code execution**: Use `ls` and `cat` to navigate the skills hierarchy.
- **get_document(doc_id)**: Retrieve the full text of a document by its ID.
  The doc_id values are listed in leaf-level INDEX.md files.

## Answer Format

- First sentence = direct answer. No preamble.
- Factual questions: 1-3 sentences (~80 words max).
- Procedural questions: numbered steps only (~150 words max).
- Plain text. No bold, headers, or dividers.
- One approach only. Do not present alternatives.
- Never add "contact support" or closing remarks.
"""

GET_DOCUMENT_TOOL = {
    "name": "get_document",
    "description": (
        "Retrieve the full text of a document by its ID. "
        "Document IDs are listed in the leaf-level INDEX.md files. "
        "Call this after navigating the skill hierarchy to find relevant document IDs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "description": "The document ID (hex string) from the SKILL.md listing.",
            }
        },
        "required": ["doc_id"],
    },
}

CODE_EXECUTION_TOOL = {"type": "code_execution_20250825", "name": "code_execution"}


# ---------------------------------------------------------------------------
# Skill upload
# ---------------------------------------------------------------------------

MAX_FILES_PER_SKILL = 200


def _collect_skill_files(skill_dir: Path, skills_dir: Path) -> list[tuple[str, bytes, str]]:
    """Collect all files in a skill dir as (rel_path, content, mime) tuples."""
    files = []
    for f in sorted(skill_dir.rglob("*")):
        if not f.is_file() or f.name.startswith("."):
            continue
        rel = str(f.relative_to(skills_dir))
        mime = "text/markdown" if f.suffix == ".md" else "text/plain"
        files.append((rel, f.read_bytes(), mime))
    return files


def _split_skill_files(
    skill_dir: Path, skills_dir: Path
) -> list[tuple[str, list[tuple[str, bytes, str]]]]:
    """Split a skill into chunks of <= MAX_FILES_PER_SKILL files.

    If the skill fits, returns one chunk. Otherwise splits by top-level
    sub-groups, creating multiple upload batches.
    """
    all_files = _collect_skill_files(skill_dir, skills_dir)
    if len(all_files) <= MAX_FILES_PER_SKILL:
        return [(skill_dir.name, all_files)]

    # Split by top-level sub-groups
    top_skill_md = skill_dir / "SKILL.md"
    groups = sorted([d for d in skill_dir.iterdir() if d.is_dir()])

    chunks: list[tuple[str, list[tuple[str, bytes, str]]]] = []
    current_name = f"{skill_dir.name}-part1"
    current_files: list[tuple[str, bytes, str]] = []

    # Always include the top-level SKILL.md in each part
    if top_skill_md.exists():
        top_rel = str(top_skill_md.relative_to(skills_dir))
        top_content = top_skill_md.read_bytes()
        base_file = (top_rel, top_content, "text/markdown")
    else:
        base_file = None

    if base_file:
        current_files.append(base_file)

    part_num = 1
    for group in groups:
        group_files = _collect_skill_files(group, skills_dir)
        if len(current_files) + len(group_files) > MAX_FILES_PER_SKILL and current_files:
            chunks.append((current_name, current_files))
            part_num += 1
            current_name = f"{skill_dir.name}-part{part_num}"
            current_files = [base_file] if base_file else []
        current_files.extend(group_files)

    if current_files:
        chunks.append((current_name, current_files))

    return chunks


def _upload_skills(skills_dir: Path) -> dict[str, str]:
    """Upload skill directories (SKILL.md only) to Anthropic's Skills API.

    Skills exceeding the 200-file limit are automatically split into parts.
    Returns {skill_name: skill_id}. Caches in _manifest.json.
    """
    manifest_path = skills_dir / "_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        print(f"  Skills cached ({len(manifest)} skills)")
        return manifest

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    manifest: dict[str, str] = {}

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
            continue
        if not (skill_dir / "SKILL.md").exists():
            continue

        chunks = _split_skill_files(skill_dir, skills_dir)

        import hashlib, time as _t
        for chunk_name, files in chunks:
            if not files:
                continue
            uid = hashlib.md5(f"{chunk_name}-{_t.time()}".encode()).hexdigest()[:6]
            upload_name = f"{chunk_name}-{uid}"
            print(f"  Uploading: {upload_name} ({len(files)} files)...", end=" ", flush=True)
            try:
                skill = client.beta.skills.create(
                    display_title=upload_name,
                    files=files,
                    betas=[SKILLS_BETA],
                )
                manifest[chunk_name] = skill.id
                print(f"OK (id={skill.id})")
            except Exception as e:
                print(f"FAILED: {e}")

    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"  Manifest saved ({len(manifest)} skills)")
    return manifest


# ---------------------------------------------------------------------------
# Document store
# ---------------------------------------------------------------------------

_doc_store: dict[str, str] | None = None


def _load_doc_store(output_dir: Path) -> dict[str, str]:
    """Load documents.json into memory (cached)."""
    global _doc_store
    if _doc_store is not None:
        return _doc_store

    doc_path = output_dir / "documents.json"
    if not doc_path.exists():
        raise FileNotFoundError(f"Document store not found: {doc_path}")

    print(f"  Loading document store from {doc_path}...")
    _doc_store = json.loads(doc_path.read_text(encoding="utf-8"))
    print(f"  Loaded {len(_doc_store)} documents")
    return _doc_store


def _get_document(doc_id: str, doc_store: dict[str, str]) -> str:
    """Look up a document by ID, with fuzzy prefix matching."""
    if doc_id in doc_store:
        return doc_store[doc_id]

    for full_id, text in doc_store.items():
        if full_id.startswith(doc_id) or doc_id.startswith(full_id):
            return text

    return f"Document not found: {doc_id}. Check the doc_id from the SKILL.md listing."


# ---------------------------------------------------------------------------
# Main query answering
# ---------------------------------------------------------------------------

def answer_query(
    query: str,
    skills_dir: Path,
    output_dir: Path,
    config: ServeConfig | None = None,
) -> dict[str, Any]:
    """Answer a query by hierarchical skill navigation + document retrieval.

    Args:
        query: user question
        skills_dir: path to .claude/skills/ directory
        output_dir: path to the compilation output dir (contains documents.json)
        config: optional serving config
    """
    cfg = config or ServeConfig(skills_dir=skills_dir)
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    doc_store = _load_doc_store(output_dir)

    manifest = _upload_skills(skills_dir)
    skill_ids = [
        {"type": "custom", "skill_id": sid, "version": "latest"}
        for sid in manifest.values()
    ]

    container = {"skills": skill_ids} if skill_ids else None
    tools = [CODE_EXECUTION_TOOL, GET_DOCUMENT_TOOL]

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": query}
    ]
    system_prompt = [{"type": "text", "text": SYSTEM_PROMPT}]

    skills_referenced: set[str] = set()
    docs_retrieved: list[str] = []
    docs_texts: list[str] = []  # full text of each successfully retrieved document
    tool_trace: list[dict] = []
    total_input = 0
    total_output = 0
    start = time.time()

    for turn in range(cfg.max_turns):
        try:
            create_kwargs: dict[str, Any] = dict(
                model=cfg.llm_model,
                max_tokens=8192,
                system=system_prompt,
                tools=tools,
                messages=messages,
                betas=cfg.skills_betas,
            )
            if container:
                create_kwargs["container"] = container

            response = client.beta.messages.create(**create_kwargs)
        except Exception as e:
            if "rate" in str(e).lower() or "429" in str(e):
                time.sleep(10)
                continue
            raise

        total_input += getattr(response.usage, "input_tokens", 0)
        total_output += getattr(response.usage, "output_tokens", 0)

        messages.append({"role": "assistant", "content": response.content})

        # Track skill reads from code execution results
        for block in response.content:
            btype = getattr(block, "type", "")
            if "result" in btype:
                _extract_skill_name(block, skills_referenced)

        # Handle tool_use blocks (get_document)
        tool_results = []
        for block in response.content:
            if getattr(block, "type", "") == "tool_use":
                if block.name == "get_document":
                    doc_id = block.input.get("doc_id", "")
                    doc_text = _get_document(doc_id, doc_store)
                    docs_retrieved.append(doc_id)
                    found = "not found" not in doc_text.lower()
                    if found:
                        docs_texts.append(doc_text[:6000])
                    tool_trace.append({
                        "tool": "get_document",
                        "doc_id": doc_id,
                        "found": found,
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": doc_text[:6000],
                    })
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Unknown tool: {block.name}",
                    })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
            continue

        if response.stop_reason == "end_turn":
            answer_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    answer_text += block.text

            answer_text = _clean_answer(answer_text)

            last5 = docs_texts[-5:]
            return {
                "answer": answer_text,
                "turns": turn + 1,
                "skills_referenced": sorted(skills_referenced),
                "docs_retrieved": docs_retrieved,
                "context_text": "\n\n---\n\n".join(last5),
                "tool_trace": tool_trace,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "latency": time.time() - start,
                "cost_usd": _estimate_cost(total_input, total_output, cfg.llm_model),
            }

    last5 = docs_texts[-5:]
    return {
        "answer": "Max turns reached without a final answer.",
        "turns": cfg.max_turns,
        "skills_referenced": sorted(skills_referenced),
        "docs_retrieved": docs_retrieved,
        "context_text": "\n\n---\n\n".join(last5),
        "tool_trace": tool_trace,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "latency": time.time() - start,
        "cost_usd": _estimate_cost(total_input, total_output, cfg.llm_model),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_skill_name(block: Any, skills_referenced: set[str]):
    """Extract skill/document names from code execution results."""
    try:
        c = getattr(block, "content", None)
        if c is None:
            return

        content_text = ""
        if isinstance(c, str):
            content_text = c
        elif hasattr(c, "content"):
            inner = c.content
            content_text = inner if isinstance(inner, str) else str(inner)
        elif isinstance(c, list):
            for item in c:
                if hasattr(item, "text"):
                    content_text += item.text
                elif hasattr(item, "content") and isinstance(item.content, str):
                    content_text += item.content
        else:
            content_text = str(c)

        if not content_text:
            return

        name_match = re.search(r"^name:\s*(.+)$", content_text, re.MULTILINE)
        if name_match:
            skills_referenced.add(name_match.group(1).strip())
    except Exception:
        pass


def _clean_answer(text: str) -> str:
    """Strip markdown formatting artifacts."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'^#{1,4}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^---+\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _estimate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Rough cost estimate based on model pricing."""
    if "sonnet" in model:
        return (input_tokens * 3 + output_tokens * 15) / 1_000_000
    elif "haiku" in model:
        return (input_tokens * 1 + output_tokens * 5) / 1_000_000
    return (input_tokens * 3 + output_tokens * 15) / 1_000_000

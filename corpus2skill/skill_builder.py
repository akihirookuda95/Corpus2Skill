"""Build skill folder hierarchy from cluster tree.

Converts a list of ClusterNode trees into an Anthropic-compatible
skill directory structure. Only SKILL.md files are written (no doc-*.md),
keeping each skill under the 200-file Skills API limit.

Leaf documents are referenced by ID in the SKILL.md listings.
Full document content is stored in a separate documents.json file,
served via a get_document tool at query time.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from corpus2skill.clustering import ClusterNode


_MAX_SKILL_NAME = 60


def _merge_unique_name(parent: str, child: str, max_len: int = _MAX_SKILL_NAME) -> str:
    """Combine parent + child into a globally-unique, length-bounded name.

    Anthropic's Skills API requires unique `name:` values across the tree.
    We path-qualify children by prefixing with the parent's unique name and
    truncate from the middle with a short hash to stay within limits.
    """
    combined = f"{parent}__{child}" if parent else child
    if len(combined) <= max_len:
        return combined
    import hashlib

    h = hashlib.md5(combined.encode("utf-8")).hexdigest()[:6]
    keep = max_len - len(h) - 1  # minus separator
    keep = max(keep, 1)
    head = combined[: keep // 2]
    tail = combined[-(keep - len(head)) :]
    return f"{head}~{tail}-{h}" if tail else f"{head}-{h}"


def _safe_name(text: str, prefix: str = "", max_len: int = 50) -> str:
    """Convert text to a filesystem-safe directory/file name."""
    name = text.lower().strip()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"\s+", "-", name)
    name = name.strip("-")[:max_len]
    if prefix:
        name = f"{prefix}-{name}" if name else prefix
    return name or prefix or "item"


def build_skill_tree(
    roots: list[ClusterNode],
    output_dir: Path,
    doc_content: dict[str, str] | None = None,
    compact: bool = False,
) -> Path:
    """Write the skill hierarchy to disk (SKILL.md files only).

    Also writes documents.json mapping doc_id -> full text for the
    get_document tool.

    Args:
        roots: top-level ClusterNode trees from build_hierarchy()
        output_dir: base output directory (skills go under .claude/skills/)
        doc_content: dict mapping doc_id -> full document text
        compact: if True, merge leaf-level INDEX.md files into their parent
            INDEX.md to reduce total file count (useful for deep hierarchies)

    Returns:
        Path to the skills directory.
    """
    skills_dir = output_dir / ".claude" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    for i, root in enumerate(roots):
        skill_name = _safe_name(root.label, prefix=f"skill-{i:02d}")
        root._folder_name = skill_name
        skill_dir = skills_dir / skill_name
        skill_dir.mkdir(exist_ok=True)
        _write_node(root, skill_dir, depth=0, compact=compact)

    # Write document store
    if doc_content:
        doc_store_path = output_dir / "documents.json"
        doc_store_path.write_text(
            json.dumps(doc_content, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  Document store: {doc_store_path} ({len(doc_content)} docs)")

    total_skills = sum(1 for _ in skills_dir.iterdir() if _.is_dir())
    total_nav = sum(1 for _ in skills_dir.rglob("*") if _.is_file() and _.name in ("SKILL.md", "INDEX.md"))
    print(f"  Built {total_skills} skills ({total_nav} nav files) in {skills_dir}")
    return skills_dir


def _nav_filename(depth: int) -> str:
    """Top-level gets SKILL.md; deeper levels get INDEX.md (Skills API allows only one SKILL.md)."""
    return "SKILL.md" if depth == 0 else "INDEX.md"


def _write_node(node: ClusterNode, node_dir: Path, depth: int, compact: bool = False):
    """Recursively write a node and its children."""
    if node.children:
        _write_branch_node(node, node_dir, depth, compact=compact)
    else:
        _write_leaf_node(node, node_dir, depth)


def _is_near_leaf(node: ClusterNode) -> bool:
    """Check if a node is a branch whose children are all leaves."""
    return bool(node.children) and all(not c.children for c in node.children)


def _write_branch_node(node: ClusterNode, node_dir: Path, depth: int, compact: bool = False):
    """Write SKILL.md for a branch node and recurse into children.

    In compact mode, children that are near-leaf branches (all their
    children are leaves) are merged into this node's INDEX.md as labeled
    sections rather than separate subdirectories, reducing file count.
    """
    child_entries = []
    merged_sections: list[dict] = []

    for ci, child in enumerate(node.children):
        if child.children:
            if compact and _is_near_leaf(child):
                merged_sections.append({
                    "index": ci,
                    "label": child.label or child.node_id,
                    "summary": child.summary[:200],
                    "num_docs": len(child.doc_ids),
                    "child_node": child,
                })
                child_entries.append({
                    "type": "merged_section",
                    "label": child.label or child.node_id,
                    "summary": child.summary[:200],
                    "num_docs": len(child.doc_ids),
                })
            else:
                child_name = _safe_name(child.label, prefix=f"group-{ci:02d}")
                # Make the frontmatter `name:` globally unique by qualifying
                # with the parent's folder name. Anthropic's Skills API rejects
                # duplicate skill/index names across the tree.
                parent_unique = getattr(node, "_folder_name", None) or node_dir.name
                child._folder_name = _merge_unique_name(parent_unique, child_name)
                child_dir = node_dir / child_name
                child_dir.mkdir(exist_ok=True)
                _write_node(child, child_dir, depth + 1, compact=compact)
                child_entries.append({
                    "type": "directory",
                    "name": child_name,
                    "summary": child.summary[:200],
                    "num_docs": len(child.doc_ids),
                })
        else:
            for doc_id, doc_text in zip(child.doc_ids, child.doc_texts):
                first_line = doc_text.split("\n", 1)[0].strip()[:120]
                child_entries.append({
                    "type": "doc_ref",
                    "doc_id": doc_id,
                    "title": first_line,
                    "summary": doc_text[:200],
                })

    fname = _nav_filename(depth)
    skill_md = _format_skill_md(node, child_entries, depth, merged_sections)
    (node_dir / fname).write_text(skill_md, encoding="utf-8")


def _write_leaf_node(node: ClusterNode, node_dir: Path, depth: int = 1):
    """Write INDEX.md with doc ID references (no doc files)."""
    child_entries = []
    for doc_id, doc_text in zip(node.doc_ids, node.doc_texts):
        first_line = doc_text.split("\n", 1)[0].strip()[:120]
        child_entries.append({
            "type": "doc_ref",
            "doc_id": doc_id,
            "title": first_line,
            "summary": doc_text[:200],
        })

    fname = _nav_filename(depth)
    skill_md = _format_skill_md(node, child_entries, depth)
    (node_dir / fname).write_text(skill_md, encoding="utf-8")


def _format_skill_md(
    node: ClusterNode,
    child_entries: list[dict],
    depth: int,
    merged_sections: list[dict] | None = None,
) -> str:
    """Format a SKILL.md / INDEX.md file with summary and child index."""
    folder_name = getattr(node, "_folder_name", None)
    name = folder_name if folder_name else (node.label or node.node_id)
    lines = [
        "---",
        f"name: {name}",
        "description: >",
        f"  {node.summary[:300]}",
        f"level: {node.level}",
        f"num_documents: {len(node.doc_ids)}",
        "---",
        "",
        "## Overview",
        "",
        node.summary,
        "",
        "## Contents",
        "",
    ]

    dirs = [e for e in child_entries if e["type"] == "directory"]
    docs = [e for e in child_entries if e["type"] == "doc_ref"]
    merged = [e for e in child_entries if e["type"] == "merged_section"]

    if dirs:
        lines.append("### Sub-groups (directories)")
        lines.append("")
        lines.append("Read the INDEX.md in each sub-group to understand what it covers.")
        lines.append("")
        for d in dirs:
            lines.append(f"- **{d['name']}/** ({d['num_docs']} docs): {d['summary']}")
        lines.append("")

    if docs:
        lines.append(f"### Documents ({len(docs)} items)")
        lines.append("")
        lines.append("Use `get_document` tool with the doc_id to read full content.")
        lines.append("")
        for d in docs:
            lines.append(f"- `{d['doc_id']}`: {d['title']}")
        lines.append("")

    if merged_sections:
        lines.append(f"### Merged sub-groups ({len(merged_sections)} groups below)")
        lines.append("")
        lines.append("Use `get_document` tool with any doc_id to read full content.")
        lines.append("")
        for sec in merged_sections:
            child_node = sec["child_node"]
            lines.append(f"#### {sec['label']} ({sec['num_docs']} docs)")
            lines.append("")
            lines.append(sec["summary"])
            lines.append("")
            for doc_id, doc_text in zip(child_node.doc_ids, child_node.doc_texts):
                first_line = doc_text.split("\n", 1)[0].strip()[:120]
                lines.append(f"- `{doc_id}`: {first_line}")
            lines.append("")

    return "\n".join(lines)

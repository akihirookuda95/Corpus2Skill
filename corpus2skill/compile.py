"""Corpus2Skill compiler — converts a document corpus into navigable skill hierarchy.

Usage:
    python -m corpus2skill --input wixqa_corpus/wix_kb_corpus --output c2s_output --p 10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

from corpus2skill.config import CompileConfig
from corpus2skill.clustering import build_hierarchy, tree_stats
from corpus2skill.summarizer import summarize_cluster, summarize_batch, label_cluster, label_batch
from corpus2skill.skill_builder import build_skill_tree


def load_documents(input_dir: Path, max_chars: int = 8000) -> tuple[list[str], list[str]]:
    """Load documents from a directory of text/markdown/json files.

    Returns (doc_ids, doc_texts).
    """
    doc_ids = []
    doc_texts = []

    files = sorted(input_dir.rglob("*"))
    for f in files:
        if not f.is_file():
            continue
        if f.suffix in (".md", ".txt"):
            text = f.read_text(encoding="utf-8", errors="replace")
        elif f.suffix == ".json":
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    text = data.get("text", data.get("content", json.dumps(data)))
                else:
                    text = json.dumps(data)
            except Exception:
                continue
        elif f.suffix == ".jsonl":
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                try:
                    obj = json.loads(line)
                    t = obj.get("contents", obj.get("text", obj.get("content", "")))
                    if not t:
                        continue
                    did = obj.get("id", hashlib.sha256(t[:500].encode()).hexdigest())[:16]
                    doc_ids.append(did)
                    doc_texts.append(t[:max_chars])
                except Exception:
                    continue
            continue
        else:
            continue

        if not text.strip():
            continue

        doc_id = hashlib.sha256(f.name.encode() + text[:200].encode()).hexdigest()[:16]
        doc_ids.append(doc_id)
        doc_texts.append(text[:max_chars])

    return doc_ids, doc_texts


def embed_documents(texts: list[str], model_name: str, batch_size: int = 32) -> np.ndarray:
    """Embed documents using the configured model."""
    from sentence_transformers import SentenceTransformer

    print(f"  Loading embedding model: {model_name}...")
    model = SentenceTransformer(model_name, trust_remote_code=True)
    dim = model.get_sentence_embedding_dimension()
    print(f"  Model loaded (dim={dim})")

    print(f"  Embedding {len(texts)} documents...")
    truncated = [t[:2048] for t in texts]

    kwargs = {
        "show_progress_bar": True,
        "batch_size": batch_size,
        "normalize_embeddings": True,
    }
    if "qwen3" in model_name.lower():
        if hasattr(model, "prompts") and "document" in (model.prompts or {}):
            kwargs["prompt_name"] = "document"

    embeddings = model.encode(truncated, **kwargs)
    return np.array(embeddings, dtype=np.float32)


def embed_single(text: str, model_name: str) -> np.ndarray:
    """Embed a single text (for cluster summaries)."""
    from sentence_transformers import SentenceTransformer

    _cache = getattr(embed_single, "_cache", {})
    if model_name not in _cache:
        _cache[model_name] = SentenceTransformer(model_name, trust_remote_code=True)
        embed_single._cache = _cache

    model = _cache[model_name]
    kwargs = {"normalize_embeddings": True, "show_progress_bar": False}
    if "qwen3" in model_name.lower():
        if hasattr(model, "prompts") and "document" in (model.prompts or {}):
            kwargs["prompt_name"] = "document"

    emb = model.encode([text[:2048]], **kwargs)
    return np.array(emb[0], dtype=np.float32)


def compile_corpus(cfg: CompileConfig):
    """Main compilation pipeline."""
    print(f"=" * 60)
    print(f"Corpus2Skill Compiler")
    print(f"  Input:  {cfg.input_dir}")
    print(f"  Output: {cfg.output_dir}")
    print(f"  p={cfg.p}, max_top={cfg.max_top_clusters}")
    print(f"=" * 60)

    t0 = time.time()

    # 1. Load documents
    print(f"\n[1/4] Loading documents from {cfg.input_dir}...")
    doc_ids, doc_texts = load_documents(cfg.input_dir, cfg.max_doc_chars)
    print(f"  Loaded {len(doc_ids)} documents")

    if not doc_ids:
        print("ERROR: No documents found!")
        return

    # 2. Embed documents
    print(f"\n[2/4] Embedding documents...")
    embeddings = embed_documents(doc_texts, cfg.embed_model, cfg.batch_size)
    print(f"  Embeddings shape: {embeddings.shape}")

    # 3. Hierarchical clustering
    print(f"\n[3/4] Building hierarchy (p={cfg.p})...")

    def summarize_batch_fn(items: list[list[str]], level: int = 1) -> list[str]:
        return summarize_batch(items, level=level, model=cfg.llm_model,
                               max_tokens=cfg.summary_max_tokens, concurrency=10)

    def embed_fn(text: str) -> np.ndarray:
        return embed_single(text, cfg.embed_model)

    roots = build_hierarchy(
        doc_ids=doc_ids,
        doc_texts=doc_texts,
        embeddings=embeddings,
        p=cfg.p,
        max_top=cfg.max_top_clusters,
        min_cluster_size=cfg.min_cluster_size,
        summarize_batch_fn=summarize_batch_fn,
        embed_fn=embed_fn,
    )

    stats = tree_stats(roots)
    print(f"  Hierarchy: {stats}")

    # Label all non-leaf nodes concurrently
    all_to_label = []
    for root in roots:
        all_to_label.append(root)
        for child in root.children:
            if child.children:
                all_to_label.append(child)

    print(f"\n  Labeling {len(all_to_label)} clusters concurrently...")
    summaries = [n.summary for n in all_to_label]
    labels = label_batch(summaries, model=cfg.llm_model, concurrency=10)
    for node, label in zip(all_to_label, labels):
        node.label = label
        if node.level == max(n.level for n in roots):
            print(f"    {node.node_id} → {label} ({len(node.doc_ids)} docs)")

    # 4. Build skill directories
    print(f"\n[4/4] Building skill directories...")
    doc_content = dict(zip(doc_ids, doc_texts))
    skills_dir = build_skill_tree(roots, cfg.output_dir, doc_content, compact=cfg.compact)

    elapsed = time.time() - t0

    # Save compilation metadata
    meta = {
        "input_dir": str(cfg.input_dir),
        "output_dir": str(cfg.output_dir),
        "p": cfg.p,
        "max_top_clusters": cfg.max_top_clusters,
        "num_documents": len(doc_ids),
        "hierarchy": stats,
        "skills": [{"name": r.label, "num_docs": len(r.doc_ids)} for r in roots],
        "embed_model": cfg.embed_model,
        "llm_model": cfg.llm_model,
        "elapsed_seconds": elapsed,
    }
    meta_path = cfg.output_dir / "compile_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    print(f"\n{'=' * 60}")
    print(f"Compilation complete in {elapsed:.1f}s")
    print(f"  Skills: {len(roots)}")
    print(f"  Documents: {len(doc_ids)}")
    print(f"  Hierarchy depth: {stats['max_depth']}")
    print(f"  Output: {skills_dir}")
    print(f"  Metadata: {meta_path}")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description="Corpus2Skill compiler")
    parser.add_argument("--input", type=Path, required=True, help="Input document directory")
    parser.add_argument("--output", type=Path, default=Path("c2s_output"), help="Output directory")
    parser.add_argument("--p", type=int, default=10, help="Branching ratio (default: 10)")
    parser.add_argument("--max-top", type=int, default=8, help="Max top-level clusters")
    parser.add_argument("--model", type=str, default="claude-sonnet-4-6", help="LLM model")
    parser.add_argument("--embed-model", type=str, default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--compact", action="store_true",
                        help="Merge leaf INDEX.md into parent to reduce file count")
    args = parser.parse_args()

    cfg = CompileConfig(
        input_dir=args.input,
        output_dir=args.output,
        p=args.p,
        max_top_clusters=args.max_top,
        llm_model=args.model,
        embed_model=args.embed_model,
        compact=args.compact,
    )
    compile_corpus(cfg)


if __name__ == "__main__":
    main()

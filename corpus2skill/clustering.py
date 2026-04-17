"""Hierarchical clustering with branching ratio p.

Builds a tree bottom-up:
  Level 0: n documents (leaves)
  Level 1: ceil(n/p) clusters of ~p documents each
  Level 2: ceil(n/p²) clusters of ~p level-1 clusters each
  ...
  Level t: stop when num_clusters <= max_top

At each level, clusters are formed by K-means on embeddings.
After clustering, an LLM summary is generated for each cluster,
and that summary's embedding becomes the data point for the next level.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ClusterNode:
    """A node in the hierarchy tree."""
    node_id: str
    level: int
    label: str = ""
    summary: str = ""
    embedding: np.ndarray | None = None
    children: list[ClusterNode] = field(default_factory=list)
    doc_ids: list[str] = field(default_factory=list)
    doc_texts: list[str] = field(default_factory=list)


def cluster_level(embeddings: np.ndarray, k: int, min_size: int = 1) -> list[list[int]]:
    """Run K-means on embeddings, returning list of index-lists per cluster.

    Falls back to agglomerative if K-means produces empty clusters.
    """
    from sklearn.cluster import KMeans, AgglomerativeClustering

    n = embeddings.shape[0]
    k = min(k, n)
    if k <= 1:
        return [list(range(n))]

    normed = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-10)

    try:
        km = KMeans(n_clusters=k, n_init=3, max_iter=100, random_state=42)
        labels = km.fit_predict(normed)
    except Exception:
        agg = AgglomerativeClustering(n_clusters=k)
        labels = agg.fit_predict(normed)

    clusters: dict[int, list[int]] = {}
    for idx, lab in enumerate(labels):
        clusters.setdefault(lab, []).append(idx)

    result = [v for v in clusters.values() if len(v) >= min_size]

    orphans = []
    for v in clusters.values():
        if len(v) < min_size:
            orphans.extend(v)

    if orphans and result:
        centroids = []
        for cl in result:
            c_emb = normed[cl].mean(axis=0)
            centroids.append(c_emb / (np.linalg.norm(c_emb) + 1e-10))
        centroids = np.array(centroids)

        for oidx in orphans:
            sims = centroids @ normed[oidx]
            best = int(np.argmax(sims))
            result[best].append(oidx)

    return result


def build_hierarchy(
    doc_ids: list[str],
    doc_texts: list[str],
    embeddings: np.ndarray,
    p: int = 10,
    max_top: int = 8,
    min_cluster_size: int = 3,
    summarize_fn=None,
    summarize_batch_fn=None,
    embed_fn=None,
) -> list[ClusterNode]:
    """Build a multi-level cluster hierarchy.

    Args:
        doc_ids: document identifiers
        doc_texts: document text content
        embeddings: (n, dim) array of document embeddings
        p: branching ratio — each cluster has ~p children
        max_top: stop when num_clusters <= max_top
        min_cluster_size: minimum items per cluster
        summarize_fn: callable(texts: list[str], level: int) -> str
        summarize_batch_fn: callable(batch: list[list[str]], level: int) -> list[str]
        embed_fn: callable(text: str) -> np.ndarray — embed a summary

    Returns:
        list of top-level ClusterNode trees
    """
    n = len(doc_ids)
    assert n == embeddings.shape[0]

    leaf_nodes = []
    for i, (did, txt) in enumerate(zip(doc_ids, doc_texts)):
        leaf_nodes.append(ClusterNode(
            node_id=f"doc-{did[:12]}",
            level=0,
            label=did,
            summary=txt[:500],
            embedding=embeddings[i],
            doc_ids=[did],
            doc_texts=[txt],
        ))

    current_nodes = leaf_nodes
    current_embeddings = embeddings.copy()
    level = 0

    while len(current_nodes) > max_top:
        level += 1
        k = max(max_top, math.ceil(len(current_nodes) / p))
        k = min(k, len(current_nodes))

        print(f"  Level {level}: clustering {len(current_nodes)} items → {k} clusters")

        assignments = cluster_level(current_embeddings, k, min_cluster_size)

        if not assignments:
            break

        cluster_children_list = []
        cluster_doc_ids_list = []
        cluster_doc_texts_list = []
        summary_inputs = []

        for member_indices in assignments:
            children = [current_nodes[i] for i in member_indices]
            all_doc_ids = []
            all_doc_texts = []
            for child in children:
                all_doc_ids.extend(child.doc_ids)
                all_doc_texts.extend(child.doc_texts)
            cluster_children_list.append(children)
            cluster_doc_ids_list.append(all_doc_ids)
            cluster_doc_texts_list.append(all_doc_texts)
            summary_inputs.append([c.summary for c in children])

        if summarize_batch_fn:
            print(f"    Summarizing {len(assignments)} clusters concurrently...")
            summaries = summarize_batch_fn(summary_inputs, level=level)
        elif summarize_fn:
            summaries = [summarize_fn(si, level=level) for si in summary_inputs]
        else:
            summaries = [
                f"Cluster of {len(ch)} items with {len(di)} documents."
                for ch, di in zip(cluster_children_list, cluster_doc_ids_list)
            ]

        next_nodes = []
        next_embeddings = []

        for ci, (children, all_doc_ids, all_doc_texts, summary) in enumerate(
            zip(cluster_children_list, cluster_doc_ids_list,
                cluster_doc_texts_list, summaries)
        ):
            if embed_fn:
                emb = embed_fn(summary)
            else:
                member_embs = current_embeddings[assignments[ci]]
                emb = member_embs.mean(axis=0)
                emb = emb / (np.linalg.norm(emb) + 1e-10)

            node = ClusterNode(
                node_id=f"L{level}-C{ci}",
                level=level,
                summary=summary,
                embedding=emb,
                children=children,
                doc_ids=all_doc_ids,
                doc_texts=all_doc_texts,
            )
            next_nodes.append(node)
            next_embeddings.append(emb)

        if not next_nodes:
            break

        current_nodes = next_nodes
        current_embeddings = np.array(next_embeddings, dtype=np.float32)
        print(f"    → {len(current_nodes)} clusters (total docs per cluster: "
              f"min={min(len(n.doc_ids) for n in current_nodes)}, "
              f"max={max(len(n.doc_ids) for n in current_nodes)}, "
              f"mean={sum(len(n.doc_ids) for n in current_nodes)/len(current_nodes):.0f})")

    return current_nodes


def tree_stats(roots: list[ClusterNode]) -> dict:
    """Compute statistics about the hierarchy tree."""
    def _depth(node: ClusterNode) -> int:
        if not node.children:
            return 0
        return 1 + max(_depth(c) for c in node.children)

    def _count_nodes(node: ClusterNode) -> int:
        return 1 + sum(_count_nodes(c) for c in node.children)

    max_depth = max(_depth(r) for r in roots)
    total_nodes = sum(_count_nodes(r) for r in roots)
    total_docs = sum(len(r.doc_ids) for r in roots)

    return {
        "num_top_clusters": len(roots),
        "max_depth": max_depth,
        "total_nodes": total_nodes,
        "total_documents": total_docs,
    }

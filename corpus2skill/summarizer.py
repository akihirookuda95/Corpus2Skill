"""LLM-based cluster summarization and labeling.

Generates concise summaries at each hierarchy level.
Lower levels summarize document content; higher levels summarize child summaries.
Supports concurrent API calls for throughput.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import anthropic


_client: anthropic.Anthropic | None = None
_async_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        _client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _client


def _get_async_client() -> anthropic.AsyncAnthropic:
    global _async_client
    if _async_client is None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        _async_client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _async_client


def summarize_cluster(
    child_texts: list[str],
    level: int = 1,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 300,
) -> str:
    """Summarize a cluster's children into a concise description."""
    if level == 1:
        prompt = _doc_summary_prompt(child_texts)
    else:
        prompt = _cluster_summary_prompt(child_texts, level)

    client = _get_client()

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text_parts = [getattr(b, "text", "") for b in (resp.content or [])]
            text = "".join(p for p in text_parts if p).strip()
            if text:
                return text
            if attempt < 2:
                continue
            return f"Cluster of {len(child_texts)} items."
        except Exception as e:
            if "rate" in str(e).lower() or "429" in str(e):
                time.sleep(5 * (attempt + 1))
                continue
            raise

    return f"Cluster of {len(child_texts)} items."


async def _async_summarize_one(
    child_texts: list[str],
    level: int,
    model: str,
    max_tokens: int,
    semaphore: asyncio.Semaphore,
) -> str:
    """Single async summarization call with concurrency control."""
    prompt = _doc_summary_prompt(child_texts) if level == 1 else _cluster_summary_prompt(child_texts, level)
    client = _get_async_client()

    async with semaphore:
        for attempt in range(5):
            try:
                resp = await client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                text_parts: list[str] = []
                for block in resp.content or []:
                    t = getattr(block, "text", None)
                    if t:
                        text_parts.append(t)
                text = "".join(text_parts).strip()
                if text:
                    return text
                # Empty / refused response — retry a couple of times before
                # falling back to a boilerplate summary so the compile doesn't die.
                if attempt < 4:
                    await asyncio.sleep(2)
                    continue
                return f"Cluster of {len(child_texts)} items."
            except Exception as e:
                err = str(e).lower()
                if "rate" in err or "429" in err or "connection" in err or "overloaded" in err:
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                if attempt < 4:
                    await asyncio.sleep(2)
                    continue
                raise

    return f"Cluster of {len(child_texts)} items."


def summarize_batch(
    items: list[list[str]],
    level: int = 1,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 300,
    concurrency: int = 20,
) -> list[str]:
    """Summarize multiple clusters concurrently.

    Args:
        items: list of child_texts lists, one per cluster
        concurrency: max parallel API calls
    """
    async def _run():
        sem = asyncio.Semaphore(concurrency)
        tasks = [
            _async_summarize_one(texts, level, model, max_tokens, sem)
            for texts in items
        ]
        return await asyncio.gather(*tasks)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            return list(pool.submit(asyncio.run, _run()).result())
    else:
        return list(asyncio.run(_run()))


async def _async_label_one(
    summary: str,
    model: str,
    semaphore: asyncio.Semaphore,
) -> str:
    """Single async label call."""
    client = _get_async_client()
    async with semaphore:
        for attempt in range(4):
            try:
                resp = await client.messages.create(
                    model=model,
                    max_tokens=20,
                    messages=[{"role": "user", "content": (
                        f"Generate a short (2-5 word) filesystem-safe label for this cluster. "
                        f"Use lowercase, hyphens instead of spaces. No quotes.\n\n"
                        f"Summary: {summary[:500]}"
                    )}],
                )
                text_parts = [getattr(b, "text", "") for b in (resp.content or [])]
                raw = "".join(p for p in text_parts if p).strip().lower()
                label = "".join(c if c.isalnum() or c == "-" else "-" for c in raw)
                label = label.strip("-")[:50]
                return label or "cluster"
            except Exception:
                if attempt < 3:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                return "cluster"
    return "cluster"


def label_batch(
    summaries: list[str],
    model: str = "claude-sonnet-4-6",
    concurrency: int = 20,
) -> list[str]:
    """Label multiple clusters concurrently."""
    async def _run():
        sem = asyncio.Semaphore(concurrency)
        tasks = [_async_label_one(s, model, sem) for s in summaries]
        return await asyncio.gather(*tasks)

    return list(asyncio.run(_run()))


def label_cluster(summary: str, model: str = "claude-sonnet-4-6") -> str:
    """Generate a short filesystem-safe label from a summary."""
    return label_batch([summary], model)[0]


def _doc_summary_prompt(doc_texts: list[str]) -> str:
    """Prompt for summarizing a group of documents."""
    combined = ""
    for i, text in enumerate(doc_texts[:15]):
        combined += f"\n--- Document {i+1} ---\n{text[:600]}\n"

    return (
        "You are summarizing a cluster of related documents from a knowledge base. "
        "Write a 2-3 sentence summary that captures:\n"
        "1. The common TOPIC area these documents cover\n"
        "2. The types of QUESTIONS these documents answer\n"
        "3. Key TERMS or features mentioned across documents\n\n"
        "Be specific and concrete. Name actual features, products, or processes.\n\n"
        f"Documents ({len(doc_texts)} total, showing up to 15):\n{combined}\n\n"
        "Summary:"
    )


def _cluster_summary_prompt(child_summaries: list[str], level: int) -> str:
    """Prompt for summarizing a group of sub-cluster summaries."""
    combined = ""
    for i, s in enumerate(child_summaries[:20]):
        combined += f"\n- Sub-group {i+1}: {s[:300]}\n"

    return (
        f"You are summarizing a level-{level} grouping of {len(child_summaries)} "
        f"sub-groups from a knowledge base. Each sub-group already has a summary below.\n\n"
        "Write a 2-3 sentence overview that captures:\n"
        "1. The broad DOMAIN these sub-groups cover\n"
        "2. The range of TOPICS within this domain\n"
        "3. What types of user QUESTIONS this group can answer\n\n"
        "Be specific — name the main product areas, features, or workflows.\n\n"
        f"Sub-group summaries:\n{combined}\n\n"
        "Overview:"
    )

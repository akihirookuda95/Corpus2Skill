"""Configuration for Corpus2Skill compilation and serving."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CompileConfig:
    input_dir: Path = Path("wixqa_corpus/wix_kb_corpus")
    output_dir: Path = Path("c2s_output")

    p: int = 10
    max_top_clusters: int = 10
    min_cluster_size: int = 3

    embed_model: str = "Qwen/Qwen3-Embedding-0.6B"
    llm_model: str = "claude-sonnet-4-6"

    max_doc_chars: int = 8000
    summary_max_tokens: int = 300
    batch_size: int = 32
    compact: bool = False


@dataclass
class ServeConfig:
    skills_dir: Path = Path("c2s_output/.claude/skills")
    llm_model: str = "claude-sonnet-4-6"
    max_turns: int = 20

    skills_betas: list[str] = field(default_factory=lambda: [
        "code-execution-2025-08-25",
        "skills-2025-10-02",
    ])

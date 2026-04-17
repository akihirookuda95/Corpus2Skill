# Corpus2Skill

**Distill any document corpus into a navigable skill hierarchy for LLM agents — no retrieval system needed at serve time.**

This is the official implementation of the paper [**"Don't Retrieve, Navigate: Distilling Enterprise Knowledge into Navigable Agent Skills for QA and RAG"**](https://arxiv.org/abs/2604.14572) (Sun, Wei, and Hsieh, 2026).

Corpus2Skill converts a collection of documents into a structured tree of [Anthropic Skills](https://docs.anthropic.com/en/docs/build-with-claude/skills). At query time, the LLM agent navigates this hierarchy (reading SKILL.md / INDEX.md files, drilling into sub-topics) and fetches full documents on demand — without embeddings, vector stores, or BM25 at serve time.

> **🚧 Work in Progress** — This is an early release. The core pipeline works end-to-end, but rough edges remain. We're actively improving it and would love your feedback! Please [open an issue](../../issues) if you run into problems or have suggestions.

## How It Works

```
Documents ──> Embed + Cluster ──> Summarize & Label ──> Skill Tree (.claude/)
(any text)                                                      |
                                                                v
                                                         LLM Agent navigates
                                                         hierarchy at query time
```

**Compile time** — documents are embedded, clustered hierarchically, and summarized by an LLM into a skill tree with navigable index files.

**Serve time** — given a question, the LLM reads top-level skill descriptions, drills into the most relevant branch, finds document IDs at leaf nodes, and retrieves full text via a `get_document` tool. No vector DB, no retrieval index.

## Quick Start

### 1. Install

```bash
pip install -e .
```

### 2. Set your API key

```bash
cp .env.example .env
# Edit .env and add your Anthropic API key
```

### 3. Prepare a corpus

Your documents can be a directory of `.txt`, `.md`, or `.json` files, or a single `.jsonl` file where each line has an `id` and `contents` (or `text`) field.

To try with the [WixQA](https://huggingface.co/datasets/Wix/WixQA) benchmark:

```bash
python scripts/prepare_wixqa.py --output ./wixqa_corpus
```

### 4. Compile

```bash
python -m corpus2skill \
    --input ./wixqa_corpus/wix_kb_corpus \
    --output ./c2s_compiled \
    --p 10
```

**Key flags:**
- `--p` — branching ratio (how many children per cluster node; default 10)
- `--max-top` — maximum top-level skills (default 8)
- `--model` — LLM for summarization (default `claude-sonnet-4-6`)
- `--embed-model` — embedding model (default `Qwen/Qwen3-Embedding-0.6B`)
- `--compact` — merge leaf INDEX.md into parent to reduce file count

### 5. Query

```python
from corpus2skill.serve import answer_query
from corpus2skill.config import ServeConfig
from pathlib import Path

output_dir = Path("./c2s_compiled")
skills_dir = output_dir / ".claude" / "skills"
config = ServeConfig(skills_dir=skills_dir)

result = answer_query(
    "How do I add a custom domain to my site?",
    skills_dir=skills_dir,
    output_dir=output_dir,
    config=config,
)

print(result["answer"])
```

### 6. Evaluate

```bash
python -m corpus2skill.eval \
    --output-dir ./c2s_compiled \
    --qa ./wixqa_corpus/wixqa_expertwritten.jsonl \
    --output eval_results.json
```

Metrics reported: F1, BLEU, ROUGE-1, ROUGE-2, Factuality (LLM judge), Context Recall (LLM judge).

## Project Structure

```
corpus2skill/
├── __init__.py        # Package entry
├── __main__.py        # python -m corpus2skill
├── config.py          # CompileConfig & ServeConfig dataclasses
├── compile.py         # Compilation pipeline (embed → cluster → summarize → build)
├── clustering.py      # Hierarchical K-means / agglomerative clustering
├── summarizer.py      # Async LLM summarization & labeling
├── skill_builder.py   # Writes SKILL.md / INDEX.md / documents.json
├── serve.py           # Serve-time agent (Skills API + get_document tool)
├── metrics.py         # Evaluation metrics (F1, BLEU, ROUGE, LLM judges)
└── eval.py            # Evaluation harness
scripts/
└── prepare_wixqa.py   # Download & prepare WixQA benchmark data
```

## Requirements

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/) (for compilation and serving)
- ~2 GB disk for the default embedding model on first run

## How Compilation Works

1. **Load** — reads `.jsonl`, `.txt`, `.md`, or `.json` documents from the input directory
2. **Embed** — encodes documents using a sentence-transformer model
3. **Cluster** — builds a hierarchical tree via recursive K-means with agglomerative merging of small clusters
4. **Summarize** — LLM generates a summary for each cluster node
5. **Label** — LLM produces short topic labels for navigation
6. **Build** — writes the skill tree (`SKILL.md`, `INDEX.md` at each level) plus a `documents.json` store for full-text retrieval

The output lives under `<output_dir>/.claude/skills/` and can be uploaded to Anthropic's Skills API.

## Contributing

This project is in active development. Contributions, bug reports, and feature requests are very welcome!

- **Found a bug?** [Open an issue](../../issues)
- **Have an idea?** [Start a discussion](../../issues)
- **Want to contribute?** PRs are welcome — please open an issue first to discuss larger changes

## Citation

If you use Corpus2Skill in your research, please cite:

```bibtex
@misc{sun2026dontretrievenavigatedistilling,
      title={Don't Retrieve, Navigate: Distilling Enterprise Knowledge into Navigable Agent Skills for QA and RAG},
      author={Yiqun Sun and Pengfei Wei and Lawrence B. Hsieh},
      year={2026},
      eprint={2604.14572},
      archivePrefix={arXiv},
      primaryClass={cs.IR},
      url={https://arxiv.org/abs/2604.14572},
}
```

Paper: [arXiv:2604.14572](https://arxiv.org/abs/2604.14572)

"""Evaluate Corpus2Skill on a QA dataset.

Usage:
    python -m corpus2skill.eval \
        --output-dir c2s_output \
        --qa wixqa_corpus/eval_sample_50.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from corpus2skill.metrics import (
    compute_f1, compute_bleu, compute_rouge,
    judge_factuality, judge_context_recall,
)
from corpus2skill.serve import answer_query
from corpus2skill.config import ServeConfig


def score_result(
    question: str,
    gold_answer: str,
    result: dict,
    judge_client,
    judge_model: str,
) -> dict:
    """Score a single result against the gold answer."""
    predicted = result["answer"]

    f1 = compute_f1(predicted, gold_answer)
    bleu = compute_bleu(predicted, gold_answer)
    rouge = compute_rouge(predicted, gold_answer)

    fact_result = judge_factuality(
        question, predicted, gold_answer, judge_client, judge_model
    )
    context_text = result.get("context_text", "")
    if not context_text:
        context_text = "(no documents retrieved)"
    ctx_result = judge_context_recall(
        question, gold_answer, context_text, judge_client, judge_model
    )

    return {
        "f1": f1,
        "bleu": bleu,
        "rouge1": rouge["rouge1"],
        "rouge2": rouge["rouge2"],
        "factuality": fact_result.get("score_01", 0),
        "context_recall": ctx_result.get("score_01", 0),
        "turns": result["turns"],
        "latency": result["latency"],
        "cost_usd": result.get("cost_usd", 0),
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
    }


def aggregate(scored_list: list[dict]) -> dict:
    """Compute aggregate metrics."""
    if not scored_list:
        return {}
    metrics = ["f1", "bleu", "rouge1", "rouge2", "factuality", "context_recall",
               "turns", "latency", "cost_usd"]
    agg = {}
    n = len(scored_list)
    for m in metrics:
        vals = [s.get(m, 0) for s in scored_list]
        agg[m] = sum(vals) / n if vals else 0
    agg["total_input_tokens"] = sum(s.get("input_tokens", 0) for s in scored_list)
    agg["total_output_tokens"] = sum(s.get("output_tokens", 0) for s in scored_list)
    agg["total_cost_usd"] = round(sum(s.get("cost_usd", 0) for s in scored_list), 6)
    agg["count"] = n
    return agg


def main():
    parser = argparse.ArgumentParser(description="Evaluate Corpus2Skill")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Compilation output dir (contains .claude/skills/ and documents.json)")
    parser.add_argument("--qa", type=Path, required=True, help="QA JSONL file")
    parser.add_argument("--model", type=str, default="claude-sonnet-4-6")
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    import anthropic
    import os
    from dotenv import load_dotenv
    load_dotenv()
    judge_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    output_dir = args.output_dir
    skills_dir = output_dir / ".claude" / "skills"

    qa_pairs = []
    with open(args.qa) as f:
        for line in f:
            qa_pairs.append(json.loads(line))

    cfg = ServeConfig(skills_dir=skills_dir, llm_model=args.model, max_turns=args.max_turns)

    print(f"Evaluating {len(qa_pairs)} queries...")
    print(f"  Output dir: {output_dir}")
    print(f"  Skills dir: {skills_dir}")
    print(f"  Model: {args.model}")

    scored_list = []
    errors = []
    skill_usage = {}
    total_cost = 0.0

    for i, qa in enumerate(qa_pairs):
        q = qa["question"]
        gold = qa["answer"]

        print(f"\n[{i+1}/{len(qa_pairs)}] {q[:70]}...")
        t0 = time.time()
        try:
            result = answer_query(q, skills_dir, output_dir, cfg)
            scored = score_result(q, gold, result, judge_client, args.model)
            elapsed = time.time() - t0

            query_cost = result.get("cost_usd", 0)
            total_cost += query_cost

            scored["question"] = q
            scored["predicted_answer"] = result["answer"][:3000]
            scored["gold_answer"] = gold[:2000]
            scored["method"] = "corpus2skill"
            scored["skills_referenced"] = result.get("skills_referenced", [])
            scored["docs_retrieved"] = result.get("docs_retrieved", [])
            scored["cost_usd"] = query_cost
            scored["input_tokens"] = result.get("input_tokens", 0)
            scored["output_tokens"] = result.get("output_tokens", 0)
            scored_list.append(scored)

            skills_used = result.get("skills_referenced", [])
            for sk in skills_used:
                skill_usage[sk] = skill_usage.get(sk, 0) + 1

            n_docs = len(result.get("docs_retrieved", []))
            anslen = len(result["answer"])
            print(f"  F1={scored['f1']:.3f} Fact={scored['factuality']:.2f} "
                  f"CtxR={scored['context_recall']:.2f} "
                  f"turns={scored['turns']} docs={n_docs} "
                  f"skills={','.join(skills_used[:3]) if skills_used else 'none'} "
                  f"anslen={anslen} cost=${query_cost:.4f} ({elapsed:.1f}s) "
                  f"[cumul=${total_cost:.3f}]")
        except Exception as e:
            elapsed = time.time() - t0
            tb = traceback.format_exc()
            print(f"  ERROR ({elapsed:.1f}s): {e}")
            errors.append({"index": i, "question": q, "error": str(e), "traceback": tb})
            scored_list.append({
                "question": q, "gold_answer": gold, "predicted_answer": f"ERROR: {e}",
                "f1": 0, "bleu": 0, "rouge1": 0, "rouge2": 0,
                "factuality": 0, "context_recall": 0,
                "turns": 0, "latency": 0, "method": "corpus2skill",
                "error_detail": str(e), "cost_usd": 0,
                "input_tokens": 0, "output_tokens": 0,
            })

    agg = aggregate([s for s in scored_list if "error_detail" not in s])

    output = {
        "config": {"model": args.model, "num_queries": len(qa_pairs),
                   "version": "corpus2skill", "max_turns": args.max_turns},
        "aggregate": agg,
        "cost_stats": {
            "total_cost_usd": total_cost,
            "avg_cost_per_query": total_cost / max(len(scored_list), 1),
        },
        "per_query": scored_list,
        "errors": errors,
        "skill_usage": skill_usage,
    }

    out_path = args.output or Path(f"c2s_eval_{int(time.time())}.json")
    out_path.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nResults saved to {out_path}")

    print(f"\n{'='*70}")
    print(f"AGGREGATE ({len(scored_list)} queries, {len(errors)} errors):")
    for k in ["f1", "bleu", "rouge1", "rouge2", "factuality", "context_recall",
              "turns", "latency", "cost_usd"]:
        v = agg.get(k, 'N/A')
        print(f"  {k}: {v:.4f}" if isinstance(v, (int, float)) else f"  {k}: {v}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()

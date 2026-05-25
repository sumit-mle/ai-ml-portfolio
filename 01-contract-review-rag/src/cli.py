"""CLI for the contract-review RAG project.

Subcommands:
    ask     run a single question through one technique
    ingest  download / cache the full CUAD dataset (otherwise we use the sample)
    eval    run the golden Q/A set against one technique and write results

The CLI keeps imports lazy so that, e.g., `python -m src.cli ask --backend
langchain ...` does not require the LlamaIndex stack to be installed, and vice
versa.
"""
from __future__ import annotations

import argparse
import sys
from importlib import import_module

from dotenv import load_dotenv

from .shared.corpus import load_corpus

LC_TECHNIQUES = ["naive", "hybrid", "rerank", "multi_query", "hyde"]
LI_TECHNIQUES = ["naive", "sentence_window", "auto_merging", "hybrid_fusion", "rerank"]


def _technique_module(backend: str, technique: str):
    return import_module(f"src.{backend}_impl.{technique}")


def cmd_ask(args: argparse.Namespace) -> int:
    contracts = load_corpus(full=args.full, max_contracts=args.max_contracts)
    mod = _technique_module(args.backend, args.technique)
    result = mod.run(args.question, contracts, top_k=args.top_k)

    retrieved = getattr(result, "retrieved", [])
    answer = getattr(result, "answer", str(result))
    technique = getattr(result, "technique", f"{args.backend}.{args.technique}")

    print(f"\nTechnique: {technique}")
    print(f"Question : {args.question}\n")
    print("Retrieved:")
    for c in retrieved:
        title = (c.title or "").split("(")[0].strip()[:60]
        print(f"  - {c.chunk_id}  [{c.section}]  ({title})")
    print("\nAnswer:")
    print(answer)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    print("Downloading CUAD v1 from Hugging Face (cached after first run) ...")
    contracts = load_corpus(full=True, max_contracts=args.max_contracts)
    print(f"Loaded {len(contracts)} contracts.")
    total_labels = sum(
        sum(len(v) for v in c.labels.values()) for c in contracts
    )
    print(f"Loaded {total_labels} labeled clause spans.")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.runner import run_eval

    contracts = load_corpus(full=args.full, max_contracts=args.max_contracts)
    summary = run_eval(
        contracts,
        backend=args.backend,
        technique=args.technique,
        top_k=args.top_k,
    )
    print(f"\nEval summary for {summary['backend']}.{summary['technique']}")
    print(f"  questions          : {summary['n_questions']}")
    print(f"  clause_match_rate  : {summary['clause_match_rate']:.2f}")
    print(f"  citation_correct   : {summary['citation_correct_rate']:.2f}")
    print(f"  answer_quotes_rate : {summary['answer_quotes_rate']:.2f}")
    print(f"  results written to : results/{summary['backend']}__{summary['technique']}.json")
    return 0


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--backend", choices=["langchain", "llamaindex"], default="langchain")
    p.add_argument("--technique", default="naive")
    p.add_argument("--top_k", type=int, default=4)
    p.add_argument("--full", action="store_true",
                   help="Use the full CUAD dataset (downloads on first use).")
    p.add_argument("--max_contracts", type=int, default=None,
                   help="Cap number of contracts when --full is set.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="contract-review-rag")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="ask a single question")
    _add_common(ask)
    ask.add_argument("--question", required=True)

    ingest = sub.add_parser("ingest", help="download/cache CUAD")
    ingest.add_argument("--max_contracts", type=int, default=None)

    ev = sub.add_parser("eval", help="run the golden Q/A set")
    _add_common(ev)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    valid = LC_TECHNIQUES if getattr(args, "backend", "langchain") == "langchain" else LI_TECHNIQUES
    if hasattr(args, "technique") and args.technique not in valid:
        print(
            f"Unknown technique '{args.technique}' for backend '{args.backend}'. "
            f"Valid: {', '.join(valid)}",
            file=sys.stderr,
        )
        return 2

    try:
        if args.cmd == "ask":
            return cmd_ask(args)
        if args.cmd == "ingest":
            return cmd_ingest(args)
        if args.cmd == "eval":
            return cmd_eval(args)
    except NotImplementedError as exc:
        print(f"Technique not implemented yet: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

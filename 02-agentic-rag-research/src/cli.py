"""CLI for the agentic-RAG research assistant.

Subcommands:
    ask     run a single question through the LangGraph agent
    ingest  fetch a PubMed query into a local cache (sanity check)
    eval    run the golden Q/A set, write results JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from .shared.corpus import Abstract, load_corpus, load_full, load_sample


def _load_abstracts(args: argparse.Namespace) -> list[Abstract]:
    if args.full:
        return load_full(args.query, retmax=args.retmax)
    return load_sample()


def cmd_ask(args: argparse.Namespace) -> int:
    from .agent.graph import run_agent

    abstracts = _load_abstracts(args)
    result = run_agent(
        args.question,
        abstracts,
        top_k=args.top_k,
        max_iterations=args.max_iterations,
    )

    print(f"\nQuestion : {args.question}")
    print(f"Iterations: {result.iterations}")
    print("\nTrace:")
    for line in result.trace:
        print(f"  {line}")
    print("\nRetrieved (deduped):")
    for d in result.retrieved:
        venue = f"{d.journal} {d.year}".strip()
        print(f"  - PMID {d.pmid}  ({venue})  {d.title[:80]}")
    print("\nCritique:")
    if result.critique:
        c = result.critique
        print(
            f"  grounded={c.get('grounded')}  cited={c.get('cited')}  "
            f"complete={c.get('complete')}  needs_more={c.get('needs_more_evidence')}"
        )
        if c.get("critique"):
            print(f"  note: {c.get('critique')}")
    print("\nAnswer:")
    print(result.final_answer)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from .shared.corpus import fetch_abstracts, search_pubmed

    pmids = search_pubmed(args.query, retmax=args.retmax)
    print(f"esearch '{args.query}' -> {len(pmids)} PMIDs")
    abstracts = fetch_abstracts(pmids)
    print(f"efetch -> {len(abstracts)} abstracts with non-empty text")

    out_dir = Path("data/pubmed")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in args.query)[:60]
    out = out_dir / f"{safe}.json"
    out.write_text(
        json.dumps(
            [a.__dict__ for a in abstracts],
            indent=2,
        )
    )
    print(f"wrote {out}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.runner import run_eval

    abstracts = _load_abstracts(args)
    summary = run_eval(
        abstracts,
        top_k=args.top_k,
        max_iterations=args.max_iterations,
        label=args.label,
    )
    print("\nEval summary")
    for k, v in summary.items():
        if isinstance(v, float):
            print(f"  {k:22s} : {v:.2f}")
        else:
            print(f"  {k:22s} : {v}")
    print(f"  results written to    : results/{summary['label']}.json")
    return 0


def _add_corpus_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--full",
        action="store_true",
        help="Use a live PubMed search instead of the built-in synthetic sample.",
    )
    p.add_argument(
        "--query",
        default="",
        help="PubMed search query. Required with --full.",
    )
    p.add_argument(
        "--retmax",
        type=int,
        default=20,
        help="Max PubMed results when --full.",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agentic-rag-research")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="ask a single question")
    ask.add_argument("--question", required=True)
    ask.add_argument("--top_k", type=int, default=5)
    ask.add_argument("--max_iterations", type=int, default=2)
    _add_corpus_args(ask)

    ingest = sub.add_parser("ingest", help="fetch a PubMed query to local cache")
    ingest.add_argument("--query", required=True)
    ingest.add_argument("--retmax", type=int, default=20)

    ev = sub.add_parser("eval", help="run the golden Q/A set")
    ev.add_argument("--top_k", type=int, default=5)
    ev.add_argument("--max_iterations", type=int, default=2)
    ev.add_argument("--label", default="agentic_rag_sample")
    _add_corpus_args(ev)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    try:
        if args.cmd == "ask":
            return cmd_ask(args)
        if args.cmd == "ingest":
            return cmd_ingest(args)
        if args.cmd == "eval":
            return cmd_eval(args)
    except RuntimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

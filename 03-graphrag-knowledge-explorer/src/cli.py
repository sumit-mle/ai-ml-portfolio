"""Production CLI for the Neo4j-backed GraphRAG Knowledge Explorer.

Subcommands:
    init    apply Neo4j schema (constraints + vector index). Idempotent.
    reset   wipe the database. Confirms unless --yes.
    stats   counts of nodes / edges by type.
    ingest  fetch real SEC filings for a CIK and load them into Neo4j.
    ask     run a single question through graph_rag or vector_rag.
    eval    build a dynamic golden set from the graph and compare both retrievers.
"""
from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # Keep noisy libs quiet
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)


def cmd_init(args: argparse.Namespace) -> int:
    from .db.schema import ensure_schema
    ensure_schema()
    print("Schema ready.")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        print(
            "This will DELETE every node and relationship in the configured Neo4j database.\n"
            "Re-run with --yes to confirm.",
            file=sys.stderr,
        )
        return 2
    from .db.schema import drop_all, ensure_schema
    drop_all()
    ensure_schema()
    print("Database wiped and schema re-applied.")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from .db.schema import stats
    s = stats()
    print("\nGraph stats:")
    for k, v in s.items():
        print(f"  {k:25s} {v:>8d}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from .db.schema import ensure_schema
    from .ingest.pipeline import ingest_company

    ensure_schema()  # safe + idempotent

    forms = tuple(f.strip() for f in args.forms.split(",") if f.strip())
    print(f"Ingesting CIK {args.cik}, forms={forms}, limit={args.limit} ...")
    results = ingest_company(
        args.cik,
        forms=forms,
        limit=args.limit,
        embed_chunks=not args.no_embeddings,
    )
    print(f"\nIngested {len(results)} filing(s):")
    for r in results:
        print(
            f"  {r['accession_no']} {r['form']:8s} "
            f"entities={r['n_entities']:>3d}  relations={r['n_relations']:>3d}  "
            f"chunks={r['n_chunks']:>3d}"
        )
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    if args.technique == "graph":
        from .retrieval.graph_rag import run as graph_run

        result = graph_run(args.question, k_chunks=args.k)
        print(f"\nTechnique: {result.technique}")
        print(f"Question : {args.question}\n")
        print(f"Seed filings : {result.seed_filings}")
        print(f"Triples ({len(result.triples)}):")
        for t in result.triples[:25]:
            print(f"  {t}")
        if len(result.triples) > 25:
            print(f"  ... and {len(result.triples) - 25} more")
        print(f"\nAnswer:\n{result.answer}")
    elif args.technique == "vector":
        from .retrieval.vector_rag import run as vector_run

        result = vector_run(args.question, top_k=args.k)
        print(f"\nTechnique: {result.technique}")
        print(f"Question : {args.question}\n")
        print(f"Retrieved filings: {result.retrieved_filings}")
        print(f"\nAnswer:\n{result.answer}")
    else:
        print(f"Unknown technique: {args.technique}", file=sys.stderr)
        return 2
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.runner import run_eval

    summary = run_eval(k_chunks=args.k_chunks, top_k=args.top_k)

    print("\n=== EVAL RESULTS ===\n")
    for label in ("graph_summary", "vector_summary"):
        s = summary[label]
        print(f"{s['technique']}:")
        print(f"  n_questions      : {s['n_questions']}")
        print(f"  must_mention_hit : {s['must_mention_hit']:.2f}")
        print(f"  filing_recall    : {s['filing_recall']:.2f}")
        print(f"  answered_rate    : {s['answered_rate']:.2f}")
        print()

    print("By pattern (must_mention_hit):")
    print(f"  {'pattern':<25s} {'n':>4s}  {'graph':>6s}  {'vector':>6s}")
    for pat, m in summary["by_pattern"].items():
        print(
            f"  {pat:<25s} {int(m['n']):>4d}  "
            f"{m['graph_must_mention']:>6.2f}  {m['vector_must_mention']:>6.2f}"
        )

    c = summary["comparison"]
    print(f"\nHEAD-TO-HEAD:")
    print(f"  graph wins : {c['graph_wins']}")
    print(f"  ties       : {c['ties']}")
    print(f"  vector wins: {c['vector_wins']}")
    print(f"  graph win rate: {c['graph_win_rate']:.0%}")
    print("\n  results written to: results/graphrag_vs_vector.json")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="graphrag-knowledge-explorer")
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="apply Neo4j schema (constraints + vector index)")

    rst = sub.add_parser("reset", help="wipe the database (use --yes to confirm)")
    rst.add_argument("--yes", action="store_true")

    sub.add_parser("stats", help="show node/edge counts")

    ing = sub.add_parser("ingest", help="fetch SEC filings for a CIK and load")
    ing.add_argument("--cik", required=True, help="SEC CIK (with or without leading zeros)")
    ing.add_argument("--forms", default="10-K,DEF 14A",
                     help="Comma-separated form types (default: '10-K,DEF 14A')")
    ing.add_argument("--limit", type=int, default=2,
                     help="Max filings per CIK (default: 2)")
    ing.add_argument("--no-embeddings", action="store_true",
                     help="Skip chunk embedding (saves $/time, breaks vector_rag)")

    ask = sub.add_parser("ask", help="ask a question")
    ask.add_argument("--question", required=True)
    ask.add_argument("--technique", choices=["graph", "vector"], default="graph")
    ask.add_argument("--k", type=int, default=5,
                     help="Vector top-k (chunks)")

    ev = sub.add_parser("eval", help="dynamic golden set + side-by-side comparison")
    ev.add_argument("--k_chunks", type=int, default=5)
    ev.add_argument("--top_k", type=int, default=5)

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    _setup_logging(args.verbose)

    try:
        if args.cmd == "init":   return cmd_init(args)
        if args.cmd == "reset":  return cmd_reset(args)
        if args.cmd == "stats":  return cmd_stats(args)
        if args.cmd == "ingest": return cmd_ingest(args)
        if args.cmd == "ask":    return cmd_ask(args)
        if args.cmd == "eval":   return cmd_eval(args)
    except RuntimeError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

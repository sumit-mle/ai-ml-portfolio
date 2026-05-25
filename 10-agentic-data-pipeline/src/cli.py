"""CLI for the marketing analytics SQL agent.

Subcommands:
    init-db     materialize the synthetic marketing warehouse
    schema      print the table catalog
    ask         run one natural-language question end-to-end
    eval        run the golden Q/A set against the agent
    show-trace  pretty-print a saved trace
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


def _setup_logging(verbose: bool) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "openai", "urllib3", "duckdb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_init_db(args: argparse.Namespace) -> int:
    from .db.bootstrap import init_db
    info = init_db(force=args.force)
    print("\nWarehouse ready:")
    for k, v in info.items():
        print(f"  {k:25s} : {v}")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    from .db.schema import render_full_schema
    print("\n" + render_full_schema())
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from .agent.graph import run_question
    from .eval.runner import save_trace
    final = run_question(args.question)
    print(f"\nQ: {args.question}")
    print(f"\nA: {final.get('answer', '(no answer)')}")
    print()
    print(f"  safe_sql:       {final.get('safe_sql', '(none)')[:250]}")
    print(f"  rows returned:  {final.get('n_rows', 0)}")
    print(f"  repair attempts: {final.get('repair_attempts', 0)}")
    if final.get("last_error"):
        print(f"  last_error:     {final.get('last_error')}")
    if args.show_trace:
        print("\nTrace:")
        for evt in final.get("trace", []) or []:
            print(f"  {evt}")
    if args.save_trace:
        out = save_trace(args.question, final)
        print(f"\nTrace saved to {out}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.runner import run_eval
    summary = run_eval(out_dir=args.out_dir)
    print("\n=== EVAL SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k:25s} : {v}")
    print(f"\n  results: {args.out_dir}/agent_eval.json")
    return 0


def cmd_show_trace(args: argparse.Namespace) -> int:
    p = Path(args.path)
    if not p.exists():
        print(f"Not found: {p}", file=sys.stderr)
        return 2
    payload = json.loads(p.read_text(encoding="utf-8"))
    print(f"\nQ: {payload.get('question')}")
    print(f"A: {payload.get('answer', '(no answer)')}")
    print(f"\nSafe SQL:\n{payload.get('safe_sql', '(none)')}")
    print(f"\nTrace ({len(payload.get('trace', []) or [])} events):")
    for evt in payload.get("trace", []) or []:
        node = evt.get("node", "?")
        rest = {k: v for k, v in evt.items() if k not in ("ts", "node")}
        print(f"  [{node:9s}] {rest}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="marketing-analytics-agent")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init-db", help="build the synthetic marketing warehouse")
    init.add_argument("--force", action="store_true")

    sub.add_parser("schema", help="show the table catalog")

    a = sub.add_parser("ask", help="ask one question")
    a.add_argument("--question", required=True)
    a.add_argument("--show-trace", action="store_true")
    a.add_argument("--save-trace", action="store_true")

    e = sub.add_parser("eval", help="run the golden Q/A set")
    e.add_argument("--out_dir", default="results")

    st = sub.add_parser("show-trace", help="pretty-print a saved trace")
    st.add_argument("path")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    _setup_logging(args.verbose)
    try:
        if args.cmd == "init-db":    return cmd_init_db(args)
        if args.cmd == "schema":     return cmd_schema(args)
        if args.cmd == "ask":        return cmd_ask(args)
        if args.cmd == "eval":       return cmd_eval(args)
        if args.cmd == "show-trace": return cmd_show_trace(args)
    except RuntimeError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

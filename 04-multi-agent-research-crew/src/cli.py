"""CLI for the multi-agent sales research crew.

Subcommands:
    research  generate one briefing (one company, one seller offering)
    eval      run the golden set, score with LLM-as-judge, write results
    show      print a saved briefing's markdown
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


def _setup_logging(verbose: bool) -> None:
    # Force UTF-8 for stdout on Windows so unicode in briefings doesn't crash.
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
    for noisy in ("httpx", "openai", "urllib3", "litellm", "LiteLLM"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_research(args: argparse.Namespace) -> int:
    from .crew import run_briefing, write_outputs
    from .models import ResearchRequest

    req = ResearchRequest(
        company_name=args.company,
        company_domain=args.domain,
        seller_offering=args.offering,
        meeting_context=args.context,
    )
    print(f"\nResearching {req.company_name} for: {req.seller_offering}\n")
    briefing = run_briefing(req)
    paths = write_outputs(briefing)
    print(f"\nWrote:\n  {paths['json']}\n  {paths['markdown']}")
    if briefing.critique:
        c = briefing.critique
        print(
            f"\nIn-crew QA: grounded={c.grounded:.2f} specific={c.specific:.2f} "
            f"actionable={c.actionable:.2f} pass={c.overall_pass}"
        )
    print("\n--- Markdown preview ---\n")
    print(briefing.to_markdown())
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.runner import run_eval
    summary = run_eval(out_dir=args.out_dir)
    print("\n=== EVAL SUMMARY ===\n")
    for k, v in summary.items():
        if isinstance(v, float):
            print(f"  {k:32s} : {v:.2f}")
        else:
            print(f"  {k:32s} : {v}")
    print(f"\n  results: {args.out_dir}/crew_eval.json")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    from .models import AccountBriefing
    p = Path(args.path)
    if not p.exists():
        print(f"Not found: {p}", file=sys.stderr)
        return 2
    data = json.loads(p.read_text(encoding="utf-8"))
    briefing = AccountBriefing.model_validate(data)
    print(briefing.to_markdown())
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sales-research-crew")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("research", help="generate one briefing")
    r.add_argument("--company", required=True, help="Target company legal name")
    r.add_argument("--domain", default=None)
    r.add_argument("--offering", required=True, help="What you sell")
    r.add_argument("--context", default=None, help="Meeting type / context")

    ev = sub.add_parser("eval", help="run the golden set + score with judge")
    ev.add_argument("--out_dir", default="results")

    sh = sub.add_parser("show", help="print a saved briefing")
    sh.add_argument("path", help="Path to briefing JSON")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    _setup_logging(args.verbose)
    try:
        if args.cmd == "research": return cmd_research(args)
        if args.cmd == "eval":     return cmd_eval(args)
        if args.cmd == "show":     return cmd_show(args)
    except RuntimeError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

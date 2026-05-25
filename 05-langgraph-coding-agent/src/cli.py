"""CLI for the legacy modernization agent.

Subcommands:
    scan       just list findings (no LLM, no writes)
    plan       scan + LLM-rate + show plans (no writes)
    modernize  full pipeline: scan -> plan -> gate -> apply -> test -> report
    show       print a saved audit report
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
    for noisy in ("httpx", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_scan(args: argparse.Namespace) -> int:
    from .scanner import scan_repo
    findings = scan_repo(Path(args.project))
    print(f"\nFound {len(findings)} candidate(s) in {args.project}\n")
    by_recipe: dict[str, int] = {}
    for f in findings:
        by_recipe[f.recipe] = by_recipe.get(f.recipe, 0) + 1
        print(f"  [{f.estimated_risk}] {f.recipe:30s} {f.file}:{f.line}")
    print("\nBy recipe:")
    for k, v in sorted(by_recipe.items()):
        print(f"  {k:30s} {v}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    from .scanner import scan_repo
    from .planner import plan_changes
    findings = scan_repo(Path(args.project))
    print(f"\nScanner: {len(findings)} candidate(s)")
    print("Planner: rating each ...")
    plans = plan_changes(findings)
    print(f"\n{'recipe':<30s} {'risk':<7s} {'auto':<5s} file:line")
    for p in plans:
        auto = "yes" if p.auto_approved else "no"
        print(
            f"{p.finding.recipe:<30s} {p.risk:<7s} {auto:<5s} "
            f"{p.finding.file}:{p.finding.line}"
        )
    return 0


def cmd_modernize(args: argparse.Namespace) -> int:
    from .agent.graph import run_modernize
    project = Path(args.project).resolve()
    workdir = Path(args.workdir).resolve() if args.workdir else project.parent / f"_workdir_{project.name}"
    final = run_modernize(
        project,
        workdir,
        interactive=args.interactive,
        auto_yes=args.auto_yes,
        rollback_enabled=not args.no_rollback,
    )
    print("\n=== AGENT RUN COMPLETE ===")
    before = final.get("test_run_before") or {}
    after = final.get("test_run_after") or {}
    print(f"  findings:           {len(final.get('findings', []))}")
    print(f"  plans:              {len(final.get('plans', []))}")
    print(f"  patches applied:    {sum(1 for p in final.get('patches', []) if p.get('applied'))}")
    print(f"  tests before:       passed={before.get('passed')} ({before.get('n_passed', 0)} pass, {before.get('n_failed', 0)} fail)")
    print(f"  tests after:        passed={after.get('passed')} ({after.get('n_passed', 0)} pass, {after.get('n_failed', 0)} fail)")
    print(f"  rollback triggered: {final.get('rollback_triggered', False)}")
    print(f"\n  workdir: {workdir}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    from .models import AuditReport
    p = Path(args.path)
    if not p.exists():
        print(f"Not found: {p}", file=sys.stderr)
        return 2
    rpt = AuditReport.model_validate(json.loads(p.read_text(encoding="utf-8")))
    print(rpt.to_markdown())
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.runner import run_eval
    summary = run_eval(out_dir=args.out_dir)
    print("\n=== EVAL SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k:25s} : {v}")
    print(f"\n  results: {args.out_dir}/modernize_eval.json")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="legacy-modernize-agent")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scan", help="list findings only")
    sc.add_argument("--project", required=True)

    pl = sub.add_parser("plan", help="scan + LLM-rate (no writes)")
    pl.add_argument("--project", required=True)

    md = sub.add_parser("modernize", help="full pipeline")
    md.add_argument("--project", required=True)
    md.add_argument("--workdir", default=None,
                    help="Where to write the modified copy (default: sibling _workdir_*)")
    md.add_argument("--interactive", action="store_true",
                    help="Prompt the human at the gate for non-auto plans")
    md.add_argument("--auto_yes", action="store_true",
                    help="Auto-approve everything in non-interactive mode (use only for demos)")
    md.add_argument("--no_rollback", action="store_true",
                    help="Do not rollback if tests fail (still produces audit)")

    sh = sub.add_parser("show", help="print a saved audit report")
    sh.add_argument("path", help="Path to audit JSON")

    ev = sub.add_parser("eval", help="run the golden eval scenarios")
    ev.add_argument("--out_dir", default="results")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    _setup_logging(args.verbose)
    try:
        if args.cmd == "scan":      return cmd_scan(args)
        if args.cmd == "plan":      return cmd_plan(args)
        if args.cmd == "modernize": return cmd_modernize(args)
        if args.cmd == "show":      return cmd_show(args)
        if args.cmd == "eval":      return cmd_eval(args)
    except RuntimeError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

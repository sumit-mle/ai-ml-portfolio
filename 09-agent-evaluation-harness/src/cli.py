"""CLI for the RAG regression harness.

Subcommands:
    list-suts      print known SUTs (system-under-test pipelines)
    run            evaluate one or more SUTs over the golden set
    save-baseline  promote the most recent run for a SUT to the baseline
    gate           re-run a SUT and compare to baseline; non-zero exit on regression
    drift          compare two runs of the same SUT (Kendall-tau)
    report         render an HTML report from one or more recent runs
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
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
    for noisy in ("httpx", "openai", "urllib3", "faiss"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_list_suts(args: argparse.Namespace) -> int:
    from .sut.interface import known_suts
    suts = known_suts()
    print("\nKnown SUTs:")
    for name in sorted(suts):
        print(f"  {name}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .runner import run_sut
    for name in args.sut:
        print(f"\n=== Running {name} ===")
        rec = run_sut(name, use_judge=not args.no_judge)
        s = rec["summary"]
        print("  ", " ".join(f"{k}={v:.2f}" for k, v in s.items() if isinstance(v, float)))
    return 0


def cmd_save_baseline(args: argparse.Namespace) -> int:
    from .config import get_settings
    from .runner import save_as_baseline
    s = get_settings()
    runs_dir = s.results_dir / "runs"
    safe = args.sut.replace(".", "_")
    matches = sorted(runs_dir.glob(f"{safe}__*.json"))
    if not matches:
        print(f"No runs found for {args.sut}", file=sys.stderr)
        return 2
    latest = matches[-1]
    rec = json.loads(latest.read_text(encoding="utf-8"))
    path = save_as_baseline(rec)
    print(f"Saved baseline -> {path} (from {latest.name})")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    from .runner import detect_regressions, load_baseline, run_sut
    base = load_baseline(args.sut)
    if base is None:
        print(f"No baseline for {args.sut}. Run `save-baseline` first.", file=sys.stderr)
        return 3

    print(f"Re-running {args.sut} for regression gate ...")
    cur = run_sut(args.sut, use_judge=not args.no_judge)

    issues = detect_regressions(cur, base)
    print("\n=== Gate result ===")
    print(f"  baseline: {base['ts']}")
    print(f"  current : {cur['ts']}")
    print()
    print(f"  {'metric':<22s} {'base':>6s} {'curr':>6s} {'delta':>7s}  status")
    for k in ("clause_match", "citation_correct", "context_recall",
              "answer_quotes_clause", "faithfulness", "answer_relevancy"):
        b = base["summary"].get(k); c = cur["summary"].get(k)
        if b is None or c is None:
            continue
        delta = c - b
        status = "REGRESS" if any(i["metric"] == k for i in issues) else "ok"
        print(f"  {k:<22s} {b:>6.2f} {c:>6.2f} {delta:>+7.2f}  {status}")
    if issues:
        print(f"\n{len(issues)} regression(s):")
        for i in issues:
            print(f"  - {i['metric']}: {i['baseline']:.2f} -> {i['current']:.2f} "
                  f"(tolerance {i['tolerance']})")
        return 1
    print("\n  PASS — no regressions beyond tolerance.")
    return 0


def cmd_drift(args: argparse.Namespace) -> int:
    from .drift import compute_drift
    a = json.loads(Path(args.run_a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.run_b).read_text(encoding="utf-8"))
    rep = compute_drift(a, b)
    print(f"\nDrift report: {a['sut']} {a['ts']} vs {b['ts']}")
    print(f"  n_questions:    {rep.n_questions}")
    print(f"  avg Kendall-tau: {rep.avg_tau:.2f}")
    print(f"  questions with drift (tau<0.7): {rep.n_with_drift}")
    if args.verbose:
        for q in rep.per_question:
            print(f"  {q['qid']}: tau={q['tau']:.2f} (shared {q['shared']})")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .config import get_settings
    from .report import write_report
    from .runner import detect_regressions, load_baseline

    s = get_settings()
    runs_dir = s.results_dir / "runs"
    runs: list[dict] = []
    for sut in args.sut:
        safe = sut.replace(".", "_")
        matches = sorted(runs_dir.glob(f"{safe}__*.json"))
        if not matches:
            print(f"No runs for {sut}", file=sys.stderr)
            continue
        rec = json.loads(matches[-1].read_text(encoding="utf-8"))
        base = load_baseline(sut)
        rec["regressions"] = detect_regressions(rec, base) if base else []
        runs.append(rec)
    if not runs:
        return 2
    out = s.reports_dir / "report.html"
    write_report(runs, out, generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    print(f"Wrote {out}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="rag-eval-harness")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-suts", help="list known SUTs")

    r = sub.add_parser("run", help="evaluate one or more SUTs")
    r.add_argument("--sut", nargs="+", required=True)
    r.add_argument("--no-judge", action="store_true",
                   help="skip the LLM-as-judge metrics (faster, cheaper)")

    sb = sub.add_parser("save-baseline", help="promote latest run to baseline")
    sb.add_argument("--sut", required=True)

    g = sub.add_parser("gate", help="re-run + compare to baseline (CI mode)")
    g.add_argument("--sut", required=True)
    g.add_argument("--no-judge", action="store_true")

    d = sub.add_parser("drift", help="compare retrieval order between two runs")
    d.add_argument("run_a")
    d.add_argument("run_b")

    rp = sub.add_parser("report", help="render an HTML report")
    rp.add_argument("--sut", nargs="+", required=True)

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    _setup_logging(args.verbose)
    try:
        if args.cmd == "list-suts":     return cmd_list_suts(args)
        if args.cmd == "run":           return cmd_run(args)
        if args.cmd == "save-baseline": return cmd_save_baseline(args)
        if args.cmd == "gate":          return cmd_gate(args)
        if args.cmd == "drift":         return cmd_drift(args)
        if args.cmd == "report":        return cmd_report(args)
    except RuntimeError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

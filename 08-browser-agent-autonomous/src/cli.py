"""CLI for the browser agent.

Subcommands:
    serve-portal    run the self-hosted vendor compliance portal
    list-vendors    print the demo vendor inventory
    pull-evidence   run one vendor end-to-end (deterministic by default,
                    --autonomous to use the browser-use agent)
    eval            run the deterministic eval over all 3 golden vendors
    show-audit      pretty-print recent audit entries
"""
from __future__ import annotations

import argparse
import asyncio
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
    for noisy in ("httpx", "openai", "urllib3", "uvicorn.access", "playwright",
                  "browser_use", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_serve_portal(args: argparse.Namespace) -> int:
    from .portal.app import run
    run()
    return 0


def cmd_list_vendors(args: argparse.Namespace) -> int:
    from .portal.data import VENDORS
    print("\nDemo vendors:\n")
    for v in VENDORS.values():
        mfa = "MFA" if v.requires_mfa else "no MFA"
        n_docs = len(v.documents)
        print(f"  {v.vendor_id:25s}  {v.name:35s}  {mfa:7s}  {n_docs} docs")
    return 0


def cmd_pull_evidence(args: argparse.Namespace) -> int:
    if args.autonomous:
        from .agents.autonomous import pull_vendor_autonomous_sync
        bundle = pull_vendor_autonomous_sync(args.vendor, operator=args.operator)
    else:
        from .extractors.deterministic import pull_vendor_sync
        bundle = pull_vendor_sync(args.vendor, operator=args.operator)
    print(f"\n=== {bundle.vendor_name} ({bundle.vendor_id}) ===")
    print(f"  status:    {bundle.overall_status}")
    print(f"  found:     {bundle.n_found}/{bundle.n_required}")
    print(f"  missing:   {bundle.n_missing}, expired: {bundle.n_expired}")
    print(f"  run_id:    {bundle.run_id}")
    print(f"  evidence:")
    for item in bundle.items:
        print(f"    [{item.status:7s}] {item.kind:30s}  {item.file_path or '(no file)'}")

    # Persist the bundle
    from .config import get_settings
    s = get_settings()
    out_path = s.evidence_dir / bundle.run_id / bundle.vendor_id / "bundle.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(bundle.model_dump_json(indent=2))
    print(f"\n  bundle:   {out_path}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.runner import run_eval
    summary = run_eval(out_dir=args.out_dir)
    print("\n=== EVAL SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k:20s} : {v}")
    print(f"\n  results: {args.out_dir}/browser_eval.json")
    return 0


def cmd_show_audit(args: argparse.Namespace) -> int:
    from .config import get_settings
    s = get_settings()
    if not s.audit_log_path.exists():
        print(f"No audit log at {s.audit_log_path}")
        return 0
    lines = s.audit_log_path.read_text(encoding="utf-8").splitlines()
    tail = lines[-args.n :]
    print(f"\nLast {len(tail)} audit entries from {s.audit_log_path}:\n")
    for ln in tail:
        try:
            obj = json.loads(ln)
        except Exception:
            print(ln)
            continue
        url_short = (obj.get("url") or "").replace("http://127.0.0.1:7878", "")
        print(
            f"  {obj.get('ts')}  {obj.get('outcome', '?').upper():<5s}  "
            f"{obj.get('action'):<18s}  {obj.get('vendor_id', '-'):<22s}  "
            f"{url_short[:50]}"
        )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="vendor-compliance-agent")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve-portal", help="run the self-hosted vendor portal")
    sub.add_parser("list-vendors", help="list demo vendors")

    pe = sub.add_parser("pull-evidence", help="pull evidence for one vendor")
    pe.add_argument("--vendor", required=True, help="vendor_id")
    pe.add_argument("--operator", default="cli")
    pe.add_argument(
        "--autonomous", action="store_true",
        help="use the browser-use LLM agent path instead of the deterministic one",
    )

    ev = sub.add_parser("eval", help="run the deterministic eval over all golden vendors")
    ev.add_argument("--out_dir", default="results")

    au = sub.add_parser("show-audit", help="pretty-print recent audit entries")
    au.add_argument("--n", type=int, default=30)

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    _setup_logging(args.verbose)
    try:
        if args.cmd == "serve-portal":   return cmd_serve_portal(args)
        if args.cmd == "list-vendors":   return cmd_list_vendors(args)
        if args.cmd == "pull-evidence":  return cmd_pull_evidence(args)
        if args.cmd == "eval":           return cmd_eval(args)
        if args.cmd == "show-audit":     return cmd_show_audit(args)
    except RuntimeError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

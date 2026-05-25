"""CLI for the voice helpdesk.

Subcommands:
    init-db     create the demo SQLite DB with 50 synthetic users
    serve       run the FastAPI + Realtime bridge server
    eval        replay the golden scenarios in text mode (cheap, deterministic)
    show-audit  pretty-print recent audit entries
    show-user   print one user's record (debug helper)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

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
    for noisy in ("httpx", "openai", "urllib3", "websockets", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_init_db(args: argparse.Namespace) -> int:
    from .db.bootstrap import init_db
    info = init_db(force=args.force)
    print(f"\nHelpdesk DB ready: {info}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server.app import run
    run()
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.runner import run_eval
    summary = run_eval(out_dir=args.out_dir)
    print("\n=== EVAL SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k:25s} : {v}")
    print(f"\n  results: {args.out_dir}/voice_eval.json")
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
        verified = "V" if obj.get("identity_verified") else "-"
        print(
            f"  {obj.get('ts')}  [{verified}] {obj.get('outcome', '?').upper():<8s} "
            f"{obj.get('tool'):<26s} by {obj.get('principal_id') or '<none>'}"
            + (f"  err={obj.get('error')}" if obj.get("error") else "")
        )
    return 0


def cmd_show_user(args: argparse.Namespace) -> int:
    from .db import store
    u = store.find_user(employee_id=args.employee_id)
    if not u:
        print(f"Not found: {args.employee_id}")
        return 1
    print(json.dumps(u, indent=2))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="voice-helpdesk")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    init_db = sub.add_parser("init-db", help="create the demo SQLite DB")
    init_db.add_argument("--force", action="store_true")

    sub.add_parser("serve", help="run the FastAPI + Realtime bridge server")

    ev = sub.add_parser("eval", help="run the golden scenarios in text mode")
    ev.add_argument("--out_dir", default="results")

    au = sub.add_parser("show-audit", help="pretty-print recent audit entries")
    au.add_argument("--n", type=int, default=20)

    su = sub.add_parser("show-user", help="print one user (debug)")
    su.add_argument("employee_id")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    _setup_logging(args.verbose)
    try:
        if args.cmd == "init-db":     return cmd_init_db(args)
        if args.cmd == "serve":       return cmd_serve(args)
        if args.cmd == "eval":        return cmd_eval(args)
        if args.cmd == "show-audit":  return cmd_show_audit(args)
        if args.cmd == "show-user":   return cmd_show_user(args)
    except RuntimeError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

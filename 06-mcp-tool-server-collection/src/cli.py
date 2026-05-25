"""CLI for the enterprise data platform MCP server.

Subcommands:
    init-db     materialize the synthetic warehouse (taxi_trips, passengers)
    init-tokens generate a fresh tokens.json with three demo principals
    serve       run the FastMCP server (stdio or http per .env TRANSPORT)
    eval        run the golden in-process eval over all 5 tools
    smoke-http  quick check against a running HTTP server
    show-audit  pretty-print the last N audit entries
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
    for noisy in ("httpx", "openai", "urllib3", "duckdb", "fastmcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_init_db(args: argparse.Namespace) -> int:
    from .db.bootstrap import init_warehouse
    info = init_warehouse(force=args.force)
    print(f"\nWarehouse ready: {info}")
    return 0


def cmd_init_tokens(args: argparse.Namespace) -> int:
    from .config import get_settings
    from .security.auth import generate_token

    s = get_settings()
    s.auth_token_file.parent.mkdir(parents=True, exist_ok=True)

    if s.auth_token_file.exists() and not args.force:
        print(
            f"Token file already exists at {s.auth_token_file}. "
            "Use --force to overwrite.",
            file=sys.stderr,
        )
        return 2

    tokens = {
        "tokens": [
            {
                "id": "tok_analyst_public",
                "name": "Public-data Analyst",
                "secret": generate_token(),
                "scopes": ["read:public"],
            },
            {
                "id": "tok_analyst_pii",
                "name": "PII-cleared Analyst",
                "secret": generate_token(),
                "scopes": ["read:public", "read:pii"],
            },
            {
                "id": "tok_data_scientist",
                "name": "Data Scientist (NL queries)",
                "secret": generate_token(),
                "scopes": ["read:public", "query:nl"],
            },
        ]
    }
    s.auth_token_file.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    print(f"\nWrote {s.auth_token_file} with 3 demo principals.")
    print("Token IDs and scopes:")
    for t in tokens["tokens"]:
        print(f"  {t['id']:24s} scopes={t['scopes']}")
    print("\nKeep this file secret — it contains live bearer tokens.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .config import get_settings
    from .server.app import run_http, run_stdio

    s = get_settings()
    transport = args.transport or s.transport
    print(f"\nStarting {s.server_name} v{s.server_version} on {transport} ...")
    if transport == "http":
        print(f"  HTTP: http://{s.http_host}:{s.http_port}")
        run_http()
    else:
        run_stdio()
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.runner import run_eval
    summary = run_eval(out_dir=args.out_dir)
    print("\n=== EVAL SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k:25s} : {v}")
    print(f"\n  results: {args.out_dir}/mcp_eval.json")
    return 0


def cmd_smoke_http(args: argparse.Namespace) -> int:
    """Hit the running HTTP MCP server with a real MCP client to exercise the
    streamable-http transport. Verifies tools list and one list_tables call.
    """
    from fastmcp import Client

    async def _go() -> int:
        from .config import get_settings
        s = get_settings()
        url = args.url or f"http://{s.http_host}:{s.http_port}/mcp"
        # FastMCP's Client auto-detects HTTP vs stdio from the URL/spec
        async with Client(url) as client:
            tools = await client.list_tools()
            print(f"\nTools advertised by {url}:")
            for t in tools:
                print(f"  - {t.name}: {t.description[:80] if t.description else ''}")
            if not args.bearer:
                print("\n(no --bearer; skipping live tool invocation)")
                return 0
            result = await client.call_tool(
                "list_tables", {"bearer": args.bearer},
            )
            print("\nlist_tables result:")
            print(result.data if hasattr(result, "data") else result)
            return 0

    try:
        return asyncio.run(_go())
    except Exception as e:
        print(f"smoke failed: {e}", file=sys.stderr)
        return 4


def cmd_show_audit(args: argparse.Namespace) -> int:
    from .config import get_settings
    s = get_settings()
    if not s.audit_log_path.exists():
        print(f"No audit log at {s.audit_log_path}")
        return 0
    lines = s.audit_log_path.read_text(encoding="utf-8").splitlines()
    tail = lines[-args.n:]
    print(f"\nLast {len(tail)} audit entries from {s.audit_log_path}:\n")
    for ln in tail:
        try:
            obj = json.loads(ln)
        except Exception:
            print(ln)
            continue
        print(
            f"  {obj.get('ts')}  {obj.get('outcome', '?').upper():<8s}  "
            f"{obj.get('tool'):<30s}  by {obj.get('principal_id') or '<none>'}"
            + (f"  err={obj.get('error')}" if obj.get('error') else "")
        )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="enterprise-data-platform-mcp")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    init_db = sub.add_parser("init-db", help="materialize the synthetic warehouse")
    init_db.add_argument("--force", action="store_true")

    init_tok = sub.add_parser("init-tokens", help="generate demo bearer tokens")
    init_tok.add_argument("--force", action="store_true")

    sv = sub.add_parser("serve", help="run the MCP server")
    sv.add_argument("--transport", choices=["stdio", "http"], default=None)

    ev = sub.add_parser("eval", help="run the golden eval (in-process)")
    ev.add_argument("--out_dir", default="results")

    sm = sub.add_parser("smoke-http", help="MCP client smoke check against a live HTTP server")
    sm.add_argument("--url", default=None)
    sm.add_argument("--bearer", default=None)

    au = sub.add_parser("show-audit", help="pretty-print recent audit entries")
    au.add_argument("--n", type=int, default=20)

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    _setup_logging(args.verbose)
    try:
        if args.cmd == "init-db":     return cmd_init_db(args)
        if args.cmd == "init-tokens": return cmd_init_tokens(args)
        if args.cmd == "serve":       return cmd_serve(args)
        if args.cmd == "eval":        return cmd_eval(args)
        if args.cmd == "smoke-http":  return cmd_smoke_http(args)
        if args.cmd == "show-audit":  return cmd_show_audit(args)
    except RuntimeError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

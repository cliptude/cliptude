"""Tiny CLI wrapping /api/v1: auth | templates | generate | status | jobs | account | cancel."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cliptude_mcp.client import CliptudeApiError, CliptudeClient


def _print(payload) -> None:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _die(exc: Exception, code: int = 1) -> None:
    if isinstance(exc, CliptudeApiError):
        _print({"error": str(exc), "status": exc.status, "detail": exc.payload})
        raise SystemExit(exc.status or code)
    _print({"error": str(exc)})
    raise SystemExit(code)


def _resolve_template_id(client: CliptudeClient, value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise CliptudeApiError("Template name or id is required.")
    listing = client.list_templates(limit=100)
    results = listing.get("results") or []
    for row in results:
        if str(row.get("id")) == value or str(row.get("name", "")).lower() == value.lower():
            return str(row["id"])
    raise CliptudeApiError(f"Template not found: {value}")


def _run_auth(auth_cmd: str) -> None:
    from cliptude_mcp.auth_login import AuthLoginError, login
    from cliptude_mcp.credentials import clear_credentials, stored_access_token

    if auth_cmd == "login":
        try:
            login()
        except AuthLoginError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        print("Logged in.")
        return
    if auth_cmd == "logout":
        clear_credentials()
        print("Logged out.")
        return
    if not stored_access_token():
        print("Not authenticated. Run: cliptude auth login", file=sys.stderr)
        raise SystemExit(1)
    try:
        account = CliptudeClient().get_account()
    except Exception as exc:
        _die(exc)
    email = account.get("email") if isinstance(account, dict) else None
    print(f"Logged in{f' as {email}' if email else '.'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cliptude", description="Cliptude jobs CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    auth = sub.add_parser("auth", help="Sign in with a browser")
    auth_sub = auth.add_subparsers(dest="auth_cmd", required=True)
    auth_sub.add_parser("login", help="Open a browser and save a local session")
    auth_sub.add_parser("logout", help="Delete the local session")
    auth_sub.add_parser("status", help="Show whether this machine is signed in")

    t = sub.add_parser("templates", help="List saved templates")
    t.add_argument("--limit", type=int, default=20)
    t.add_argument("--offset", type=int, default=0)

    g = sub.add_parser("generate", help="Start a job")
    g.add_argument("--template", required=True, help="Template name or UUID")
    g.add_argument("--title", required=True)
    g.add_argument("--prompt", default="")
    g.add_argument("--script", default="")
    g.add_argument("--script-file", default="")
    g.add_argument(
        "--faceless-coding",
        default="",
        help="external = you author Faceless storyboard/HTML; omit for Cliptude coding",
    )

    k = sub.add_parser("kit", help="Download Faceless coding kit zip")
    k.add_argument("job_id")
    k.add_argument("--out", default="kit.zip")

    sp = sub.add_parser("submit-project", help="Upload Faceless project zip")
    sp.add_argument("job_id")
    sp.add_argument("zip_path")

    s = sub.add_parser("status", help="Get job status")
    s.add_argument("job_id")

    j = sub.add_parser("jobs", help="List recent jobs")
    j.add_argument("--status", default="")
    j.add_argument("--limit", type=int, default=20)

    sub.add_parser("account", help="Plan and credits")

    c = sub.add_parser("cancel", help="Cancel a job")
    c.add_argument("job_id")

    args = parser.parse_args(argv)
    if args.cmd == "auth":
        _run_auth(args.auth_cmd)
        return
    client = CliptudeClient()
    try:
        if args.cmd == "templates":
            _print(client.list_templates(limit=args.limit, offset=args.offset))
        elif args.cmd == "generate":
            script = args.script
            if args.script_file:
                script = Path(args.script_file).read_text(encoding="utf-8")
            if not (args.prompt or script):
                raise CliptudeApiError("Provide --prompt or --script / --script-file.")
            template_id = _resolve_template_id(client, args.template)
            _print(client.create_job(
                template_id=template_id,
                title=args.title,
                prompt=args.prompt or None,
                script=script or None,
                faceless_coding=(args.faceless_coding or None),
            ))
        elif args.cmd == "kit":
            dest = Path(args.out)
            client.download_coding_kit_file(args.job_id, dest)
            _print({"ok": True, "path": str(dest.resolve())})
        elif args.cmd == "submit-project":
            _print(client.submit_faceless_project(args.job_id, args.zip_path))
        elif args.cmd == "status":
            _print(client.get_job(args.job_id))
        elif args.cmd == "jobs":
            _print(client.list_jobs(status=args.status or None, limit=args.limit))
        elif args.cmd == "account":
            _print(client.get_account())
        elif args.cmd == "cancel":
            _print(client.cancel_job(args.job_id))
    except Exception as exc:
        _die(exc)


if __name__ == "__main__":
    main()

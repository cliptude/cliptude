"""Tiny CLI wrapping /api/v1: auth | templates | template-create | template-options | voices | avatars | estimate | generate | editor | agent | render | script | voiceover | continue | status | jobs | account | cancel."""

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


def _parse_set_args(pairs: list[str]) -> dict:
    out = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key.strip():
            raise CliptudeApiError(f"--set expects KEY=VALUE, got {pair!r}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        out[key.strip()] = value
    return out


_DONE_STATUSES = {"completed", "failed", "cancelled", "conflict"}


def _wait(fetch, status_of, *, interval: float = 10.0, timeout: float = 3600.0):
    import time

    deadline = time.monotonic() + timeout
    while True:
        payload = fetch()
        status = str(status_of(payload) or "").lower()
        if status in _DONE_STATUSES or time.monotonic() > deadline:
            return payload
        print(f"{status or 'pending'}...", file=sys.stderr)
        time.sleep(interval)


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
    parser = argparse.ArgumentParser(
        prog="cliptude",
        description="Cliptude jobs CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude generate --template "Finance vector weekly" --title "Why index funds win" '
            '--prompt "why most retail traders underperform the S&P over 10 years, keep it for beginners" '
            '--duration 3-4'
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    auth = sub.add_parser("auth", help="Sign in with a browser")
    auth_sub = auth.add_subparsers(dest="auth_cmd", required=True)
    auth_sub.add_parser("login", help="Open a browser and save a local session")
    auth_sub.add_parser("logout", help="Delete the local session")
    auth_sub.add_parser("status", help="Show whether this machine is signed in")

    t = sub.add_parser("templates", help="List saved templates")
    t.add_argument("--limit", type=int, default=20)
    t.add_argument("--offset", type=int, default=0)

    tc = sub.add_parser(
        "template-create",
        help="Create a template from settings (no wizard)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude template-create --name "TSMC doc" --format faceless_documentary '
            "--set voice_id=JBFqnCBsd6RMkjVDRZzb --set voice_name=George --set generation_model=pro "
            "--set faceless_documentary_style=investigative --set faceless_documentary_primary=#E11D48\n"
            '  cliptude template-create --name "TSMC doc" --format faceless_documentary --settings-file settings.json\n'
            "See cliptude template-options --format <format> for every field and its choices."
        ),
    )
    tc.add_argument("--name", required=True)
    tc.add_argument("--format", required=True, help="e.g. faceless_documentary, vector_explainer")
    tc.add_argument("--settings-file", default="", help="JSON object of settings")
    tc.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="One setting; VALUE is parsed as JSON when possible (true, 0.4, [\"a\"]), else a string. Repeatable.",
    )

    to = sub.add_parser("template-options", help="Fields, choices and defaults for template-create")
    to.add_argument("--format", required=True)

    vc = sub.add_parser("voices", help="List voices for template-create (voice_id)")
    vc.add_argument("--provider", default="elevenlabs", help="elevenlabs, minimax, cartesia, inworld, voicetory")
    vc.add_argument("--language", default="")

    av = sub.add_parser("avatars", help="List avatars for Avatar Video (id for --avatar / avatar_profile_id)")
    av.add_argument("--section", default="", choices=["", "mine", "builtin", "public", "favorites"])
    av.add_argument("--search", default="", help="Part of the avatar's name")
    av.add_argument("--gender", default="", choices=["", "male", "female"])
    av.add_argument("--limit", type=int, default=50)

    est = sub.add_parser(
        "estimate",
        help="Credits a job would need (creates nothing)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude estimate --template "Finance vector weekly" --script-file script.txt\n'
            '  cliptude estimate --template "Finance vector weekly" --voiceover-seconds 3600'
        ),
    )
    est.add_argument("--template", required=True, help="Template name or UUID")
    est.add_argument("--prompt", default="")
    est.add_argument("--script", default="")
    est.add_argument("--script-file", default="")
    est.add_argument("--duration", default="", help="Duration bucket, e.g. 6-8. Default: template (or from --script)")
    est.add_argument("--voiceover-seconds", type=float, default=None, help="Length of the voiceover you would upload")
    est.add_argument("--talking-head-seconds", type=float, default=None, help="Length of the talking-head video you would upload")
    est.add_argument("--output", default="", choices=["", "video", "plan", "script", "voiceover"])
    est.add_argument("--avatar-intro-only", action=argparse.BooleanOptionalAction, default=None)
    est.add_argument("--avatar-ai-footage", action=argparse.BooleanOptionalAction, default=None)
    est.add_argument("--avatar", type=int, default=None, help="Avatar Video: avatar id from `cliptude avatars`. Default: the template's avatar")
    est.add_argument("--avatar-engine", default="", choices=["", "iii", "iv", "v"], help="Avatar Video: HeyGen engine. Default: the template's")

    g = sub.add_parser(
        "generate",
        help="Start a job",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude generate --template "Finance vector weekly" --title "Why index funds win" '
            '--prompt "why most retail traders underperform the S&P over 10 years, keep it for beginners" '
            '--duration 3-4\n'
            '  cliptude generate --template "Finance vector weekly" --title "Why index funds win" '
            '--script-file script.txt\n'
            '  cliptude generate --template "Finance vector weekly" --title "Why index funds win" '
            '--voiceover narration.mp3\n'
            '  cliptude generate --template "My talking head" --title "Weekly recap" '
            '--talking-head clip.mp4'
        ),
    )
    g.add_argument("--template", required=True, help="Template name or UUID")
    g.add_argument("--title", required=True)
    g.add_argument(
        "--prompt",
        default="",
        help='Topic or brief, e.g. "why most retail traders underperform the S&P over 10 years, keep it for beginners"',
    )
    g.add_argument("--script", default="")
    g.add_argument("--script-file", default="")
    g.add_argument(
        "--voiceover",
        default="",
        help="Path to a narration audio file (MP3, M4A, WAV, …). Transcribed; skips TTS. Do not combine with --prompt or --script.",
    )
    g.add_argument(
        "--talking-head",
        default="",
        help="Path to a talking-head presenter video (MP4, MOV, WEBM, AVI). Requires a Talking Head template. Transcribed; skips TTS. Do not combine with --prompt, --script, or --voiceover.",
    )
    g.add_argument(
        "--duration",
        default="",
        help="Wizard duration bucket, e.g. 3-4 or 6-8. Default: template duration (or estimated from --script)",
    )
    g.add_argument(
        "--avatar-intro-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Avatar Video: Avatar Intro Only (HeyGen in a short opening). "
            "Omit to use the template. Only valid for Avatar Video templates."
        ),
    )
    g.add_argument(
        "--avatar-ai-footage",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Avatar Video: AI Image + AI Video instead of stock and web footage. "
            "Omit to use the template. Only valid for Avatar Video templates."
        ),
    )
    g.add_argument("--avatar", type=int, default=None, help="Avatar Video: avatar id from `cliptude avatars`. Default: the template's avatar")
    g.add_argument("--avatar-engine", default="", choices=["", "iii", "iv", "v"], help="Avatar Video: HeyGen engine. Default: the template's")
    g.add_argument(
        "--disable-animations",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="No animated text reveals or motion graphics. Omit to use the template.",
    )
    g.add_argument(
        "--disable-overlays",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="No text overlays or graphic elements over footage (captions unaffected). Omit to use the template.",
    )
    g.add_argument(
        "--disable-effects",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="No color grading, film grain, or light leaks. Ken Burns is unaffected. Omit to use the template.",
    )
    g.add_argument(
        "--disable-transitions",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="No scene transitions; hard cuts only. Omit to use the template.",
    )
    g.add_argument(
        "--exclude-animation-type",
        action="append",
        dest="excluded_animation_types",
        default=None,
        metavar="TYPE",
        help="Exclude an animation type from this video. Repeatable. Omit to use the template.",
    )
    g.add_argument(
        "--exclude-transition",
        action="append",
        dest="excluded_transition_kinds",
        default=None,
        metavar="SLUG",
        help="Exclude a transition (e.g. flip-horizontal) from auto-selection. Repeatable. Omit to use the template.",
    )

    sc = sub.add_parser(
        "script",
        help="Generate a spoken script only (no video)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude script --template "Finance vector weekly" --title "Why index funds win" '
            '--prompt "why most retail traders underperform the S&P" --duration 3-4'
        ),
    )
    sc.add_argument("--template", required=True, help="Template name or UUID")
    sc.add_argument("--title", required=True)
    sc.add_argument("--prompt", required=True, help="Topic or brief. Cliptude writes the narration.")
    sc.add_argument("--duration", default="", help="Wizard duration bucket, e.g. 3-4 or 6-8")

    vo = sub.add_parser(
        "voiceover",
        help="Generate TTS audio only (no video)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude voiceover --template "Finance vector weekly" --title "Why index funds win" '
            '--script-file script.txt'
        ),
    )
    vo.add_argument("--template", required=True, help="Template name or UUID")
    vo.add_argument("--title", required=True)
    vo.add_argument("--prompt", default="", help="Topic or brief. Cliptude writes the narration then voices it.")
    vo.add_argument("--script", default="")
    vo.add_argument("--script-file", default="")
    vo.add_argument("--duration", default="", help="Wizard duration bucket, e.g. 3-4 or 6-8")

    dv = sub.add_parser("download-voiceover", help="Download voiceover MP3 for a completed voiceover job")
    dv.add_argument("job_id")
    dv.add_argument("--out", default="voiceover.mp3")

    cont = sub.add_parser(
        "continue",
        help="Turn a completed script/voiceover job, or a plan job with a locked plan, into a full video",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            "  cliptude continue <job-id>"
        ),
    )
    cont.add_argument("job_id")

    pj = sub.add_parser(
        "plan-job",
        help="Start an agent-planned job: Cliptude voices the narration and stops at plan_ready",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude plan-job --template "TSMC doc" --title "The island" --script-file script.txt\n'
            "  cliptude narration <job-id> > narration.json\n"
            "  cliptude catalog --format faceless_documentary --compact\n"
            "  cliptude plan-check <job-id> plan.json   (repeat until valid)\n"
            "  cliptude plan-lock <job-id> plan.json && cliptude continue <job-id>\n"
            "  cliptude plan-report <job-id>"
        ),
    )
    pj.add_argument("--template", required=True, help="Template name or UUID (Faceless Documentary, Top 10, Avatar Video)")
    pj.add_argument("--title", required=True)
    pj.add_argument("--prompt", default="", help="Topic or brief. Cliptude writes the narration.")
    pj.add_argument("--script", default="")
    pj.add_argument("--script-file", default="")
    pj.add_argument("--voiceover", default="", help="Local audio to use as the narration (aligned, not re-voiced)")
    pj.add_argument("--duration", default="", help="Wizard duration bucket, e.g. 6-8")
    pj.add_argument("--avatar", type=int, default=None, help="Avatar Video: avatar id from `cliptude avatars`. Default: the template's avatar")
    pj.add_argument("--avatar-engine", default="", choices=["", "iii", "iv", "v"], help="Avatar Video: HeyGen engine. Default: the template's")

    nr = sub.add_parser("narration", help="Numbered sentences and word timings of a plan_ready job")
    nr.add_argument("job_id")

    cat = sub.add_parser("catalog", help="Graphics an agent can place (kinds, types, overlay styles)")
    cat.add_argument("--format", default="", help="faceless_documentary, faceless_top_10 or avatar_video")
    cat.add_argument("--theme", default="")
    cat.add_argument("--compact", action="store_true", help="One summary line per graphic")
    cat.add_argument("--types", default="", help="Comma-separated types/styles for full entries")
    cat.add_argument("--premium", action="store_true", help="Only the premium boards")

    for name, text in (("plan-check", "Validate a plan file (nothing stored)"), ("plan-lock", "Validate and lock a plan file")):
        pc = sub.add_parser(name, help=text)
        pc.add_argument("job_id")
        pc.add_argument("plan_file", help="JSON file with the cliptude.plan/v1 plan")

    pg = sub.add_parser("plan-get", help="Show the locked plan")
    pg.add_argument("job_id")
    pv = sub.add_parser("plan-review", help="The built design beat by beat and graphic by graphic (review before rendering)")
    pv.add_argument("job_id")
    pr = sub.add_parser("plan-report", help="What sourcing used for each media request of the locked plan")
    pr.add_argument("job_id")

    ed = sub.add_parser("editor", help="Open the timeline editor project for a completed job (prints the timeline)")
    ed.add_argument("job_id")

    ag = sub.add_parser(
        "agent",
        help="Ask Cliptude Agent to edit a project's timeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude agent 123 --prompt "Replace this shot with real footage of a TSMC fab" --items clip-1 --wait\n'
            "Item ids come from cliptude editor <job-id> (timeline[].id)."
        ),
    )
    ag.add_argument("project_id")
    ag.add_argument("--prompt", default="", help="Edit request in plain English")
    ag.add_argument("--prompt-file", default="")
    ag.add_argument("--items", default="", help="Comma-separated timeline item ids the request is about")
    ag.add_argument("--tier", default="fast", help="fast or smart (see cliptude agent-tiers)")
    ag.add_argument("--chat", default="", help="Continue an existing agent chat id")
    ag.add_argument("--wait", action="store_true", help="Poll until the run finishes")

    ags = sub.add_parser("agent-status", help="Get a Cliptude Agent run")
    ags.add_argument("project_id")
    ags.add_argument("run_id")

    agc = sub.add_parser("agent-cancel", help="Cancel a Cliptude Agent run")
    agc.add_argument("project_id")
    agc.add_argument("run_id")

    sub.add_parser("agent-tiers", help="List Cliptude Agent tiers")

    rn = sub.add_parser("render", help="Render an edited project to a new MP4")
    rn.add_argument("project_id")
    rn.add_argument("--wait", action="store_true", help="Poll until the render finishes")

    rs = sub.add_parser("render-status", help="Get an editor render (output_url when done)")
    rs.add_argument("render_id")

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
        elif args.cmd == "template-create":
            settings = {}
            if args.settings_file:
                settings = json.loads(Path(args.settings_file).read_text(encoding="utf-8"))
                if not isinstance(settings, dict):
                    raise CliptudeApiError("--settings-file must contain a JSON object.")
            settings.update(_parse_set_args(args.set))
            _print(client.create_template(name=args.name, format=args.format, settings=settings))
        elif args.cmd == "template-options":
            _print(client.template_options(args.format))
        elif args.cmd == "voices":
            _print(client.list_voices(provider=args.provider, language=args.language or None))
        elif args.cmd == "avatars":
            _print(client.list_avatars(
                section=args.section or None, search=args.search or None,
                gender=args.gender or None, limit=args.limit,
            ))
        elif args.cmd == "estimate":
            script = args.script
            if args.script_file:
                script = Path(args.script_file).read_text(encoding="utf-8")
            _print(client.estimate_credits(
                template_id=_resolve_template_id(client, args.template),
                prompt=args.prompt or None,
                script=script or None,
                duration=args.duration or None,
                voiceover_seconds=args.voiceover_seconds,
                talking_head_seconds=args.talking_head_seconds,
                output=args.output or None,
                avatar_intro_only=args.avatar_intro_only,
                avatar_ai_footage=args.avatar_ai_footage,
                avatar_profile_id=args.avatar,
                avatar_engine=args.avatar_engine or None,
            ))
        elif args.cmd == "generate":
            script = args.script
            if args.script_file:
                script = Path(args.script_file).read_text(encoding="utf-8")
            voiceover = (args.voiceover or "").strip()
            talking_head = (args.talking_head or "").strip()
            if talking_head and voiceover:
                raise CliptudeApiError("Use --talking-head or --voiceover, not both.")
            if talking_head and (args.prompt or script):
                raise CliptudeApiError(
                    "Use --talking-head or --prompt/--script, not both. "
                    "An uploaded talking head is transcribed and used as the narration."
                )
            if voiceover and (args.prompt or script):
                raise CliptudeApiError(
                    "Use --voiceover or --prompt/--script, not both. "
                    "An uploaded file is transcribed and used as the narration."
                )
            if not talking_head and not voiceover and not (args.prompt or script):
                raise CliptudeApiError(
                    "Provide --prompt, --script / --script-file, --voiceover, or --talking-head."
                )
            template_id = _resolve_template_id(client, args.template)
            _print(client.create_job(
                template_id=template_id,
                title=args.title,
                prompt=args.prompt or None,
                script=script or None,
                duration=(args.duration or None),
                voiceover_path=voiceover or None,
                talking_head_path=talking_head or None,
                avatar_intro_only=args.avatar_intro_only,
                avatar_ai_footage=args.avatar_ai_footage,
                avatar_profile_id=args.avatar,
                avatar_engine=args.avatar_engine or None,
                disable_animations=args.disable_animations,
                disable_overlays=args.disable_overlays,
                disable_effects=args.disable_effects,
                disable_transitions=args.disable_transitions,
                excluded_animation_types=args.excluded_animation_types,
                excluded_transition_kinds=args.excluded_transition_kinds,
            ))
        elif args.cmd == "script":
            if not (args.prompt or "").strip():
                raise CliptudeApiError("Provide --prompt.")
            template_id = _resolve_template_id(client, args.template)
            _print(client.create_job(
                template_id=template_id,
                title=args.title,
                prompt=args.prompt,
                duration=(args.duration or None),
                output="script",
            ))
        elif args.cmd == "voiceover":
            script = args.script
            if args.script_file:
                script = Path(args.script_file).read_text(encoding="utf-8")
            if not (args.prompt or "").strip() and not (script or "").strip():
                raise CliptudeApiError("Provide --prompt or --script / --script-file.")
            template_id = _resolve_template_id(client, args.template)
            _print(client.create_job(
                template_id=template_id,
                title=args.title,
                prompt=args.prompt or None,
                script=script or None,
                duration=(args.duration or None),
                output="voiceover",
            ))
        elif args.cmd == "plan-job":
            script = args.script
            if args.script_file:
                script = Path(args.script_file).read_text(encoding="utf-8")
            voiceover = (args.voiceover or "").strip()
            if not (args.prompt or "").strip() and not (script or "").strip() and not voiceover:
                raise CliptudeApiError("Provide --prompt, --script / --script-file, or --voiceover.")
            template_id = _resolve_template_id(client, args.template)
            _print(client.create_job(
                template_id=template_id,
                title=args.title,
                prompt=args.prompt or None,
                script=script or None,
                voiceover_path=voiceover or None,
                duration=(args.duration or None),
                output="plan",
                avatar_profile_id=args.avatar,
                avatar_engine=args.avatar_engine or None,
            ))
        elif args.cmd == "narration":
            _print(client.get_narration(args.job_id))
        elif args.cmd == "catalog":
            types = [t.strip() for t in (args.types or "").split(",") if t.strip()]
            _print(client.get_kind_catalog(
                format=args.format or None, theme=args.theme or None, compact=args.compact, types=types or None,
                premium=args.premium,
            ))
        elif args.cmd in ("plan-check", "plan-lock"):
            plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
            _print(client.put_plan(args.job_id, plan, dry_run=args.cmd == "plan-check"))
        elif args.cmd == "plan-get":
            _print(client.get_plan(args.job_id))
        elif args.cmd == "plan-review":
            _print(client.get_plan_review(args.job_id))
        elif args.cmd == "plan-report":
            _print(client.get_plan_report(args.job_id))
        elif args.cmd == "download-voiceover":
            dest = Path(args.out)
            client.download_voiceover_file(args.job_id, dest)
            _print({"ok": True, "path": str(dest.resolve())})
        elif args.cmd == "continue":
            _print(client.continue_job(args.job_id))
        elif args.cmd == "editor":
            _print(client.open_editor_project(args.job_id))
        elif args.cmd == "agent":
            prompt = args.prompt
            if args.prompt_file:
                prompt = Path(args.prompt_file).read_text(encoding="utf-8")
            if not (prompt or "").strip():
                raise CliptudeApiError("Provide --prompt or --prompt-file.")
            items = [i.strip() for i in (args.items or "").split(",") if i.strip()]
            payload = client.run_agent(
                args.project_id, prompt=prompt, tier=args.tier, referenced_item_ids=items, chat_id=args.chat or None
            )
            if args.wait:
                run_id = (payload.get("run") or {}).get("id")
                payload = _wait(lambda: client.get_agent_run(args.project_id, run_id), lambda p: (p.get("run") or {}).get("status"))
            _print(payload)
        elif args.cmd == "agent-status":
            _print(client.get_agent_run(args.project_id, args.run_id))
        elif args.cmd == "agent-cancel":
            _print(client.cancel_agent_run(args.project_id, args.run_id))
        elif args.cmd == "agent-tiers":
            _print(client.agent_tiers())
        elif args.cmd == "render":
            payload = client.render_project(args.project_id)
            if args.wait:
                render_id = (payload.get("render") or {}).get("id")
                payload = _wait(lambda: client.get_render(render_id), lambda p: (p.get("render") or {}).get("status"))
            _print(payload)
        elif args.cmd == "render-status":
            _print(client.get_render(args.render_id))
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

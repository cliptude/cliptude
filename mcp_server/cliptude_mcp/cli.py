"""Tiny CLI wrapping /api/v1: auth | pick | templates | template-create | template-update | template-options | looks |
look-template | brand-kits | brand-kit | media | voices | voice-design | avatars | estimate | generate | editor | agent | render | script |
voiceover | continue | status | jobs | account | cancel | docs.

Live formats: faceless_documentary, faceless_top_10, avatar_video, talking_head, vector_explainer (16:9 only)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cliptude_mcp.client import CliptudeApiError, CliptudeClient


def _print(payload) -> None:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _screenshot_kw(args) -> dict:
    """{"screenshot": True/False} when --screenshot / --no-screenshot was given, else {} (the call is unchanged)."""
    value = getattr(args, "screenshot", None)
    return {} if value is None else {"screenshot": value}


def _add_screenshot_flag(parser) -> None:
    """--screenshot / --no-screenshot: say whether a picture is a screenshot (shown whole and still); default: measured."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--screenshot", dest="screenshot", action="store_const", const=True, default=None,
                       help="The picture is a screenshot of a post, thread, article or app screen: shown whole and still")
    group.add_argument("--no-screenshot", dest="screenshot", action="store_const", const=False,
                       help="The picture is a photo: cropped and moved like any picture, even if it looks like a screenshot")


def _add_review_args(parser) -> None:
    parser.add_argument("--review-token", default="", help="Token from the job review (shown when you run without it)")
    parser.add_argument(
        "--acknowledge", action="append", default=[],
        help="An item from acknowledgements_required the user approved, e.g. no_general_web_content (repeatable)",
    )


def _reviewed(args, call, *, heading: str = "Review this video before it is created:",
              question: str = "Create this video?"):
    """A call that spends credits, behind its review: in a terminal, show it and ask; otherwise print it and stop.

    Cliptude answers create_job, continue, product script/render, agent and paid render without a review_token
    with the review (HTTP 428) and does nothing. ``call(review_token, acknowledge)`` makes the request.
    """
    token = (getattr(args, "review_token", "") or "").strip() or None
    acks = [a.strip() for raw in (getattr(args, "acknowledge", None) or []) for a in raw.split(",") if a.strip()]
    try:
        return call(token, acks or None)
    except CliptudeApiError as exc:
        detail = exc.payload if isinstance(exc.payload, dict) else {}
        if exc.status != 428 or "review" not in detail or not sys.stdin.isatty():
            raise
    review = detail["review"]
    out = sys.stderr
    out.write(f"\n{heading}\n")
    if review.get("summary"):
        out.write(f"  {review['summary']}\n")
    for item in review.get("settings") or []:
        out.write(f"  {item.get('label')}: {item.get('value')}\n")
    credits = review.get("credits") or {}
    if credits.get("credits_required") is not None:
        out.write(f"  Credits: about {credits['credits_required']} (you have {credits.get('credits_available')})\n")
        for line in _rate_lines(review):
            out.write(f"  {line}\n")
    else:
        for key, value in credits.items():
            out.write(f"  Credits, {key.replace('_', ' ')}: {value}\n")
    for warning in review.get("warnings") or []:
        out.write(f"  ! {warning}\n")
    approved = []
    for ack in review.get("acknowledgements_required") or []:
        out.write(f"\n  {ack['message']}\n")
        if input(f"  Approve ({ack['id']})? [y/N] ").strip().lower() != "y":
            raise CliptudeApiError("Not done: change the template and run again.")
        approved.append(ack["id"])
    if input(f"\n{question} [y/N] ").strip().lower() != "y":
        raise CliptudeApiError("Not done: nothing was spent.")
    return call(detail["review_token"], approved or None)


def _rate_lines(review: dict) -> list[str]:
    """The per-minute rate, for a job review."""
    credits = review.get("credits") or {}
    lines = []
    if credits.get("credits_per_minute"):
        lines.append(f"Credits per minute: {credits['credits_per_minute']}")
    # The Look itself is a review setting ("Look: Empire Story (empire-story)"), printed above.
    return lines


def _create_reviewed(client: CliptudeClient, args, **kwargs):
    """create_job behind the job review (see _reviewed)."""
    return _reviewed(args, lambda token, acks: client.create_job(review_token=token, acknowledge=acks, **kwargs))


def _spend_reviewed(args, call, question: str):
    """A call on an existing job or project that spends credits, behind its review (see _reviewed)."""
    return _reviewed(args, lambda token, _acks: call(token), heading="This spends credits:", question=question)


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


def _add_thumbnail_args(parser: argparse.ArgumentParser, *, scope: str) -> None:
    parser.add_argument(
        "--thumbnail-prompt",
        default="",
        help=f"Thumbnail brief for {scope} (people, style, text). Not with --thumbnail-reference.",
    )
    parser.add_argument(
        "--thumbnail-reference",
        default="",
        help=(
            f"YouTube video link or https image whose thumbnail style {scope} copies. Videos need a brief or "
            "reference (here, on the template, or when the job is continued); Cliptude does not guess one."
        ),
    )


def _add_pronunciations_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pronunciations",
        default="",
        metavar="FILE",
        help='JSON file: a list of {"word": "Gmay", "alias": "guh-MAY"} or {"word", "phoneme", "alphabet": "ipa"} '
        "(ElevenLabs voices). Replaces the template's list for this video.",
    )


def _pronunciations_from_args(args) -> list | None:
    path = (getattr(args, "pronunciations", "") or "").strip()
    if not path:
        return None
    try:
        rules = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CliptudeApiError(f"--pronunciations: could not read {path}: {exc}") from exc
    if not isinstance(rules, list):
        raise CliptudeApiError("--pronunciations must be a JSON list of rules.")
    return rules


def _thumbnail_from_args(args) -> dict | None:
    thumbnail = {
        key: value.strip()
        for key, value in (
            ("custom_prompt", getattr(args, "thumbnail_prompt", "")),
            ("reference_url", getattr(args, "thumbnail_reference", "")),
        )
        if value and value.strip()
    }
    return thumbnail or None


def _add_branding_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--look", default=None,
                        help='A Look slug from `cliptude looks` whose formats include this one ("" removes it)')
    parser.add_argument("--brand-kit", type=int, default=None,
                        help="Brand kit id from `cliptude brand-kits` (0 = your default kit)")
    parser.add_argument("--show-logo", action=argparse.BooleanOptionalAction, default=None,
                        help="Put the brand kit's logo on the videos (--no-show-logo: off)")


def _add_captions_arg(parser: argparse.ArgumentParser, *, scope: str) -> None:
    parser.add_argument("--captions", action=argparse.BooleanOptionalAction, default=None,
                        help=f"Whole-video captions on {scope}, in the caption style. Off by default; "
                             "--no-captions turns them off. Omit to keep the template's.")


def _add_job_look_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--look", default=None,
                        help='A Look for this video only, overriding the template\'s: a slug from `cliptude looks` '
                             '("none" = no Look). Only the Looks `cliptude looks` lists are accepted for a new choice '
                             '(a no-longer-offered Look is refused with code look_hidden unless the template already has it).')
    parser.add_argument("--brand-kit", type=int, default=None,
                        help="A brand kit for this video only, overriding the template's (id from `cliptude brand-kit "
                             "list`; 0 = your default kit): its colours, fonts, subscribe-card channel and logo.")


def _add_new_brand_kit_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--new-brand-kit", default=None, metavar="JSON",
                        help='Make a new brand kit and give it to the template in the same call: a JSON object (or '
                             '@file.json) with name, channel_name, channel_handle, colors, fonts, logo_position and a '
                             'logo as logo_path (a local file, uploaded first), logo_url or logo_media_id. '
                             'Not with --brand-kit.')


def _new_brand_kit_from_args(args) -> dict | None:
    raw = getattr(args, "new_brand_kit", None)
    if not raw:
        return None
    text = Path(raw[1:]).read_text(encoding="utf-8") if raw.startswith("@") else raw
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CliptudeApiError(f"--new-brand-kit is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CliptudeApiError("--new-brand-kit must be a JSON object.")
    return data


def _add_brand_kit_field_args(parser: argparse.ArgumentParser, *, update: bool) -> None:
    parser.add_argument("--name", default=None, required=not update, help="The kit's name, usually the channel's")
    parser.add_argument("--channel-name", default=None,
                        help='Channel name on the subscribe card ("" clears it)')
    parser.add_argument("--channel-handle", default=None,
                        help='YouTube handle for the subscribe card, e.g. @northwindnotes ("" clears it)')
    parser.add_argument("--color", action="append", default=None, metavar="HEX",
                        help="A brand colour, #RRGGBB; repeat up to five times (first = main colour). "
                             "Replaces all the kit's colours.")
    parser.add_argument("--font", action="append", default=None, metavar="ROLE=FAMILY[:WEIGHT]",
                        help="A font, e.g. title=Oswald:700 or body=Inter; roles title, subtitle, body, label. "
                             "Repeatable; `cliptude brand-kit options` lists the families.")
    parser.add_argument("--logo-position", default=None,
                        choices=["auto", "top_left", "top_right", "bottom_left", "bottom_right", "none"])
    parser.add_argument("--logo", default=None, metavar="PATH|URL",
                        help="The logo: a local PNG/JPG/WebP file (uploaded) or a public https link (max 5 MB)")
    parser.add_argument("--logo-media-id", default=None, help="An image already in your media library")
    if update:
        parser.add_argument("--remove-logo", action="store_true", help="Take the logo off the kit")
    parser.add_argument("--default", action="store_true", help="Make it your default kit")


def _fonts_from_args(values: list[str] | None) -> dict | None:
    if not values:
        return None
    fonts: dict = {}
    for item in values:
        role, sep, rest = item.partition("=")
        if not sep or not role.strip() or not rest.strip():
            raise CliptudeApiError(f"--font {item!r}: use ROLE=FAMILY or ROLE=FAMILY:WEIGHT, e.g. title=Oswald:700.")
        family, _, weight = rest.partition(":")
        entry: dict = {"family": family.strip()}
        if weight.strip():
            try:
                entry["weight"] = int(weight)
            except ValueError as exc:
                raise CliptudeApiError(f"--font {item!r}: the weight is a number such as 700.") from exc
        fonts[role.strip()] = entry
    return fonts


def _brand_kit_fields_from_args(args) -> dict:
    """create_brand_kit / update_brand_kit keyword arguments from the flags (None = not sent)."""
    logo = (args.logo or "").strip()
    is_url = logo.lower().startswith(("http://", "https://"))
    fields = {
        "channel_name": args.channel_name,
        "channel_handle": args.channel_handle,
        "colors": args.color,
        "fonts": _fonts_from_args(args.font),
        "logo_position": args.logo_position,
        "logo_url": logo if is_url else None,
        "logo_path": logo if logo and not is_url else None,
        "logo_media_id": args.logo_media_id or None,
        "is_default": True if args.default else None,
    }
    if getattr(args, "remove_logo", False):
        fields["remove_logo"] = True
    return fields


def _run_media(client: CliptudeClient, args) -> None:
    cmd = args.media_cmd
    if cmd == "list":
        _print(client.list_media(folder=args.folder, kind=args.kind or None, limit=args.limit, offset=args.offset))
    elif cmd == "upload":
        if len(args.paths) > 50:
            raise CliptudeApiError("Upload at most 50 files a run.")
        if args.name and len(args.paths) != 1:
            raise CliptudeApiError("--name names one file: upload one file with it.")
        if args.name:
            _print(client.upload_to_library(args.paths[0], folder=args.folder, name=args.name, **_screenshot_kw(args)))
            return
        result = client.upload_many_to_library(args.paths, folder=args.folder, **_screenshot_kw(args))
        _print(result)
        if result.get("failed"):
            raise SystemExit(1)
    elif cmd == "import":
        _print(client.import_media(url=args.url, folder=args.folder, name=args.name, **_screenshot_kw(args)))


def _run_brand_kit(client: CliptudeClient, args) -> None:
    cmd = args.bk_cmd
    if cmd == "list":
        _print(client.list_brand_kits())
    elif cmd == "options":
        _print(client.brand_kit_options())
    elif cmd == "get":
        _print(client.get_brand_kit(args.kit_id))
    elif cmd == "create":
        _print(client.create_brand_kit(name=args.name, **_brand_kit_fields_from_args(args)))
    elif cmd == "update":
        fields = {k: v for k, v in _brand_kit_fields_from_args(args).items() if v is not None}
        if args.name is not None:
            fields["name"] = args.name
        if not fields:
            raise CliptudeApiError("Nothing to change: give --name, --channel-name, --channel-handle, --color, --font, "
                                   "--logo-position, --logo, --logo-media-id, --remove-logo or --default.")
        _print(client.update_brand_kit(args.kit_id, **fields))
    elif cmd == "set-default":
        _print(client.set_default_brand_kit(args.kit_id))
    elif cmd == "delete":
        if not args.yes:
            if not sys.stdin.isatty():
                raise CliptudeApiError("Deleting a brand kit cannot be undone: add --yes to confirm.")
            kit = client.get_brand_kit(args.kit_id)
            answer = input(f"Delete brand kit {kit.get('name', args.kit_id)!r}? Its logo stays in your media library. [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                print("Not deleted.", file=sys.stderr)
                return
        _print(client.delete_brand_kit(args.kit_id))


def _add_sponsor_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sponsor", default=None,
                        help="A media-library video (@nordvpn_ad or its file name) spliced in as a sponsored segment "
                             "with its own sound: the narration pauses for it. At most 3 minutes.")
    parser.add_argument("--sponsor-placement", default=None,
                        help="Where: 'at 2:00', 'after the intro', 'halfway', 'before chapter 3', 'before the "
                             "ending', 'at the start' or 'at the end' (snapped to a sentence end). Omit: Cliptude "
                             "places it after the hook and first chapter.")
    parser.add_argument("--sponsor-bumpers", action=argparse.BooleanOptionalAction, default=None,
                        help="'A word from our sponsor' / 'Back to the story' cards around it (on by default; "
                             "--no-sponsor-bumpers leaves them out)")


def _add_aroll_args(parser: argparse.ArgumentParser, *, include: bool = True) -> None:
    parser.add_argument(
        "--aroll", action="append", default=[], metavar="SPEC",
        help='Your own A-roll clip, played with its own sound (the narration pauses for it): a media-library video '
             'or audio file and, optionally, where and what it says, e.g. '
             '\'@kress_cnbc after "they refused to take any more questions"\' or '
             '\'@phone_call halfway says "We will not reinstate him"\'. A quoted line must be in the script word for '
             'word. JSON works too: \'{"media": "@clip", "after": "a line"}\'. Repeatable, up to 4; at most 3 '
             'minutes each. Upload clips with `cliptude media upload`.')
    if include:
        parser.add_argument(
            "--include-aroll", action=argparse.BooleanOptionalAction, default=None,
            help="Cliptude's own A-roll inserts (Faceless Documentary): it finds 2-4 clips of people speaking on "
                 "YouTube and splices them in with their sound. --no-include-aroll turns them off. Default: the "
                 "template's setting.")


def _aroll_from_args(args) -> list | None:
    """--aroll SPEC values: '@clip <placement words>' or a JSON object."""
    out = []
    for spec in getattr(args, "aroll", None) or []:
        spec = str(spec or "").strip()
        if not spec:
            continue
        if spec.startswith("{"):
            try:
                item = json.loads(spec)
            except ValueError as exc:
                raise CliptudeApiError(f"--aroll is not valid JSON: {exc}") from exc
            if not isinstance(item, dict):
                raise CliptudeApiError("--aroll JSON must be an object, like {\"media\": \"@clip\", \"after\": \"a line\"}.")
            out.append(item)
            continue
        media, _, where = spec.partition(" ")
        out.append({"media": media, "placement": where.strip()} if where.strip() else media)
    return out or None


def _sponsor_kwargs(args) -> dict:
    """The sponsor, A-roll and include-A-roll flags, shared by estimate, generate and plan-job."""
    out = {"sponsor": getattr(args, "sponsor", None) or None,
           "sponsor_placement": getattr(args, "sponsor_placement", None) or None,
           "sponsor_bumpers": getattr(args, "sponsor_bumpers", None),
           "aroll": _aroll_from_args(args)}
    if getattr(args, "include_aroll", None) is not None:
        out["include_aroll"] = args.include_aroll
    return out


def _add_voice_setting_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stability", type=float, default=None, help="0 to 1")
    parser.add_argument("--similarity-boost", type=float, default=None, help="0 to 1")
    parser.add_argument("--style", type=float, default=None, help="0 to 1")
    parser.add_argument("--speed", type=float, default=None, help="0.7 to 1.2")
    parser.add_argument("--speaker-boost", action=argparse.BooleanOptionalAction, default=None,
                        dest="use_speaker_boost")


def _voice_settings_from_args(args) -> dict:
    keys = ("stability", "similarity_boost", "style", "speed", "use_speaker_boost")
    return {k: getattr(args, k) for k in keys if getattr(args, k, None) is not None}


def _save_previews(payload, out_dir: str) -> dict:
    """Write each preview's data: URL audio to a file and replace it with the path."""
    import base64 as _b64

    if not isinstance(payload, dict):
        return payload
    folder = Path(out_dir).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    for preview in payload.get("previews") or []:
        url = str(preview.pop("audio_url", "") or "")
        if url.startswith("data:") and ";base64," in url:
            mime, data = url[5:].split(";base64,", 1)
            ext = {"audio/wav": "wav", "audio/ogg": "ogg"}.get(mime, "mp3")
            dest = folder / f"preview_{preview.get('preview_id')}.{ext}"
            dest.write_bytes(_b64.b64decode(data))
            preview["file"] = str(dest)
    return payload


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


def _run_docs(client: CliptudeClient, args) -> None:
    """cliptude docs search|read|list: Markdown and plain text by default, --json for the API response."""
    cmd = args.docs_cmd
    if cmd == "search":
        payload = client.search_docs(" ".join(args.query), limit=args.limit)
        if args.json:
            _print(payload)
            return
        for row in payload.get("results") or []:
            heading = f" > {row['section']}" if row.get("section") and row["section"] != row.get("title") else ""
            print(f"## {row.get('title')}{heading}\n{row.get('url')}  (read: cliptude docs read {row.get('slug')}"
                  + (f' --section "{row["section"]}"' if heading else "") + f")\n\n{row.get('excerpt', '')}\n")
        if not payload.get("results"):
            print("No docs section matches. Try other words, or `cliptude docs list`.", file=sys.stderr)
    elif cmd == "read":
        payload = client.read_doc(args.slug, section=args.section or None, offset=args.offset, max_chars=args.max_chars)
        if args.json:
            _print(payload)
            return
        print(f"Source: {payload.get('url')}\n")
        print(payload.get("content", ""))
        if payload.get("next_offset") is not None:
            print(f"\n[{payload['next_offset']} of {payload.get('total_chars')} characters shown; continue with "
                  f"--offset {payload['next_offset']}]", file=sys.stderr)
    else:
        payload = client.list_docs(category=args.category or None)
        if args.json:
            _print(payload)
            return
        for row in payload.get("results") or []:
            print(f"{row.get('slug'):<28} {row.get('title')}  [{row.get('category')}]")


def _run_voice_design(client: CliptudeClient, args) -> None:
    import base64 as _b64

    cmd = args.vd_cmd
    if cmd == "options":
        _print(client.voice_design_options())
    elif cmd == "previews":
        options = {"model_id": args.model_id or None, "should_enhance": args.should_enhance, "text": args.text or None,
                   "auto_generate_text": True if args.auto_text else None, "loudness": args.loudness,
                   "guidance_scale": args.guidance_scale, "quality": args.quality,
                   "prompt_strength": args.prompt_strength, "seed": args.seed}
        payload = client.design_voice_previews(args.provider, args.description, language=args.language or None,
                                               options=options)
        _print(_save_previews(payload, args.out))
    elif cmd == "save":
        _print(client.save_designed_voice(args.provider, args.preview_id, args.name,
                                          voice_settings=_voice_settings_from_args(args)))
    elif cmd == "settings":
        settings = _voice_settings_from_args(args)
        if not settings:
            raise CliptudeApiError("Give at least one of --stability, --similarity-boost, --style, --speed, --speaker-boost.")
        _print(client.update_designed_voice_settings(args.design_id, settings))
    elif cmd == "try":
        payload = client.try_designed_voice(args.design_id, text=args.text or None,
                                            voice_settings=_voice_settings_from_args(args),
                                            model_id=args.model_id or None, language_code=args.language_code or None)
        url = str((payload or {}).pop("audio_url", "") or "")
        if ";base64," in url:
            dest = Path(args.out).expanduser()
            dest.write_bytes(_b64.b64decode(url.split(";base64,", 1)[1]))
            payload["file"] = str(dest.resolve())
        _print(payload)
    elif cmd == "remix":
        payload = client.remix_designed_voice(args.design_id, args.description, options={
            "guidance_scale": args.guidance_scale, "loudness": args.loudness, "text": args.text or None,
            "seed": args.seed})
        _print(_save_previews(payload, args.out))
    elif cmd == "remix-save":
        _print(client.save_remixed_voice(args.design_id, args.preview_id, voice_name=args.name or None))
    elif cmd == "delete":
        _print(client.delete_designed_voice(args.provider, args.design_id))


def _run_pick(client: CliptudeClient, args) -> dict:
    """Open a browser picker (made here with --kind, or one an agent got as browser_picker_url) and wait for it.

    The page is the same widget Claude Desktop shows (avatars, voices, music, a review to confirm, ...), on
    cliptude.com for the signed-in owner, for 15 minutes. Returns the pick once the user is done.
    """
    import time
    import webbrowser

    pick_id, url = (args.pick_id or "").strip(), (args.url or "").strip()
    if args.kind:
        try:
            source = json.loads(args.source or "{}")
        except ValueError as exc:
            raise CliptudeApiError("--source must be a JSON object.") from exc
        if not isinstance(source, dict):
            raise CliptudeApiError("--source must be a JSON object.")
        created = client.create_pick(args.kind, source)
        pick_id, url = str(created.get("pick_id") or ""), str(created.get("url") or "")
    if not pick_id:
        raise CliptudeApiError("Give a pick_id (with --url to open it), or --kind to make a new picker.")
    if url:
        if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise CliptudeApiError("--url must be the https link Cliptude gave (browser_picker_url).")
        print(f"Pick in your browser: {url}", file=sys.stderr)
        if not args.no_open:
            webbrowser.open(url)
    deadline = time.monotonic() + max(5.0, float(args.timeout))
    while True:
        payload = client.get_pick(pick_id)
        status = str(payload.get("status") or "")
        if status != "open" or time.monotonic() > deadline:
            return payload
        print("Waiting for your pick in the browser...", file=sys.stderr)
        time.sleep(max(1.0, float(args.interval)))


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
    from cliptude_mcp.client import resolve_connection

    conn = resolve_connection()
    if conn["runtime"] == "desktop":
        if conn["error"]:
            print(conn["error"], file=sys.stderr)
            raise SystemExit(1)
        account = CliptudeClient(client_kind="cli").get_account()
        print(f"Using the Cliptude desktop app as {account.get('email')} (no cliptude.com login needed).")
        return
    if not stored_access_token():
        print("Not authenticated. Run: cliptude auth login", file=sys.stderr)
        raise SystemExit(1)
    try:
        account = CliptudeClient(client_kind="cli").get_account()
    except Exception as exc:
        _die(exc)
    email = account.get("email") if isinstance(account, dict) else None
    print(f"Logged in{f' as {email}' if email else '.'}")


def _run_use(target: str | None) -> None:
    from cliptude_mcp.client import chosen_target, resolve_connection, save_target

    if target:
        save_target(target)
    conn = resolve_connection()
    where = {"desktop": "the Cliptude desktop app", "cloud": "cliptude.com"}.get(conn["runtime"], conn["base_url"])
    print(f"Target: {chosen_target()} → {where}" + (f" ({conn['base_url']})" if conn["base_url"] else ""))
    if conn["error"]:
        print(conn["error"], file=sys.stderr)
        raise SystemExit(1)


def _review_arg(value: str) -> dict:
    """--review "quote|author|rating" -> {quote, author, rating}."""
    parts = [p.strip() for p in value.split("|")]
    review = {"quote": parts[0], "author": parts[1] if len(parts) > 1 else ""}
    if len(parts) > 2 and parts[2]:
        review["rating"] = float(parts[2])
    return review


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="cliptude",
        description="Cliptude jobs CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude generate --template "Finance vector weekly" --title "Why index funds win" '
            '--prompt "why most retail traders underperform the S&P over 10 years, keep it for beginners" '
            '--duration 6-8'
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    auth = sub.add_parser("auth", help="Sign in with a browser")
    auth_sub = auth.add_subparsers(dest="auth_cmd", required=True)
    auth_sub.add_parser("login", help="Open a browser and save a local session")
    auth_sub.add_parser("logout", help="Delete the local session")
    auth_sub.add_parser("status", help="Show whether this machine is signed in")

    use = sub.add_parser(
        "use",
        help="Make videos in the Cliptude desktop app or on cliptude.com (no argument: show where)",
    )
    use.add_argument("target", nargs="?", choices=["desktop", "cloud", "auto"],
                     help="auto = the desktop app while it is running, else cliptude.com")

    pk = sub.add_parser(
        "pick",
        help="Open a browser picker (avatars, voices, music, a review to confirm, ...) and wait for the pick",
        description=(
            "Clients without MCP Apps widgets get browser_picker_url and pick_id from the MCP tools. This opens the "
            "link in your browser and waits until you pick (or confirm) there, then prints the result. With --kind "
            "it makes a new picker first."
        ),
    )
    pk.add_argument("pick_id", nargs="?", default="", help="pick_id from the tool result")
    pk.add_argument("--url", default="", help="browser_picker_url from the tool result")
    pk.add_argument("--kind", default="", choices=["", "avatars", "voices", "characters", "choices", "templates",
                                                   "template", "job", "script", "publish_kit", "video"],
                    help="Make a new picker of this kind instead")
    pk.add_argument("--source", default="{}", help='Where Cliptude reads it from, JSON, e.g. {"provider": "elevenlabs"}')
    pk.add_argument("--no-open", action="store_true", help="Print the link instead of opening a browser")
    pk.add_argument("--timeout", type=float, default=900.0, help="Seconds to wait (the link lasts 15 minutes)")
    pk.add_argument("--interval", type=float, default=3.0, help="Seconds between checks")

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
    _add_thumbnail_args(tc, scope="every video from this template")
    _add_branding_args(tc)
    _add_new_brand_kit_arg(tc)
    _add_captions_arg(tc, scope="every video from this template")

    tu = sub.add_parser(
        "template-update",
        help="Rename a template, set its Look, brand kit or logo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude template-update "TSMC doc" --look empire-story --brand-kit 3 --show-logo\n'
            '  cliptude template-update "TSMC doc" --look ""   (removes the Look)'
        ),
    )
    tu.add_argument("template", help="Template name or UUID")
    tu.add_argument("--name", default=None, help="New name")
    _add_branding_args(tu)
    _add_new_brand_kit_arg(tu)
    _add_captions_arg(tu, scope="every video from this template")

    lk = sub.add_parser("looks", help="List the offered Looks (formats, colours, boards, sample reel)")
    lk.add_argument("--topic", default="", help="The video's title or description: the Looks that fit it come first with suggested=true and a reason")
    lk.add_argument("--format", default="", help="Only suggest Looks that work with this format")
    lt = sub.add_parser("look-template", help="Save a Look as one of your templates; prints the template")
    lt.add_argument("slug", help="Look slug from `cliptude looks`")
    lt.add_argument("--name", default="", help="Template name (default: the Look's name)")
    lt.add_argument("--format", default="", help="One of the Look's formats (default: its own)")
    sub.add_parser("brand-kits", help="List your brand kits (id for --brand-kit)")

    dc = sub.add_parser("docs", help="Search and read the Cliptude docs as Markdown (no login needed)")
    dc_sub = dc.add_subparsers(dest="docs_cmd", required=True)
    dcs = dc_sub.add_parser("search", help="Docs sections that answer a question (title, section, url, excerpt)")
    dcs.add_argument("query", nargs="+", help="The question or words to look for")
    dcs.add_argument("--limit", type=int, default=8, help="How many sections (1-20)")
    dcs.add_argument("--json", action="store_true", help="Print the JSON response")
    dcr = dc_sub.add_parser("read", help="A docs page (or one section) as Markdown")
    dcr.add_argument("slug", help="Page slug from `cliptude docs list` or search (credits, looks, api, ...)")
    dcr.add_argument("--section", default="", help="Only this section heading and its subsections")
    dcr.add_argument("--offset", type=int, default=0, help="Continue a long page from this character (next_offset)")
    dcr.add_argument("--max-chars", type=int, default=20000, help="At most this many characters (500-20000)")
    dcr.add_argument("--json", action="store_true", help="Print the JSON response")
    dcl = dc_sub.add_parser("list", help="Every docs page: slug, title, category")
    dcl.add_argument("--category", default="", help="Only this sidebar group (Getting Started, Guides, Editor, ...)")
    dcl.add_argument("--json", action="store_true", help="Print the JSON response")
    md = sub.add_parser(
        "media",
        help="Your media library: list, upload files (clips, pictures, audio), import a public link",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  cliptude media list --folder jensen_followup\n"
            "  cliptude media upload ./jensen.mp4 ./shots/*.png --folder jensen_followup\n"
            "  cliptude media upload ./kress.mp4 --name kress_cnbc.mp4\n"
            "  cliptude media import https://example.com/clip.mp4 --folder b_roll\n"
            "Each file comes back with its @tag (kress_cnbc.mp4 -> @kress_cnbc) for --prompt, --aroll or --sponsor.\n"
            "Limits: pictures 15 MB, clips 300 MB, audio 95 MB."
        ),
    )
    md_sub = md.add_subparsers(dest="media_cmd", required=True)
    mdl = md_sub.add_parser("list", help="List your media library (each file's url and @tag)")
    mdl.add_argument("--folder", default=None, help="Only this folder ('' = files in no folder)")
    mdl.add_argument("--kind", default="", choices=["", "image", "video", "audio"], help="Only this kind")
    mdl.add_argument("--limit", type=int, default=50)
    mdl.add_argument("--offset", type=int, default=0)
    mdu = md_sub.add_parser("upload", help="Upload local files to your media library (up to 50 a run)")
    mdu.add_argument("paths", nargs="+", help="Files: pictures, clips (MP4, MOV, WebM) or audio")
    mdu.add_argument("--folder", default=None, help="Library folder for all of them (tag it as @folder_name)")
    mdu.add_argument("--name", default=None, help="A name (its @tag) when you upload one file")
    _add_screenshot_flag(mdu)
    mdi = md_sub.add_parser("import", help="Download a public https link to a file into your media library")
    mdi.add_argument("url")
    mdi.add_argument("--folder", default=None, help="Library folder")
    mdi.add_argument("--name", default=None, help="File name with extension (its @tag), needed when the link has none")
    _add_screenshot_flag(mdi)
    bk = sub.add_parser(
        "brand-kit",
        help="Brand kits (one per channel): list, options, get, create, update, set-default, delete",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            '  cliptude brand-kit create --name "Northwind Notes" --channel-handle @northwindnotes '
            "--color '#1F6FEB' --color '#FFFFFF' --font title=Oswald:700 --logo ./logo.png\n"
            "  cliptude brand-kit update 3 --logo https://example.com/logo.png --logo-position top_left\n"
            "  cliptude brand-kit set-default 3\n"
            "  cliptude brand-kit delete 3 --yes"
        ),
    )
    bk_sub = bk.add_subparsers(dest="bk_cmd", required=True)
    bk_sub.add_parser("list", help="List your brand kits")
    bk_sub.add_parser("options", help="Fonts, weights, logo positions, colour format and limits")
    bkg = bk_sub.add_parser("get", help="Show one brand kit")
    bkg.add_argument("kit_id", type=int)
    bkc = bk_sub.add_parser("create", help="Make a brand kit")
    _add_brand_kit_field_args(bkc, update=False)
    bku = bk_sub.add_parser("update", help="Change a brand kit (only the flags given)")
    bku.add_argument("kit_id", type=int)
    _add_brand_kit_field_args(bku, update=True)
    bks = bk_sub.add_parser("set-default", help="Make a kit your default")
    bks.add_argument("kit_id", type=int)
    bkd = bk_sub.add_parser("delete", help="Delete a brand kit (its logo stays in your media library)")
    bkd.add_argument("kit_id", type=int)
    bkd.add_argument("--yes", action="store_true", help="Do not ask to confirm")

    vd = sub.add_parser(
        "voice-design",
        help="Design your own voice: options, previews, save, settings, try, remix, delete",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  cliptude voice-design options\n"
            '  cliptude voice-design previews --description "Calm male narrator, late thirties, low and warm" '
            "--model-id eleven_ttv_v3 --guidance-scale 6\n"
            "  cliptude voice-design save <preview-id> --name \"Night Narrator\" --stability 0.4\n"
            "  cliptude voice-design settings <design-id> --speed 1.05 --no-speaker-boost\n"
            '  cliptude voice-design try <design-id> --text "One line to hear it." --out try.mp3\n'
            '  cliptude voice-design remix <design-id> --description "a little warmer"\n'
            "  cliptude voice-design remix-save <design-id> <preview-id>\n"
            "  cliptude voice-design delete <design-id>\n"
            "Design ids are design_id in `cliptude voices` (my_voices). Numbers outside a range are clamped."
        ),
    )
    vd_sub = vd.add_subparsers(dest="vd_cmd", required=True)
    vd_sub.add_parser("options", help="Design models, option ranges and defaults, voice setting ranges")
    vdp = vd_sub.add_parser("previews", help="Design previews from a description (preview audio saved as files)")
    vdp.add_argument("--provider", default="elevenlabs", choices=["elevenlabs", "minimax", "inworld"])
    vdp.add_argument("--description", required=True)
    vdp.add_argument("--language", default="")
    vdp.add_argument("--model-id", default="")
    vdp.add_argument("--enhance", action=argparse.BooleanOptionalAction, default=None, dest="should_enhance")
    vdp.add_argument("--text", default="", help="Preview line, 100-1000 characters")
    vdp.add_argument("--auto-text", action="store_true", help="ElevenLabs writes the preview line")
    for flag in ("loudness", "guidance-scale", "quality", "prompt-strength"):
        vdp.add_argument(f"--{flag}", type=float, default=None)
    vdp.add_argument("--seed", type=int, default=None)
    vdp.add_argument("--out", default=".", help="Folder for the preview audio files")
    vds = vd_sub.add_parser("save", help="Keep one preview as your voice")
    vds.add_argument("preview_id")
    vds.add_argument("--name", required=True)
    vds.add_argument("--provider", default="elevenlabs", choices=["elevenlabs", "minimax", "inworld"])
    _add_voice_setting_args(vds)
    vdt = vd_sub.add_parser("settings", help="Change a designed ElevenLabs voice's settings (others keep theirs)")
    vdt.add_argument("design_id")
    _add_voice_setting_args(vdt)
    vdy = vd_sub.add_parser("try", help="Speak a test line with these settings (saves an MP3)")
    vdy.add_argument("design_id")
    vdy.add_argument("--text", default="")
    vdy.add_argument("--model-id", default="")
    vdy.add_argument("--language-code", default="")
    vdy.add_argument("--out", default="voice_try.mp3")
    _add_voice_setting_args(vdy)
    vdr = vd_sub.add_parser("remix", help="Previews of a designed ElevenLabs voice with a change")
    vdr.add_argument("design_id")
    vdr.add_argument("--description", required=True)
    vdr.add_argument("--guidance-scale", type=float, default=None)
    vdr.add_argument("--loudness", type=float, default=None)
    vdr.add_argument("--text", default="")
    vdr.add_argument("--seed", type=int, default=None)
    vdr.add_argument("--out", default=".", help="Folder for the preview audio files")
    vdrs = vd_sub.add_parser("remix-save", help="Replace the voice with a remix preview")
    vdrs.add_argument("design_id")
    vdrs.add_argument("preview_id")
    vdrs.add_argument("--name", default="")
    vdd = vd_sub.add_parser("delete", help="Delete a designed voice here and at the provider")
    vdd.add_argument("design_id")
    vdd.add_argument("--provider", default="elevenlabs", choices=["elevenlabs", "minimax", "inworld"])

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

    vcl = sub.add_parser("vector-characters", help="List Vector Explainer characters (built-in and your uploads)")
    vcu = sub.add_parser(
        "vector-character-upload",
        help="Add your own Vector Explainer character from a local image or a public URL; prints its id",
    )
    vcu.add_argument("--name", required=True, help="Short name for the character")
    vcu.add_argument("--file", default="", help="Local PNG, JPG or WEBP")
    vcu.add_argument("--url", default="", help="Public https link to the image")
    vcd = sub.add_parser("vector-character-delete", help="Delete one of your uploaded Vector Explainer characters")
    vcd.add_argument("character_id")

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
    est.add_argument("--avatar-engine", default="", choices=["", "6", "iv"], help="Avatar Video: 6 (Avatar 6, the only engine; iv is read as 6). Default: the template's")
    est.add_argument("--vector-motion", default="", choices=["", "none", "video"], help="Vector Explainer: price stills (none) or AI clips (video)")
    _add_captions_arg(est, scope="this video")
    _add_job_look_arg(est)
    _add_sponsor_args(est)
    _add_aroll_args(est)
    est.add_argument("--render-only", action="store_true", help="Plan job where you bring the voiceover, pictures, clips and graphics (with --output plan --voiceover-seconds)")

    g = sub.add_parser(
        "generate",
        help="Start a job",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude generate --template "Finance vector weekly" --title "Why index funds win" '
            '--prompt "why most retail traders underperform the S&P over 10 years, keep it for beginners" '
            '--duration 6-8\n'
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
        help='The description: a topic or brief (only when Cliptude should write the narration), and any direction '
        'for the visuals ("Open with a map of Europe. Use @founder_photo"). With --script, --voiceover or --talking-head '
        "it is direction only. Captions are off by default (turn them on later with `edit` and set_captions).",
    )
    g.add_argument("--script", default="")
    g.add_argument("--script-file", default="")
    g.add_argument(
        "--voiceover",
        default="",
        help="Path to a narration audio file (MP3, M4A, WAV, …), at least 6 minutes long. Transcribed; skips TTS. Not with --script; a --prompt with it is direction for the visuals.",
    )
    g.add_argument("--voiceover-url", default="", help="Public https link to the narration audio (at least 6 minutes long); Cliptude downloads it. Not with --script; --prompt is direction.")
    g.add_argument(
        "--talking-head",
        default="",
        help="Path to a talking-head presenter video (MP4, MOV, WEBM, AVI), at least 6 minutes long. Requires a Talking Head template. Transcribed; skips TTS. Not with --script or --voiceover; a --prompt with it is direction for the visuals.",
    )
    g.add_argument(
        "--duration",
        default="",
        help="Duration bucket: 6-8, 8-10, 10-12, 18-20, 30-40 or 50-60 (videos start at 6 minutes). Default: template duration (or estimated from --script)",
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
    g.add_argument("--avatar-engine", default="", choices=["", "6", "iv"], help="Avatar Video: 6 (Avatar 6, the only engine; iv is read as 6). Default: the template's")
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
        "--disable-sound-effects",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="No sound effects; --no-disable-sound-effects turns them on for this video even if the template has them off. Omit to use the template.",
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
    _add_thumbnail_args(g, scope="this video")
    _add_pronunciations_arg(g)
    g.add_argument("--vector-motion", default="", choices=["", "none", "video"], help='Vector Explainer: "none" = stills with camera moves, "video" = AI clips. Default: the template\'s')
    g.add_argument("--vector-art-style", default=None, choices=["", "flat", "luminous", "low_poly", "mannequin", "paper", "clay", "flat_cartoon"], help='Vector Explainer art style: flat, luminous (glowing, no outlines), low_poly (faceted 3D), mannequin (clay diorama), paper (layered cardstock) or clay (claymation); "" follows the Look. Default: the template\'s')
    g.add_argument("--vector-image-model", default="", choices=["", "gpt_image", "grok"], help='Vector Explainer image model: gpt_image (more detail) or grok (simpler, cheaper). Default: the template\'s')
    _add_captions_arg(g, scope="this video")
    _add_job_look_arg(g)
    _add_sponsor_args(g)
    _add_aroll_args(g)
    g.add_argument("--vector-character", default="", help='Vector Explainer: character id from `cliptude vector-characters`, or "none". Default: the template\'s')

    sc = sub.add_parser(
        "script",
        help="Generate a spoken script only (no video)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            '  cliptude script --template "Finance vector weekly" --title "Why index funds win" '
            '--prompt "why most retail traders underperform the S&P" --duration 6-8'
        ),
    )
    sc.add_argument("--template", required=True, help="Template name or UUID")
    sc.add_argument("--title", required=True)
    sc.add_argument("--prompt", required=True, help="Topic or brief. Cliptude writes the narration.")
    sc.add_argument("--duration", default="", help="Duration bucket, e.g. 6-8 or 10-12 (videos start at 6 minutes)")

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
    vo.add_argument("--duration", default="", help="Duration bucket, e.g. 6-8 or 10-12 (videos start at 6 minutes)")

    dv = sub.add_parser("download-voiceover", help="Download the voiceover MP3 of a voiceover job, or the narration of a plan_ready plan job")
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
    _add_thumbnail_args(cont, scope="this video")
    _add_review_args(cont)

    prod = sub.add_parser(
        "product",
        help="Product videos from a store: fetch a product, draft its 80/20 script, render, check status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  cliptude product fetch https://store.com/products/handle\n"
            "  cliptude product script <product-id> --angle 1 --duration 10-12 --discount-code YOUTUBE15\n"
            "  cliptude product render <job-id> --aspect-ratio 16:9\n"
            "  cliptude product status <job-id>"
        ),
    )
    prod_sub = prod.add_subparsers(dest="product_cmd", required=True)
    pf = prod_sub.add_parser("fetch", help="Read a product URL (or search a store) and get 3 video angles")
    pf.add_argument("url_or_query")
    pf.add_argument("--store", default="", help="Store domain to search, e.g. acme.com")
    ps = prod_sub.add_parser("script", help="Queue the 80/20 educational script for a product")
    ps.add_argument("product_id")
    ps.add_argument("--angle", default="0", help="Angle index 0-2, or a custom educational title")
    ps.add_argument("--duration", default="6-8", help="6-8, 10-12, 18-20, 30-40, 50-60 or minutes (6-60)")
    ps.add_argument("--template", default="", help="Faceless Documentary template id")
    ps.add_argument("--title", default="")
    ps.add_argument("--discount-code", default="")
    ps.add_argument("--cta-url", default="")
    ps.add_argument("--review", action="append", default=[],
                    help='A real customer review, "quote|author|rating" (repeat up to 3 times)')
    ps.add_argument("--script-file", default="",
                    help="Narration you wrote (text file); the job is script_ready at once. Omit to let Cliptude write it")
    ps.add_argument("--plan", action="store_true",
                    help="You plan every visual: the job is voiced and stops at plan_ready (plan-check, plan-lock, then product render)")
    _add_review_args(ps)
    pr = prod_sub.add_parser("render", help="Produce the video from a ready product script")
    pr.add_argument("job_id")
    pr.add_argument("--aspect-ratio", default="16:9", choices=["16:9"], help="Cliptude makes 16:9 videos only")
    _add_thumbnail_args(pr, scope="this video")
    _add_review_args(pr)
    pst = prod_sub.add_parser("status", help="Stage, script, preview link and MP4 of a product video")
    pst.add_argument("job_id")

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
    pj.add_argument("--template", required=True, help="Template name or UUID (Faceless Documentary, Top 10, Avatar Video, Vector Explainer; not a motion-graphics Look: use generate)")
    pj.add_argument("--title", required=True)
    pj.add_argument("--prompt", default="", help="Topic or brief, only when Cliptude writes the narration. Omit it with --script or --voiceover: your plan is the visual direction.")
    pj.add_argument("--script", default="")
    pj.add_argument("--script-file", default="")
    pj.add_argument("--voiceover", default="", help="Local audio to use as the narration (aligned, not re-voiced)")
    pj.add_argument("--voiceover-url", default="", help="Public https link to the narration audio; Cliptude downloads it")
    pj.add_argument("--duration", default="", help="Wizard duration bucket, e.g. 6-8")
    pj.add_argument("--avatar", type=int, default=None, help="Avatar Video: avatar id from `cliptude avatars`. Default: the template's avatar")
    pj.add_argument("--avatar-engine", default="", choices=["", "6", "iv"], help="Avatar Video: 6 (Avatar 6, the only engine; iv is read as 6). Default: the template's")
    pj.add_argument("--render-only", action="store_true", help="You bring everything (needs --voiceover); Cliptude only builds and renders, at the render-only rate")
    _add_thumbnail_args(pj, scope="this video")
    _add_pronunciations_arg(pj)
    pj.add_argument("--vector-motion", default="", choices=["", "none", "video"], help='Vector Explainer: "none" = stills with camera moves, "video" = AI clips. Default: the template\'s')
    pj.add_argument("--vector-art-style", default=None, choices=["", "flat", "luminous", "low_poly", "mannequin", "paper", "clay", "flat_cartoon"], help='Vector Explainer art style: flat, luminous (glowing, no outlines), low_poly (faceted 3D), mannequin (clay diorama), paper (layered cardstock) or clay (claymation); "" follows the Look. Default: the template\'s')
    pj.add_argument("--vector-image-model", default="", choices=["", "gpt_image", "grok"], help='Vector Explainer image model: gpt_image (more detail) or grok (simpler, cheaper). Default: the template\'s')
    _add_captions_arg(pj, scope="this video")
    _add_job_look_arg(pj)
    _add_sponsor_args(pj)
    _add_aroll_args(pj, include=False)
    pj.add_argument("--vector-character", default="", help='Vector Explainer: character id from `cliptude vector-characters`, or "none". Default: the template\'s')

    nr = sub.add_parser("narration", help="Numbered sentences and word timings of a plan_ready job")
    nr.add_argument("job_id")

    cat = sub.add_parser("catalog", help="Graphics an agent can place (kinds, types, overlay styles)")
    cat.add_argument("--format", default="", help="faceless_documentary, faceless_top_10, avatar_video or vector_explainer")
    cat.add_argument("--theme", default="")
    cat.add_argument("--compact", action="store_true", help="One summary line per graphic")
    cat.add_argument("--types", default="", help="Comma-separated types/styles for full entries")
    cat.add_argument("--premium", action="store_true", help="Only the premium boards")
    cat.add_argument("--look", default="", help="The job's Look slug: a Look with a spec lists only its own boards, with look_note on when to use them; without --look the look-only boards are left out")

    for name, text in (("plan-check", "Validate a plan file (nothing stored)"), ("plan-lock", "Validate and lock a plan file")):
        pc = sub.add_parser(name, help=text)
        pc.add_argument("job_id")
        pc.add_argument("plan_file", help="JSON file with the cliptude.plan/v1 plan")

    pg = sub.add_parser("plan-get", help="Show the locked plan")
    pg.add_argument("job_id")
    pv = sub.add_parser("plan-review", help="The built design beat by beat and graphic by graphic (review before rendering)")
    pv.add_argument("job_id")
    pcd = sub.add_parser("plan-candidates", help="Every picture or clip sourcing checked for some beats, with keep ids")
    pcd.add_argument("job_id")
    pcd.add_argument("--beats", help="Comma-separated beat ids, e.g. b12,b40")
    pcd.add_argument("--start", type=float, help="Or every beat starting in this window: start (s)")
    pcd.add_argument("--end", type=float, help="End of the window (s)")
    pcs = sub.add_parser("plan-candidate-sheets", help="Contact sheets of every candidate for up to 6 beats (saves JPEGs)")
    pcs.add_argument("job_id")
    pcs.add_argument("--beats", help="Comma-separated beat ids (at most 6)")
    pcs.add_argument("--start", type=float, help="Or the beats starting in this window: start (s)")
    pcs.add_argument("--end", type=float, help="End of the window (s)")
    pcs.add_argument("--out", default=".", help="Folder for the sheets (default: here)")
    pr = sub.add_parser("plan-report", help="What sourcing used for each media request of the locked plan")
    pr.add_argument("job_id")
    um = sub.add_parser("upload-media", help="Send a picture or clip you found to a plan job; prints its keep id for media.keep")
    um.add_argument("job_id")
    um.add_argument("path", help="Local JPG/PNG/WEBP/GIF (up to 15 MB) or MP4/MOV/WEBM/M4V (up to 300 MB)")
    um.add_argument("--split-shots", action="store_true",
                    help="Also cut a long source into 3-12 s clips on its cuts, each checked and kept (prints their keep ids)")
    _add_screenshot_flag(um)

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
    _add_review_args(ag)

    ags = sub.add_parser("agent-status", help="Get a Cliptude Agent run")
    ags.add_argument("project_id")
    ags.add_argument("run_id")

    agc = sub.add_parser("agent-cancel", help="Cancel a Cliptude Agent run")
    agc.add_argument("project_id")
    agc.add_argument("run_id")

    sub.add_parser("agent-tiers", help="List Cliptude Agent tiers")

    ed = sub.add_parser("edit", help="Apply exact timeline edits (no model call, no credits)")
    ed.add_argument("project_id")
    ed.add_argument("--ops", required=True,
                    help="JSON file (or '-' for stdin) with a list of operations, e.g. [{\"op\": \"delete\", \"item_id\": \"...\"}]")
    ed.add_argument("--expected-revision", type=int, default=None,
                    help="The revision the item ids were read from; a newer timeline is refused")

    vs = sub.add_parser("versions", help="List saved versions of an editor project (undo points)")
    vs.add_argument("project_id")

    rv = sub.add_parser("restore", help="Restore an editor project to a saved version")
    rv.add_argument("project_id")
    rv.add_argument("version_id")

    rn = sub.add_parser("render", help="Render an edited project to a new MP4")
    rn.add_argument("project_id")
    rn.add_argument("--wait", action="store_true", help="Poll until the render finishes")
    rn.add_argument("--accept-quality-findings", action="store_true",
                    help="Agent-planned jobs: render although plan-review still lists plan quality findings")
    rn.add_argument("--skip-frame-review", action="store_true",
                    help="Agent-planned jobs: render without having viewed every scene (only when the user says so)")
    _add_review_args(rn)

    rs = sub.add_parser("render-status", help="Get an editor render (output_url when done)")
    rs.add_argument("render_id")

    s = sub.add_parser("status", help="Get job status")
    s.add_argument("job_id")

    j = sub.add_parser("jobs", help="List recent jobs")
    j.add_argument("--status", default="")
    j.add_argument("--limit", type=int, default=20)

    sub.add_parser("account", help="Plan and credits")

    c = sub.add_parser("cancel", help="Cancel a job that has not started (a queued or running video can't be cancelled)")
    c.add_argument("job_id")

    for job_parser in (g, sc, vo, pj):
        _add_review_args(job_parser)
    args = parser.parse_args(argv)
    if args.cmd == "auth":
        _run_auth(args.auth_cmd)
        return
    if args.cmd == "use":
        _run_use(args.target)
        return
    client = CliptudeClient(client_kind="cli")
    try:
        if args.cmd == "pick":
            payload = _run_pick(client, args)
            _print(payload)
            if payload.get("status") != "done":
                raise SystemExit(1)
        elif args.cmd == "templates":
            _print(client.list_templates(limit=args.limit, offset=args.offset))
        elif args.cmd == "template-create":
            settings = {}
            if args.settings_file:
                settings = json.loads(Path(args.settings_file).read_text(encoding="utf-8"))
                if not isinstance(settings, dict):
                    raise CliptudeApiError("--settings-file must contain a JSON object.")
            settings.update(_parse_set_args(args.set))
            thumbnail = _thumbnail_from_args(args)
            if thumbnail:
                settings["thumbnail"] = thumbnail
            new_kit = _new_brand_kit_from_args(args)
            _print(client.create_template(name=args.name, format=args.format, settings=settings,
                                          brand_kit_id=args.brand_kit, show_logo=args.show_logo, look=args.look or None,
                                          captions=args.captions,
                                          **({"brand_kit": new_kit} if new_kit is not None else {})))
        elif args.cmd == "template-update":
            new_kit = _new_brand_kit_from_args(args)
            if (args.name is None and args.look is None and args.brand_kit is None and args.show_logo is None
                    and args.captions is None and new_kit is None):
                raise CliptudeApiError("Give --name, --look, --brand-kit, --new-brand-kit, --show-logo / --no-show-logo "
                                       "or --captions / --no-captions.")
            _print(client.update_template(_resolve_template_id(client, args.template), name=args.name,
                                          brand_kit_id=args.brand_kit, show_logo=args.show_logo, look=args.look,
                                          captions=args.captions,
                                          **({"brand_kit": new_kit} if new_kit is not None else {})))
        elif args.cmd == "looks":
            _print(client.list_looks(topic=args.topic or None, format=args.format or None))
        elif args.cmd == "docs":
            _run_docs(client, args)
        elif args.cmd == "look-template":
            _print(client.save_look_as_template(args.slug, name=args.name or None, format=args.format or None))
        elif args.cmd == "brand-kits":
            _print(client.list_brand_kits())
        elif args.cmd == "brand-kit":
            _run_brand_kit(client, args)
        elif args.cmd == "media":
            _run_media(client, args)
        elif args.cmd == "voice-design":
            _run_voice_design(client, args)
        elif args.cmd == "template-options":
            _print(client.template_options(args.format))
        elif args.cmd == "voices":
            _print(client.list_voices(provider=args.provider, language=args.language or None))
        elif args.cmd == "vector-characters":
            _print(client.list_vector_characters())
        elif args.cmd == "vector-character-upload":
            if bool(args.file) == bool(args.url):
                raise CliptudeApiError("Give --file or --url (one of them).")
            _print(client.upload_vector_character(
                image_path=args.file or None, image_url=args.url or None, name=args.name,
            ))
        elif args.cmd == "vector-character-delete":
            _print(client.delete_vector_character(args.character_id))
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
                render_only=args.render_only or None,
                vector_reveal_mode=args.vector_motion or None,
                captions=args.captions,
                look=args.look,
                brand_kit_id=args.brand_kit,
                **_sponsor_kwargs(args),
            ))
        elif args.cmd == "generate":
            script = args.script
            if args.script_file:
                script = Path(args.script_file).read_text(encoding="utf-8")
            voiceover = (args.voiceover or "").strip()
            voiceover_url = (args.voiceover_url or "").strip()
            talking_head = (args.talking_head or "").strip()
            if talking_head and (voiceover or voiceover_url):
                raise CliptudeApiError("Use --talking-head or --voiceover, not both.")
            # --prompt may come with any narration as direction for the visuals; two narrations may not.
            if talking_head and script:
                raise CliptudeApiError(
                    "Use --talking-head or --script, not both. "
                    "An uploaded talking head is transcribed and used as the narration; --prompt is fine as direction."
                )
            if (voiceover or voiceover_url) and script:
                raise CliptudeApiError(
                    "Use --voiceover or --script, not both. "
                    "An uploaded file is transcribed and used as the narration; --prompt is fine as direction."
                )
            if not talking_head and not voiceover and not voiceover_url and not (args.prompt or script):
                raise CliptudeApiError(
                    "Provide --prompt, --script / --script-file, --voiceover, --voiceover-url, or --talking-head."
                )
            template_id = _resolve_template_id(client, args.template)
            _print(_create_reviewed(client, args,
                template_id=template_id,
                title=args.title,
                prompt=args.prompt or None,
                script=script or None,
                duration=(args.duration or None),
                voiceover_path=voiceover or None,
                voiceover_url=voiceover_url or None,
                talking_head_path=talking_head or None,
                avatar_intro_only=args.avatar_intro_only,
                avatar_ai_footage=args.avatar_ai_footage,
                avatar_profile_id=args.avatar,
                avatar_engine=args.avatar_engine or None,
                disable_animations=args.disable_animations,
                disable_overlays=args.disable_overlays,
                disable_effects=args.disable_effects,
                disable_transitions=args.disable_transitions,
                disable_sound_effects=args.disable_sound_effects,
                excluded_animation_types=args.excluded_animation_types,
                excluded_transition_kinds=args.excluded_transition_kinds,
                thumbnail=_thumbnail_from_args(args),
                vector_character_id=args.vector_character or None,
                vector_reveal_mode=args.vector_motion or None,
                vector_art_style=args.vector_art_style,
                vector_image_model=args.vector_image_model or None,
                pronunciations=_pronunciations_from_args(args),
                captions=args.captions,
                look=args.look,
                brand_kit_id=args.brand_kit,
                **_sponsor_kwargs(args),
            ))
        elif args.cmd == "script":
            if not (args.prompt or "").strip():
                raise CliptudeApiError("Provide --prompt.")
            template_id = _resolve_template_id(client, args.template)
            _print(_create_reviewed(client, args,
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
            _print(_create_reviewed(client, args,
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
            voiceover_url = (args.voiceover_url or "").strip()
            if not (args.prompt or "").strip() and not (script or "").strip() and not voiceover and not voiceover_url:
                raise CliptudeApiError("Provide --prompt, --script / --script-file, --voiceover or --voiceover-url.")
            template_id = _resolve_template_id(client, args.template)
            _print(_create_reviewed(client, args,
                template_id=template_id,
                title=args.title,
                prompt=args.prompt or None,
                script=script or None,
                voiceover_path=voiceover or None,
                voiceover_url=voiceover_url or None,
                duration=(args.duration or None),
                output="plan",
                avatar_profile_id=args.avatar,
                avatar_engine=args.avatar_engine or None,
                render_only=args.render_only or None,
                thumbnail=_thumbnail_from_args(args),
                vector_character_id=args.vector_character or None,
                vector_reveal_mode=args.vector_motion or None,
                vector_art_style=args.vector_art_style,
                vector_image_model=args.vector_image_model or None,
                pronunciations=_pronunciations_from_args(args),
                captions=args.captions,
                look=args.look,
                brand_kit_id=args.brand_kit,
                **_sponsor_kwargs(args),
            ))
        elif args.cmd == "narration":
            _print(client.get_narration(args.job_id))
        elif args.cmd == "catalog":
            types = [t.strip() for t in (args.types or "").split(",") if t.strip()]
            _print(client.get_kind_catalog(
                format=args.format or None, theme=args.theme or None, compact=args.compact, types=types or None,
                premium=args.premium, look=args.look or None,
            ))
        elif args.cmd in ("plan-check", "plan-lock"):
            plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
            _print(client.put_plan(args.job_id, plan, dry_run=args.cmd == "plan-check"))
        elif args.cmd == "plan-get":
            _print(client.get_plan(args.job_id))
        elif args.cmd == "plan-review":
            _print(client.get_plan_review(args.job_id))
        elif args.cmd == "plan-candidate-sheets":
            import base64 as _b64

            beats = [b.strip() for b in (args.beats or "").split(",") if b.strip()] or None
            data = client.view_candidates(args.job_id, beats=beats, start_s=args.start, end_s=args.end)
            out_dir = Path(args.out).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)
            saved = []
            for sheet in data.pop("sheets", None) or []:
                dest = out_dir / str(sheet.get("name") or f"candidates_{len(saved) + 1:02d}.jpg")
                dest.write_bytes(_b64.b64decode(sheet["jpeg_base64"]))
                saved.append(str(dest))
            data["saved_sheets"] = saved
            _print(data)
        elif args.cmd == "plan-candidates":
            beats = [b.strip() for b in (args.beats or "").split(",") if b.strip()] or None
            _print(client.get_plan_candidates(args.job_id, beats=beats, start_s=args.start, end_s=args.end))
        elif args.cmd == "plan-report":
            _print(client.get_plan_report(args.job_id))
        elif args.cmd == "upload-media":
            _print(client.upload_plan_media(args.job_id, args.path, split_shots=args.split_shots, **_screenshot_kw(args)))
        elif args.cmd == "download-voiceover":
            dest = Path(args.out)
            client.download_voiceover_file(args.job_id, dest)
            _print({"ok": True, "path": str(dest.resolve())})
        elif args.cmd == "continue":
            thumbnail = _thumbnail_from_args(args)
            _print(_spend_reviewed(
                args, lambda token: client.continue_job(args.job_id, thumbnail=thumbnail, review_token=token),
                "Build this video?",
            ))
        elif args.cmd == "product":
            if args.product_cmd == "fetch":
                _print(client.fetch_product_context(args.url_or_query, store_domain=args.store or None))
            elif args.product_cmd == "script":
                angle = int(args.angle) if str(args.angle).isdigit() else args.angle
                script = Path(args.script_file).read_text(encoding="utf-8") if args.script_file else None
                _print(_reviewed(args, lambda token, acks: client.draft_product_script(
                    args.product_id, angle=angle, duration=args.duration, template_id=args.template or None,
                    title=args.title or None, discount_code=args.discount_code or None, cta_url=args.cta_url or None,
                    reviews=[_review_arg(r) for r in args.review] or None, script=script, plan=args.plan,
                    review_token=token, acknowledge=acks,
                ), question="Start this product video?"))
            elif args.product_cmd == "render":
                thumbnail = _thumbnail_from_args(args)
                _print(_spend_reviewed(args, lambda token: client.render_product_video(
                    args.job_id, aspect_ratio=args.aspect_ratio, thumbnail=thumbnail, review_token=token,
                ), "Build this product video?"))
            else:
                _print(client.get_product_video_status(args.job_id))
        elif args.cmd == "editor":
            _print(client.open_editor_project(args.job_id))
        elif args.cmd == "agent":
            prompt = args.prompt
            if args.prompt_file:
                prompt = Path(args.prompt_file).read_text(encoding="utf-8")
            if not (prompt or "").strip():
                raise CliptudeApiError("Provide --prompt or --prompt-file.")
            items = [i.strip() for i in (args.items or "").split(",") if i.strip()]
            payload = _spend_reviewed(args, lambda token: client.run_agent(
                args.project_id, prompt=prompt, tier=args.tier, referenced_item_ids=items, chat_id=args.chat or None,
                review_token=token,
            ), "Run the agent?")
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
        elif args.cmd == "edit":
            raw = sys.stdin.read() if args.ops == "-" else Path(args.ops).read_text(encoding="utf-8")
            try:
                ops = json.loads(raw)
            except ValueError as exc:
                raise CliptudeApiError(f"--ops is not JSON: {exc}")
            if isinstance(ops, dict):
                ops = ops.get("operations") or []
            _print(client.edit_timeline(args.project_id, ops, expected_revision=args.expected_revision))
        elif args.cmd == "versions":
            _print(client.list_editor_versions(args.project_id))
        elif args.cmd == "restore":
            _print(client.restore_editor_version(args.project_id, args.version_id))
        elif args.cmd == "render":
            payload = _spend_reviewed(args, lambda token: client.render_project(
                args.project_id, accept_quality_findings=args.accept_quality_findings, review_token=token,
                skip_frame_review=args.skip_frame_review,
            ), "Render this MP4?")
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

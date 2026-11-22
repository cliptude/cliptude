"""MCP and CLI follow this week's product rules: five live formats, Avatar IV only, captions off by default, the
motion-graphics Look exception to plan jobs, designed voice settings, the rate in reviews, and the
deprecated visual_direction merged into the prompt on every path."""

import asyncio
import io
import re
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from cliptude_mcp import cli
from cliptude_mcp.client import CliptudeApiError, CliptudeClient

WIDGETS = Path(__file__).resolve().parents[1] / "cliptude_mcp" / "widgets"
RETIRED = ("whiteboard", "sketch", "stickman", "monkey_explained", "low_poly_3d", "vox_animation", "infographic",
           "mannequin_3d", "avatar_explainer", "faceless_explainer")


def _tools():
    from cliptude_mcp.server import build_server

    server = build_server()
    return server, {t.name: t for t in asyncio.run(server.list_tools())}


class McpSurfaceTests(TestCase):
    def test_no_retired_format_is_offered(self):
        server, tools = _tools()
        texts = [server.instructions or ""]
        for tool in tools.values():
            texts.append(tool.description or "")
            texts.append(str(tool.input_schema))
        blob = "\n".join(texts)
        for fmt in RETIRED:
            self.assertIsNone(re.search(rf"\b{fmt}\b", blob), fmt)
        for fmt in ("faceless_documentary", "faceless_top_10", "avatar_video", "talking_head", "vector_explainer"):
            self.assertIn(fmt, server.instructions)

    def test_widget_live_formats_and_choice_fields(self):
        from cliptude_mcp.apps import CHOICE_FIELDS

        html = (WIDGETS / "templates.html").read_text(encoding="utf-8")
        live = re.search(r"var LIVE = \{([^}]*)\}", html).group(1)
        self.assertNotIn("faceless_explainer", live)
        self.assertIn("vector_explainer", live)
        self.assertNotIn("faceless_explainer_style", CHOICE_FIELDS)

    def test_avatar_engine_is_iv_only(self):
        _server, tools = _tools()
        for name in ("create_job", "create_plan_job", "estimate_credits"):
            text = str(tools[name].input_schema["properties"]["avatar_engine"])
            self.assertIn('"iv"', text)
            self.assertNotIn('"v"', text)

    def test_instructions_state_captions_and_the_motion_graphics_exception(self):
        server, tools = _tools()
        self.assertIn("CAPTIONS are off by default", server.instructions)
        self.assertIn("set_captions", tools["edit_timeline"].input_schema["properties"]["operations"]["description"])
        self.assertIn("motion_graphics_look_plan", server.instructions)
        self.assertIn("motion_graphics_look_plan", tools["create_plan_job"].description)
        # Nov 18 2026 (count dropped Nov 22): only the offered Looks (videos/look_offer.py) are named; a hidden one is refused for a
        # new template or job (look_hidden) and never offered, motion-graphics and Vector Explainer Looks included.
        self.assertIn("look_hidden", server.instructions)
        looks = tools["list_looks"].description
        self.assertIn("The offered Looks", looks)
        self.assertNotIn("twelve", looks)
        self.assertIn("look_hidden", looks)
        for hidden in ("data-desk", "neon-noir", "editorial-explainer", "cosy-cosmos", "what-if-tale",
                       "Field Dossier", "Cold Case Board", "twenty"):
            self.assertNotIn(hidden, looks)
            self.assertNotIn(hidden, server.instructions)

    def test_voice_design_tools_exist(self):
        _server, tools = _tools()
        for name in ("voice_design_options", "update_designed_voice_settings", "try_designed_voice",
                     "remix_designed_voice", "save_remixed_voice", "delete_designed_voice"):
            self.assertIn(name, tools)
        for key in ("model_id", "guidance_scale"):
            self.assertIn(key, tools["design_voice_previews"].input_schema["properties"])
        for key in ("stability", "similarity_boost", "style", "use_speaker_boost", "speed"):
            self.assertIn(key, tools["save_designed_voice"].input_schema["properties"])

    def test_review_card_shows_rate_and_look(self):
        js = (WIDGETS / "views.js").read_text(encoding="utf-8")
        self.assertIn("Credits per minute", js)
        self.assertNotIn("painted_look_credits_per_minute", js)  # painted Looks retired Nov 17 2026
        self.assertIn('s.label === "Look"', js)
        self.assertIn("creditsBox(rev.credits, rev)", js)


class VoiceDesignClientTests(TestCase):
    def setUp(self):
        self.client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")

    def test_settings_try_remix_and_delete_paths(self):
        with patch.object(self.client, "request", return_value={}) as req:
            self.client.update_designed_voice_settings(7, {"stability": 0.4, "speed": None})
            self.assertEqual(req.call_args.args[:2], ("POST", "/api/v1/voice-designs/elevenlabs/7/settings"))
            self.assertEqual(req.call_args.kwargs["json_body"], {"stability": 0.4})
            self.client.try_designed_voice(7, text="Hi", voice_settings={"style": 0.2})
            self.assertEqual(req.call_args.kwargs["json_body"], {"text": "Hi", "style": 0.2})
            self.client.remix_designed_voice(7, "warmer", options={"guidance_scale": 3})
            self.assertEqual(req.call_args.args[1], "/api/v1/voice-designs/elevenlabs/7/remix")
            self.client.delete_designed_voice("elevenlabs", 7)
            self.assertEqual(req.call_args.args[:2], ("DELETE", "/api/v1/voice-designs/elevenlabs/7"))
        with self.assertRaises(CliptudeApiError):
            self.client.update_designed_voice_settings("7/../x", {"stability": 1})
        with self.assertRaises(CliptudeApiError):
            self.client.delete_designed_voice("cartesia", 7)


class VisualDirectionMergeTests(TestCase):
    def test_json_path_merges_into_prompt(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "request", return_value={"id": "abc"}) as req:
            client.create_job(template_id="tmpl", title="T", script="Words.", prompt="Calm.",
                              visual_direction="Open on a map.")
        body = req.call_args.kwargs["json_body"]
        self.assertEqual(body["prompt"], "Calm.\n\nOpen on a map.")
        self.assertNotIn("visual_direction", body)

    def test_talking_head_path_keeps_the_direction(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"\x00" * 64)
            path = Path(tmp.name)
        try:
            with patch.object(client, "request", return_value={"fallback": True}), patch("httpx.post") as post:
                resp = MagicMock(status_code=201)
                resp.json.return_value = {"id": "abc"}
                post.return_value = resp
                client.create_job(template_id="tmpl", title="T", talking_head_path=path,
                                  visual_direction="Open on a map.")
            self.assertEqual(post.call_args.kwargs["data"]["prompt"], "Open on a map.")
        finally:
            path.unlink(missing_ok=True)


class CliTests(TestCase):
    def test_generate_accepts_a_voiceover_url_without_prompt_or_script(self):
        with patch.object(cli.CliptudeClient, "list_templates", return_value={"results": [{"id": "t1", "name": "Doc"}]}), \
                patch.object(cli.CliptudeClient, "create_job", return_value={"id": "j1"}) as create, \
                patch.object(cli, "_print"):
            cli.main(["generate", "--template", "Doc", "--title", "T", "--voiceover-url", "https://x.test/a.mp3"])
        self.assertEqual(create.call_args.kwargs["voiceover_url"], "https://x.test/a.mp3")

    def test_generate_still_needs_a_narration_or_prompt(self):
        with patch.object(cli, "_print"), self.assertRaises(SystemExit):
            cli.main(["generate", "--template", "Doc", "--title", "T"])

    def test_avatar_engine_v_is_not_a_choice(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["generate", "--template", "Doc", "--title", "T", "--prompt", "x", "--avatar-engine", "v"])

    def test_product_render_is_16_9_only(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["product", "render", "j1", "--aspect-ratio", "9:16"])

    def test_template_create_and_update_send_branding(self):
        with patch.object(cli.CliptudeClient, "create_template", return_value={}) as create, \
                patch.object(cli, "_print"):
            cli.main(["template-create", "--name", "N", "--format", "faceless_documentary", "--set", "voice_id=v1",
                      "--look", "cold-case-board", "--brand-kit", "3", "--show-logo"])
        self.assertEqual(create.call_args.kwargs["look"], "cold-case-board")
        self.assertEqual(create.call_args.kwargs["brand_kit_id"], 3)
        self.assertTrue(create.call_args.kwargs["show_logo"])
        with patch.object(cli.CliptudeClient, "list_templates", return_value={"results": [{"id": "t1", "name": "N"}]}), \
                patch.object(cli.CliptudeClient, "update_template", return_value={}) as update, \
                patch.object(cli, "_print"):
            cli.main(["template-update", "N", "--look", "", "--no-show-logo", "--captions"])
        update.assert_called_once_with("t1", name=None, brand_kit_id=None, show_logo=False, look="", captions=True)

    def test_looks_brand_kits_and_look_template(self):
        with patch.object(cli.CliptudeClient, "list_looks", return_value={}) as looks, \
                patch.object(cli.CliptudeClient, "list_brand_kits", return_value={}) as kits, \
                patch.object(cli.CliptudeClient, "save_look_as_template", return_value={}) as save, \
                patch.object(cli, "_print"):
            cli.main(["looks"])
            cli.main(["brand-kits"])
            cli.main(["look-template", "empire-story", "--format", "faceless_top_10"])
        looks.assert_called_once()
        kits.assert_called_once()
        save.assert_called_once_with("empire-story", name=None, format="faceless_top_10")

    def test_looks_topic(self):
        with patch.object(cli.CliptudeClient, "list_looks", return_value={}) as looks, patch.object(cli, "_print"):
            cli.main(["looks", "--topic", "Viking raids", "--format", "faceless_documentary"])
        looks.assert_called_once_with(topic="Viking raids", format="faceless_documentary")
        client = cli.CliptudeClient.__new__(cli.CliptudeClient)
        with patch.object(cli.CliptudeClient, "request", return_value={}) as req:
            client.list_looks(topic="  Viking raids ", format="faceless_documentary")
            client.list_looks()
        self.assertEqual(req.call_args_list[0].kwargs["params"], {"topic": "Viking raids", "format": "faceless_documentary"})
        self.assertIsNone(req.call_args_list[1].kwargs["params"])

    def test_voice_design_settings(self):
        with patch.object(cli.CliptudeClient, "update_designed_voice_settings", return_value={}) as update, \
                patch.object(cli, "_print"):
            cli.main(["voice-design", "settings", "7", "--speed", "1.05", "--no-speaker-boost"])
        update.assert_called_once_with("7", {"speed": 1.05, "use_speaker_boost": False})

    def test_review_shows_the_rate(self):
        review = {"settings": [{"label": "Look", "value": "Empire Story (empire-story)"}],
                  "credits": {"credits_required": 600, "credits_per_minute": 40}}
        self.assertEqual(cli._rate_lines(review), ["Credits per minute: 40"])

    def test_help_has_no_three_minute_durations(self):
        source = Path(cli.__file__).read_text(encoding="utf-8")
        self.assertNotIn("3-4", source)


class CaptionsParameterTests(TestCase):
    def test_mcp_tools_take_captions_off_by_default(self):
        server, tools = _tools()
        for name in ("create_job", "create_plan_job", "estimate_credits", "create_template", "update_template"):
            prop = tools[name].input_schema["properties"]["captions"]
            self.assertIsNone(prop.get("default"), name)  # omitted = the template's (off by default)
            self.assertIn("default", prop.get("description", ""), name)
        self.assertIn("captions=true", server.instructions)

    def test_client_sends_captions_on_every_path(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "request", return_value={"id": "abc"}) as req:
            client.create_job(template_id="tmpl", title="T", script="Words.", captions=True)
            self.assertIs(req.call_args.kwargs["json_body"]["captions"], True)
            client.create_job(template_id="tmpl", title="T", script="Words.")
            self.assertNotIn("captions", req.call_args.kwargs["json_body"])
            client.estimate_credits(template_id="tmpl", script="Words.", captions=False)
            self.assertIs(req.call_args.kwargs["json_body"]["captions"], False)
            client.create_template(name="N", format="faceless_documentary", settings={"voice_id": "v"}, captions=True)
            self.assertIs(req.call_args.kwargs["json_body"]["settings"]["captions_enabled"], True)
            client.update_template("11111111-1111-1111-1111-111111111111", captions=False)
            self.assertIs(req.call_args.kwargs["json_body"]["captions"], False)
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"\x00" * 64)
            path = Path(tmp.name)
        try:
            with patch.object(client, "request", return_value={"fallback": True}), patch("httpx.post") as post:
                resp = MagicMock(status_code=201)
                resp.json.return_value = {"id": "abc"}
                post.return_value = resp
                client.create_job(template_id="tmpl", title="T", talking_head_path=path, captions=True)
            self.assertEqual(post.call_args.kwargs["data"]["captions"], "true")
        finally:
            path.unlink(missing_ok=True)

    def test_cli_generate_and_plan_job_flags(self):
        with patch.object(cli.CliptudeClient, "list_templates", return_value={"results": [{"id": "t1", "name": "Doc"}]}), \
                patch.object(cli.CliptudeClient, "create_job", return_value={"id": "j1"}) as create, \
                patch.object(cli, "_print"):
            cli.main(["generate", "--template", "Doc", "--title", "T", "--prompt", "p", "--captions"])
            self.assertIs(create.call_args.kwargs["captions"], True)
            cli.main(["plan-job", "--template", "Doc", "--title", "T", "--prompt", "p", "--no-captions"])
            self.assertIs(create.call_args.kwargs["captions"], False)
            cli.main(["generate", "--template", "Doc", "--title", "T", "--prompt", "p"])
            self.assertIsNone(create.call_args.kwargs["captions"])
            self.assertIsNone(create.call_args.kwargs["look"])
            # A job's own Look on the CLI too (the API refuses a hidden one with code look_hidden).
            cli.main(["generate", "--template", "Doc", "--title", "T", "--prompt", "p", "--look", "old-frontier"])
            self.assertEqual(create.call_args.kwargs["look"], "old-frontier")
            cli.main(["plan-job", "--template", "Doc", "--title", "T", "--prompt", "p", "--look", "none"])
            self.assertEqual(create.call_args.kwargs["look"], "none")


class DocsToolsTests(TestCase):
    """search_docs / read_doc / list_docs (MCP), `cliptude docs` (CLI) and the public /api/v1/docs calls."""

    def test_tools_exist_read_only_and_tell_the_agent_to_answer_from_the_docs(self):
        server, tools = _tools()
        for name in ("search_docs", "read_doc", "list_docs"):
            self.assertIn(name, tools)
            self.assertTrue(tools[name].annotations.read_only_hint)
        search = tools["search_docs"].description
        for text in ("before answering", "credits", "refunds", "troubleshooting", "not from memory", "never instructions"):
            self.assertIn(text, search)
        self.assertEqual(set(tools["read_doc"].input_schema["properties"]), {"slug", "section", "offset", "max_chars"})
        self.assertEqual(tools["read_doc"].input_schema["properties"]["max_chars"]["maximum"], 20000)
        self.assertIn("search_docs", server.instructions[:600])  # early, so a truncated copy still carries it

    def test_tools_call_the_client(self):
        _server, tools = _tools()
        with patch.object(CliptudeClient, "search_docs", return_value={"results": []}) as search, \
                patch.object(CliptudeClient, "read_doc", return_value={"content": "# Credits"}) as read:
            asyncio.run(_server.call_tool("search_docs", {"query": "credits per minute", "limit": 3}))
            asyncio.run(_server.call_tool("read_doc", {"slug": "credits", "section": "Plan credits"}))
        search.assert_called_once_with("credits per minute", limit=3)
        read.assert_called_once_with("credits", section="Plan credits", offset=0, max_chars=20000)

    def test_client_paths_and_public_without_a_session(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "request", return_value={}) as req:
            client.search_docs("  how many   credits ", limit=50)
            self.assertEqual(req.call_args.args[:2], ("GET", "/api/v1/docs/search"))
            self.assertEqual(req.call_args.kwargs["params"], {"q": "how many credits", "limit": 20})
            client.read_doc("https://cliptude.com/docs/refund-policy/#refunds", section="Credits & refunds")
            self.assertEqual(req.call_args.args[1], "/api/v1/docs/refund-policy")
            self.assertEqual(req.call_args.kwargs["params"]["section"], "Credits & refunds")
            client.list_docs()
            self.assertEqual(req.call_args.args[1], "/api/v1/docs")
        for bad in ("../accounts", "credits/../../x", "a b"):
            with self.assertRaises(CliptudeApiError):
                client.read_doc(bad)
        anonymous = CliptudeClient(base_url="https://cliptude.com", api_key=None)
        anonymous.api_key = None
        with patch("httpx.get") as get:
            get.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"results": []}))
            self.assertEqual(anonymous.list_docs(), {"results": []})
            self.assertNotIn("Authorization", get.call_args.kwargs["headers"])
            get.return_value = MagicMock(status_code=404, json=MagicMock(return_value={"error": "No docs page", "code": "doc_not_found"}))
            with self.assertRaises(CliptudeApiError) as caught:
                anonymous.read_doc("nope")
            self.assertEqual(caught.exception.status, 404)

    def test_cli_docs_commands(self):
        out = io.StringIO()
        from contextlib import redirect_stdout

        with patch.object(cli.CliptudeClient, "search_docs", return_value={"results": [
                {"slug": "credits", "title": "Credits System", "section": "Faceless documentary by voice",
                 "url": "https://cliptude.com/docs/credits/", "excerpt": "22 credits per minute"}]}) as search, \
                patch.object(cli.CliptudeClient, "read_doc", return_value={
                    "url": "https://cliptude.com/docs/credits/", "content": "# Understanding Credits", "next_offset": None}) as read, \
                patch.object(cli.CliptudeClient, "list_docs", return_value={"results": [
                    {"slug": "credits", "title": "Credits System", "category": "Getting Started"}]}) as listing, \
                redirect_stdout(out):
            cli.main(["docs", "search", "credits", "per", "minute", "--limit", "3"])
            cli.main(["docs", "read", "credits", "--section", "Plan credits"])
            cli.main(["docs", "list", "--category", "Guides"])
        search.assert_called_once_with("credits per minute", limit=3)
        read.assert_called_once_with("credits", section="Plan credits", offset=0, max_chars=20000)
        listing.assert_called_once_with(category="Guides")
        text = out.getvalue()
        for snippet in ("## Credits System > Faceless documentary by voice", "cliptude docs read credits",
                        "Source: https://cliptude.com/docs/credits/", "# Understanding Credits", "Credits System  [Getting Started]"):
            self.assertIn(snippet, text)


# ---- Brand kits: every /api/v1 brand-kit capability has an MCP tool and a CLI command ----

BRAND_KIT_SURFACE = {
    # API: (method, path) -> (MCP tool, CLI argv, client method)
    ("GET", "/api/v1/brand-kits"): ("list_brand_kits", ["brand-kit", "list"], "list_brand_kits"),
    ("GET", "/api/v1/brand-kits/options"): ("brand_kit_options", ["brand-kit", "options"], "brand_kit_options"),
    ("GET", "/api/v1/brand-kits/{id}"): ("get_brand_kit", ["brand-kit", "get", "3"], "get_brand_kit"),
    ("POST", "/api/v1/brand-kits"): ("create_brand_kit", ["brand-kit", "create", "--name", "Northwind Notes"],
                                     "create_brand_kit"),
    ("PATCH", "/api/v1/brand-kits/{id}"): ("update_brand_kit", ["brand-kit", "update", "3", "--name", "N"],
                                           "update_brand_kit"),
    ("POST", "/api/v1/brand-kits/{id}/default"): ("set_default_brand_kit", ["brand-kit", "set-default", "3"],
                                                  "set_default_brand_kit"),
    ("DELETE", "/api/v1/brand-kits/{id}"): ("delete_brand_kit", ["brand-kit", "delete", "3", "--yes"],
                                            "delete_brand_kit"),
    ("POST", "/api/v1/uploads/brand/init"): ("start_brand_upload", ["brand-kit", "update", "3", "--logo", "LOGO"],
                                             "upload_brand_logo"),
    ("POST", "/api/v1/uploads/brand/complete"): ("finish_brand_upload", ["brand-kit", "update", "3", "--logo", "LOGO"],
                                                 "upload_brand_logo"),
}


def _png_file() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    tmp.close()
    return Path(tmp.name)


def _call(server, name, args):
    import json as _json

    result = asyncio.run(server.call_tool(name, args))
    if getattr(result, "structured_content", None):
        return result.structured_content
    return _json.loads(result.content[0].text)


class BrandKitSurfaceTests(TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"CLIPTUDE_BASE_URL": "https://api.test.invalid",
                                             "CLIPTUDE_API_KEY": "clp_test", "CLIPTUDE_BROWSER_PICKER": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.logo = _png_file()
        self.addCleanup(self.logo.unlink, missing_ok=True)

    def test_every_brand_kit_api_capability_has_a_tool_and_a_cli_command(self):
        server, tools = _tools()
        for (method, path), (tool, argv, client_method) in BRAND_KIT_SURFACE.items():
            self.assertIn(tool, tools, path)
            argv = [str(self.logo) if a == "LOGO" else a for a in argv]
            # The CLI's --logo PATH hands the file to update_brand_kit, which runs the upload flow
            # (BrandKitClientTests.test_a_local_logo_is_uploaded_then_attached).
            patched = "update_brand_kit" if client_method == "upload_brand_logo" else client_method
            with patch.object(cli.CliptudeClient, patched, return_value={}) as called, patch.object(cli, "_print"):
                cli.main(argv)
            called.assert_called_once()
            if client_method == "upload_brand_logo":
                self.assertEqual(called.call_args.kwargs["logo_path"], str(self.logo))
        annotations = tools["delete_brand_kit"].annotations
        self.assertTrue(annotations.destructive_hint)
        self.assertIn("Only when the user asks", tools["delete_brand_kit"].description)
        self.assertIn("BRAND KITS", server.instructions)

    def test_tool_fields_and_template_and_job_parameters(self):
        _server, tools = _tools()
        props = tools["update_brand_kit"].input_schema["properties"]
        for key in ("name", "channel_name", "channel_handle", "colors", "fonts", "logo_position", "logo_url",
                    "logo_media_id", "logo_path", "remove_logo", "is_default"):
            self.assertIn(key, props)
        self.assertIn("subscribe card", props["channel_handle"]["description"])
        self.assertIn("#RRGGBB", props["colors"]["description"])
        self.assertIn("brand_kit_options", props["fonts"]["description"])
        for name in ("create_template", "update_template"):
            self.assertIn("brand_kit", tools[name].input_schema["properties"])
            self.assertIn("brand_kit_id", tools[name].input_schema["properties"])
        for name in ("create_job", "create_plan_job", "estimate_credits"):
            self.assertIn("brand_kit_id", tools[name].input_schema["properties"], name)
        blob = "\n".join((t.description or "") + str(t.input_schema) for t in tools.values())
        self.assertIn("Northwind Notes", blob)

    def test_tools_call_the_client(self):
        server, _tools_ = _tools()
        with patch.object(CliptudeClient, "create_brand_kit", return_value={"id": 3}) as create:
            _call(server, "create_brand_kit", {"name": "Northwind Notes", "channel_handle": "@northwindnotes",
                                               "colors": ["#1F6FEB"], "logo_url": "https://x.test/l.png"})
        kwargs = create.call_args.kwargs
        self.assertEqual((kwargs["name"], kwargs["channel_handle"], kwargs["logo_url"], kwargs["logo_path"]),
                         ("Northwind Notes", "@northwindnotes", "https://x.test/l.png", None))
        with patch.object(CliptudeClient, "update_brand_kit", return_value={"id": 3}) as update:
            _call(server, "update_brand_kit", {"kit_id": 3, "remove_logo": True})
        self.assertIs(update.call_args.kwargs["remove_logo"], True)
        with patch.object(CliptudeClient, "finish_brand_upload", return_value={}) as finish:
            _call(server, "finish_brand_upload", {"key": "k", "upload_id": "u", "kit_id": 3})
        finish.assert_called_once_with(key="k", upload_id="u", kit_id=3)
        with patch.object(CliptudeClient, "create_template", return_value={}) as tmpl:
            _call(server, "create_template", {"name": "W", "format": "faceless_documentary", "settings": {},
                                              "brand_kit": {"name": "Northwind Notes"}, "show_logo": True})
        self.assertEqual(tmpl.call_args.kwargs["brand_kit"], {"name": "Northwind Notes"})
        with patch.object(CliptudeClient, "create_job", return_value={}) as job:
            _call(server, "create_job", {"template_id": "11111111-1111-1111-1111-111111111111", "title": "T",
                                         "script": "Words.", "brand_kit_id": 3})
        self.assertEqual(job.call_args.kwargs["brand_kit_id"], 3)

    def test_remote_server_refuses_local_logo_paths(self):
        import warnings

        from cliptude_mcp.server import build_server

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            remote = build_server(with_http_auth=True)
        with patch.object(CliptudeClient, "create_brand_kit") as create:
            out = _call(remote, "create_brand_kit", {"name": "N", "logo_path": "/etc/passwd"})
            out2 = _call(remote, "update_template", {"template_id": "11111111-1111-1111-1111-111111111111",
                                                     "brand_kit": {"name": "N", "logo_path": "/etc/passwd"}})
        create.assert_not_called()
        self.assertIn("error", out)
        self.assertIn("error", out2)


class BrandKitClientTests(TestCase):
    def setUp(self):
        self.client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        self.logo = _png_file()
        self.addCleanup(self.logo.unlink, missing_ok=True)

    def test_paths_and_bodies(self):
        with patch.object(self.client, "request", return_value={}) as req:
            self.client.create_brand_kit(name="Northwind Notes", colors=["#1F6FEB"], channel_name=None)
            self.assertEqual(req.call_args.args, ("POST", "/api/v1/brand-kits"))
            self.assertEqual(req.call_args.kwargs["json_body"], {"name": "Northwind Notes", "colors": ["#1F6FEB"]})
            self.client.update_brand_kit(3, channel_handle="")
            self.assertEqual(req.call_args.args, ("PATCH", "/api/v1/brand-kits/3"))
            self.assertEqual(req.call_args.kwargs["json_body"], {"channel_handle": ""})
            self.client.set_default_brand_kit(3)
            self.assertEqual(req.call_args.args, ("POST", "/api/v1/brand-kits/3/default"))
            self.client.delete_brand_kit(3)
            self.assertEqual(req.call_args.args, ("DELETE", "/api/v1/brand-kits/3"))
            self.client.finish_brand_upload(key="k", upload_id="u", kit_id=3)
            self.assertEqual(req.call_args.kwargs["json_body"], {"key": "k", "upload_id": "u", "kit_id": 3})
        with self.assertRaises(CliptudeApiError):
            self.client.get_brand_kit("3/../account")
        with self.assertRaises(CliptudeApiError):
            self.client.update_brand_kit(3)

    def test_a_local_logo_is_uploaded_then_attached(self):
        calls = []

        def fake_request(method, path, **kw):
            calls.append((method, path, kw.get("json_body")))
            if path.endswith("/init"):
                return {"key": "uploads/brand/1/a_logo.png", "upload_id": "u1", "part_size": 1 << 20, "part_count": 1}
            if path.endswith("/sign-parts"):
                return {"urls": {"1": "https://r2.test/put"}}
            if path.endswith("/complete"):
                return {"media_id": "m-1"}
            return {"id": 3}

        with patch.object(self.client, "request", side_effect=fake_request), \
                patch("httpx.put", return_value=MagicMock(status_code=200)) as put:
            self.client.update_brand_kit(3, logo_path=str(self.logo), logo_position="top_left")
            put.assert_called_once()
            self.assertEqual(calls[-1], ("PATCH", "/api/v1/brand-kits/3",
                                         {"logo_position": "top_left", "logo_media_id": "m-1"}))
            # An inline kit on a template: its logo_path is uploaded first too.
            self.client.create_template(name="W", format="faceless_documentary", settings={},
                                        brand_kit={"name": "Northwind Notes", "logo_path": str(self.logo)})
            self.assertEqual(calls[-1][2]["brand_kit"], {"name": "Northwind Notes", "logo_media_id": "m-1"})
        with self.assertRaises(CliptudeApiError):
            self.client.create_template(name="W", format="x", settings={}, brand_kit={"name": "N"}, brand_kit_id=3)
        hosted = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        hosted.allow_local_files = False
        with self.assertRaises(CliptudeApiError):
            hosted.upload_brand_logo(self.logo)

    def test_job_brand_kit_on_every_path(self):
        with patch.object(self.client, "request", return_value={"id": "abc"}) as req:
            self.client.create_job(template_id="tmpl", title="T", script="Words.", brand_kit_id=3)
            self.assertEqual(req.call_args.kwargs["json_body"]["brand_kit_id"], 3)
            self.client.create_job(template_id="tmpl", title="T", script="Words.")
            self.assertNotIn("brand_kit_id", req.call_args.kwargs["json_body"])
            self.client.estimate_credits(template_id="tmpl", script="Words.", brand_kit_id=0)
            self.assertEqual(req.call_args.kwargs["json_body"]["brand_kit_id"], 0)
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"\x00" * 64)
            path = Path(tmp.name)
        try:
            with patch.object(self.client, "request", return_value={"fallback": True}), patch("httpx.post") as post:
                resp = MagicMock(status_code=201)
                resp.json.return_value = {"id": "abc"}
                post.return_value = resp
                self.client.create_job(template_id="tmpl", title="T", talking_head_path=path, brand_kit_id=3)
            self.assertEqual(post.call_args.kwargs["data"]["brand_kit_id"], "3")
        finally:
            path.unlink(missing_ok=True)


class BrandKitCliTests(TestCase):
    def test_create_flags(self):
        with patch.object(cli.CliptudeClient, "create_brand_kit", return_value={}) as create, \
                patch.object(cli, "_print"):
            cli.main(["brand-kit", "create", "--name", "Northwind Notes", "--channel-handle", "@northwindnotes",
                      "--color", "#1F6FEB", "--color", "#FFFFFF", "--font", "title=Oswald:700", "--font", "body=Inter",
                      "--logo", "https://x.test/logo.png", "--logo-position", "top_left", "--default"])
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["colors"], ["#1F6FEB", "#FFFFFF"])
        self.assertEqual(kwargs["fonts"], {"title": {"family": "Oswald", "weight": 700}, "body": {"family": "Inter"}})
        self.assertEqual((kwargs["logo_url"], kwargs["logo_path"], kwargs["is_default"]),
                         ("https://x.test/logo.png", None, True))

    def test_update_only_sends_what_was_given_and_delete_confirms(self):
        with patch.object(cli.CliptudeClient, "update_brand_kit", return_value={}) as update, \
                patch.object(cli, "_print"):
            cli.main(["brand-kit", "update", "3", "--remove-logo", "--channel-name", ""])
            update.assert_called_once_with(3, channel_name="", remove_logo=True)
            with self.assertRaises(SystemExit):
                cli.main(["brand-kit", "update", "3"])
        with patch.object(cli.CliptudeClient, "delete_brand_kit", return_value={}) as delete, \
                patch.object(cli, "_print"), patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            with self.assertRaises(SystemExit):
                cli.main(["brand-kit", "delete", "3"])
            delete.assert_not_called()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["brand-kit", "create", "--name", "N", "--logo-position", "middle"])

    def test_template_new_brand_kit_and_job_brand_kit(self):
        with patch.object(cli.CliptudeClient, "create_template", return_value={}) as create, \
                patch.object(cli, "_print"):
            cli.main(["template-create", "--name", "N", "--format", "faceless_documentary", "--set", "voice_id=v1",
                      "--new-brand-kit", '{"name": "Northwind Notes", "logo_url": "https://x.test/l.png"}',
                      "--show-logo"])
        self.assertEqual(create.call_args.kwargs["brand_kit"]["name"], "Northwind Notes")
        with patch.object(cli.CliptudeClient, "list_templates", return_value={"results": [{"id": "t1", "name": "N"}]}), \
                patch.object(cli.CliptudeClient, "update_template", return_value={}) as update, \
                patch.object(cli, "_print"):
            cli.main(["template-update", "N", "--new-brand-kit", '{"name": "Northwind Notes"}'])
        self.assertEqual(update.call_args.kwargs["brand_kit"], {"name": "Northwind Notes"})
        with patch.object(cli.CliptudeClient, "list_templates", return_value={"results": [{"id": "t1", "name": "Doc"}]}), \
                patch.object(cli.CliptudeClient, "create_job", return_value={"id": "j1"}) as job, \
                patch.object(cli.CliptudeClient, "estimate_credits", return_value={}) as est, \
                patch.object(cli, "_print"):
            cli.main(["generate", "--template", "Doc", "--title", "T", "--prompt", "p", "--brand-kit", "3"])
            self.assertEqual(job.call_args.kwargs["brand_kit_id"], 3)
            cli.main(["plan-job", "--template", "Doc", "--title", "T", "--prompt", "p", "--brand-kit", "0"])
            self.assertEqual(job.call_args.kwargs["brand_kit_id"], 0)
            cli.main(["estimate", "--template", "Doc", "--prompt", "p", "--brand-kit", "3"])
            self.assertEqual(est.call_args.kwargs["brand_kit_id"], 3)

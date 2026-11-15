"""MCP and CLI follow this week's product rules: five live formats, Avatar IV only, captions off by default, the
motion-graphics Look exception to plan jobs, designed voice settings, the painted price in reviews, and the
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
        self.assertIn("route-atlas", server.instructions)
        self.assertIn("route-atlas", tools["list_looks"].description)
        self.assertIn("motion_graphics_look_plan", tools["create_plan_job"].description)

    def test_voice_design_tools_exist(self):
        _server, tools = _tools()
        for name in ("voice_design_options", "update_designed_voice_settings", "try_designed_voice",
                     "remix_designed_voice", "save_remixed_voice", "delete_designed_voice"):
            self.assertIn(name, tools)
        for key in ("model_id", "guidance_scale"):
            self.assertIn(key, tools["design_voice_previews"].input_schema["properties"])
        for key in ("stability", "similarity_boost", "style", "use_speaker_boost", "speed"):
            self.assertIn(key, tools["save_designed_voice"].input_schema["properties"])

    def test_review_card_shows_rate_painted_add_on_and_look(self):
        js = (WIDGETS / "views.js").read_text(encoding="utf-8")
        self.assertIn("Credits per minute", js)
        self.assertIn("painted_look_credits_per_minute", js)
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

    def test_review_shows_the_rate_and_the_painted_add_on(self):
        review = {"settings": [{"label": "Look", "value": "Empire Story (empire-story), painted +20/min"}],
                  "credits": {"credits_required": 600, "credits_per_minute": 60, "painted_look_credits_per_minute": 20}}
        lines = cli._rate_lines(review)
        self.assertIn("Credits per minute: 60", lines)
        self.assertTrue(any("+20 credits/min" in line for line in lines))

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

from pathlib import Path
"""MCP Apps widgets: ui:// resources, tool bindings, and the old JSON for clients without Apps."""

import asyncio
import json
from unittest import TestCase
from unittest.mock import patch

from mcp import Client

from cliptude_mcp import apps
from cliptude_mcp.server import build_server

APP_MIME = "text/html;profile=mcp-app"
WIDGET_URIS = {
    "ui://cliptude/avatars.html",
    "ui://cliptude/voices.html",
    "ui://cliptude/characters.html",
    "ui://cliptude/player.html",
    "ui://cliptude/choices.html",
    "ui://cliptude/publish-kit.html",
}
EXPECTED_BINDINGS = {
    "list_avatars": "ui://cliptude/avatars.html",
    "list_voices": "ui://cliptude/voices.html",
    "list_vector_characters": "ui://cliptude/characters.html",
    "get_render": "ui://cliptude/player.html",
    "get_product_video_status": "ui://cliptude/player.html",
    "show_video": "ui://cliptude/player.html",
    "show_choices": "ui://cliptude/choices.html",
    "show_publish_kit": "ui://cliptude/publish-kit.html",
}


def run(coro):
    return asyncio.run(coro)


async def _with_client(fn):
    async with Client(build_server(with_http_auth=False)) as client:
        return await fn(client)


class WidgetResourceTests(TestCase):
    def test_every_widget_is_a_ui_resource_with_the_app_mime_type_and_csp(self):
        async def check(client):
            listed = {str(r.uri): r for r in (await client.list_resources()).resources}
            self.assertLessEqual(WIDGET_URIS, set(listed))
            for uri in WIDGET_URIS:
                self.assertEqual(listed[uri].mime_type, APP_MIME)
                read = await client.read_resource(uri)
                content = read.contents[0]
                self.assertEqual(content.mime_type, APP_MIME)
                ui = (content.meta or {}).get("ui") or (listed[uri].meta or {}).get("ui")
                domains = ui["csp"]["resourceDomains"]
                for host in ("https://cliptude.com", "https://cdn.cliptude.com", "https://*.r2.cloudflarestorage.com",
                             "https://storage.googleapis.com", "https://files.cartesia.ai", "https://voicetory.com"):
                    self.assertIn(host, domains, uri)
                self.assertNotIn("connectDomains", ui["csp"])  # widgets fetch nothing themselves
                self.assertTrue(content.text.lstrip().lower().startswith("<!doctype html>"))
            kit = (await client.read_resource("ui://cliptude/publish-kit.html")).contents[0]
            self.assertEqual(((kit.meta or {}).get("ui") or {}).get("permissions"), {"clipboardWrite": {}})

        run(_with_client(check))

    def test_no_wildcard_beyond_r2_and_localhost_only_for_stdio(self):
        hosted = apps.media_domains(local=False)
        self.assertEqual([d for d in hosted if "*" in d], ["https://*.r2.cloudflarestorage.com"])
        self.assertNotIn("http://127.0.0.1:*", hosted)
        self.assertIn("http://127.0.0.1:*", apps.media_domains(local=True))

    def test_widget_html_is_self_contained_and_speaks_the_apps_protocol(self):
        for file_name, _name, _desc in apps.WIDGETS.values():
            html = apps.widget_html(file_name)
            self.assertNotRegex(html, r"<script[^>]+\bsrc\s*=", file_name)
            self.assertNotRegex(html, r"<link[^>]+stylesheet", file_name)
            self.assertNotIn("@import", html, file_name)
            self.assertNotIn("/*@BRIDGE_JS@*/", html, file_name)
            self.assertNotIn("/*@BASE_CSS@*/", html, file_name)
            for method in (
                "ui/initialize",
                "ui/notifications/initialized",
                "ui/notifications/tool-input",
                "ui/notifications/tool-result",
                "ui/notifications/size-changed",
                "ui/notifications/host-context-changed",
                "ui/message",
                "ui/update-model-context",
                "ui/open-link",
                "ui/resource-teardown",
            ):
                self.assertIn(f'"{method}"', html, f"{file_name} lacks {method}")
            self.assertIn('protocolVersion: PROTOCOL_VERSION', html)
            self.assertIn('appCapabilities', html)

    def test_pickers_post_the_exact_ids_into_the_chat(self):
        self.assertIn("avatar_profile_id", apps.widget_html("avatars.html"))
        voices = apps.widget_html("voices.html")
        self.assertIn("voice_id", voices)
        self.assertIn("provider", voices)
        self.assertIn("vector_character_id", apps.widget_html("characters.html"))
        self.assertIn("background_music_mode=none", apps.widget_html("choices.html"))
        self.assertIn("navigator.clipboard", apps.widget_html("publish-kit.html"))


class ToolBindingTests(TestCase):
    def test_bound_tools_carry_the_ui_resource_uri(self):
        async def check(client):
            tools = {t.name: t for t in (await client.list_tools()).tools}
            for name, uri in EXPECTED_BINDINGS.items():
                meta = tools[name].meta or {}
                self.assertEqual(meta.get("ui", {}).get("resourceUri"), uri, name)
                self.assertEqual(meta.get("ui/resourceUri"), uri, name)
            # Tools without a widget stay plain.
            self.assertFalse((tools["get_job"].meta or {}).get("ui"))
            self.assertFalse((tools["get_template_options"].meta or {}).get("ui"))

        run(_with_client(check))

    def test_bound_tools_keep_their_parameters(self):
        async def check(client):
            tools = {t.name: t for t in (await client.list_tools()).tools}
            self.assertEqual(set(tools["list_voices"].input_schema["properties"]), {"provider", "language"})
            self.assertEqual(set(tools["list_avatars"].input_schema["properties"]),
                             {"section", "search", "gender", "limit", "offset"})
            self.assertEqual(set(tools["get_render"].input_schema["properties"]), {"render_id"})

        run(_with_client(check))

    def test_client_without_apps_still_gets_the_old_json(self):
        voices = {"provider": "elevenlabs", "voices": [{"voice_id": "v1", "name": "Mike", "preview_url": ""}], "error": None}
        avatars = {"results": [{"id": 10794, "name": "Ethan Cole", "thumbnail_url": "https://x/a.png"}], "count": 1}
        render = {"render": {"id": 5, "status": "completed", "output_url": "https://a.r2.cloudflarestorage.com/x.mp4"}}

        async def check(client):
            with patch("cliptude_mcp.client.CliptudeClient.list_voices", return_value=voices), \
                 patch("cliptude_mcp.client.CliptudeClient.list_avatars", return_value=avatars), \
                 patch("cliptude_mcp.client.CliptudeClient.get_render", return_value=render):
                for name, args, expected in (
                    ("list_voices", {"provider": "elevenlabs"}, voices),
                    ("list_avatars", {}, avatars),
                    ("get_render", {"render_id": 5}, render),
                ):
                    result = await client.call_tool(name, args)
                    self.assertFalse(result.is_error, name)
                    self.assertEqual(json.loads(result.content[0].text), expected, name)
                    self.assertEqual(result.structured_content, expected, name)

        run(_with_client(check))


class NewToolPayloadTests(TestCase):
    def test_show_choices_uses_choice_details_and_tells_where_the_value_goes(self):
        options = {"fields": {"background_music_library_file": {
            "choices": ["a.mp3"], "default": "a.mp3",
            "choice_details": [{"value": "a.mp3", "label": "A", "preview_url": "https://cliptude.com/static/music/a.mp3"}],
        }}}
        out = apps.choices_payload(options, "faceless_documentary", "background_music_library_file")
        self.assertEqual(out["kind"], "music")
        self.assertEqual(out["choices"][0]["preview_url"], "https://cliptude.com/static/music/a.mp3")
        self.assertIn("background_music_mode", out["usage"])
        self.assertIn("look", out["usage"])

    def test_show_choices_falls_back_to_plain_values_on_an_older_server(self):
        options = {"fields": {"caption_style": {"choices": ["soft-shadow"]}}}
        out = apps.choices_payload(options, "faceless_documentary", "caption_style")
        self.assertEqual(out["choices"], [{"value": "soft-shadow", "label": "soft-shadow"}])
        self.assertIn("error", apps.choices_payload({"fields": {}}, "vector_explainer", "caption_style"))

    def test_show_choices_rejects_unknown_fields(self):
        async def check(client):
            result = await client.call_tool("show_choices", {"format": "faceless_documentary", "field": "voice_id"})
            self.assertIn("field must be one of", json.loads(result.content[0].text)["error"])

        run(_with_client(check))

    def test_show_video_reads_the_job_output(self):
        job = {"id": "abc12345", "title": "TSMC", "status": "completed", "output": {"url": "https://cdn.cliptude.com/v.mp4", "path": None}}
        out = apps.video_payload(job, "abc12345")
        self.assertEqual((out["url"], out["title"], out["status"]), ("https://cdn.cliptude.com/v.mp4", "TSMC", "completed"))
        pending = apps.video_payload({"id": "abc12345", "status": "processing", "progress": 40, "output": {}}, "abc12345")
        self.assertEqual(pending["url"], "")

    def test_publish_kit_keeps_thumbnails_titles_description_and_tags(self):
        data = {"project": {"id": 9, "title": "TSMC", "thumbnails": {"status": "completed", "items": [{"url": "https://cdn.cliptude.com/t.jpg", "label": "Thumbnail 1"}]},
                            "publish_metadata": {"title_variants": ["A", "B"], "description": "D", "chapters": "00:00 Intro",
                                                 "tags": ["chips", "taiwan"], "downloads": {}}, "timeline": [1, 2, 3]}}
        out = apps.publish_kit_payload(data, "job12345")
        self.assertEqual(out["project_id"], 9)
        self.assertEqual(out["publish_metadata"]["tags"], ["chips", "taiwan"])
        self.assertEqual(out["thumbnails"]["items"][0]["label"], "Thumbnail 1")
        self.assertNotIn("timeline", out)
        self.assertEqual(out["video"]["url"], "")  # no export yet

    def test_publish_kit_plays_the_latest_export(self):
        data = {"project": {"id": 9, "title": "Aral", "latest_render": {"status": "completed",
                            "output_url": "https://x.r2.cloudflarestorage.com/v.mp4"}, "publish_metadata": {}}}
        out = apps.publish_kit_payload(data, "job12345")
        self.assertEqual(out["video"], {"url": "https://x.r2.cloudflarestorage.com/v.mp4", "source": "export", "note": ""})
        running = apps.publish_kit_payload({"project": {"id": 9, "latest_render": {"status": "running"}}}, "job12345")
        self.assertEqual(running["video"]["url"], "")
        self.assertIn("render_project", running["video"]["note"])

    def test_publish_kit_widget_opens_on_the_video(self):
        html = (Path(apps.__file__).parent / "widgets" / "publish-kit.html").read_text()
        self.assertIn("stage-video", html)
        self.assertIn("current = kit.video && kit.video.url ? -1 : 0", html)


class InstructionsTests(TestCase):
    def test_instructions_tell_the_model_to_use_the_pickers(self):
        async def check(client):
            text = client.instructions or ""
            for name in ("list_avatars", "list_voices", "show_choices", "show_video", "show_publish_kit",
                         "create_avatar", "design_voice_previews", "save_designed_voice", "clone_voice", "my_voices"):
                self.assertIn(name, text[text.index("PICKERS"):])

        run(_with_client(check))


class CreationToolTests(TestCase):
    """create_avatar, design_voice_previews, save_designed_voice and clone_voice: the pickers' make-your-own tools."""

    def test_tools_are_listed_with_their_widget_permissions(self):
        async def check(client):
            names = {t.name for t in (await client.list_tools()).tools}
            self.assertLessEqual({"create_avatar", "design_voice_previews", "save_designed_voice", "clone_voice"}, names)
            voices = (await client.read_resource("ui://cliptude/voices.html")).contents[0]
            self.assertEqual(((voices.meta or {}).get("ui") or {}).get("permissions"), {"microphone": {}})

        run(_with_client(check))

    def test_widgets_offer_create_design_and_clone(self):
        avatars = apps.widget_html("avatars.html")
        self.assertIn("Create a new avatar", avatars)
        self.assertIn('"create_avatar"', avatars)
        self.assertIn("review_token", avatars)
        self.assertIn("https://cliptude.com/videos/avatars/create/", avatars)
        voices = apps.widget_html("voices.html")
        for text in ('"design_voice_previews"', '"save_designed_voice"', '"clone_voice"', "Your voices",
                     "explicit consent and the rights to clone it", "getUserMedia",
                     "https://cliptude.com/videos/voices/clone/", "https://cliptude.com/videos/voices/design/"):
            self.assertIn(text, voices)

    def test_files_come_as_base64_never_as_a_path_on_the_hosted_server(self):
        from cliptude_mcp import creation

        with self.assertRaises(creation.CliptudeApiError):
            creation._file_arg(data_base64=None, file_name=None, path="/etc/hosts", local=False, what="recording")
        name, raw, _mime = creation._file_arg(data_base64="data:audio/wav;base64,UklGRg==", file_name="a.wav",
                                              path=None, local=False, what="recording")
        self.assertEqual((name, raw), ("a.wav", b"RIFF"))
        with self.assertRaises(creation.CliptudeApiError):
            creation._file_arg(data_base64="A" * (creation.MAX_UPLOAD_BYTES * 2), file_name="big.mp3", path=None,
                               local=False, what="recording")

    def test_clone_needs_consent(self):
        async def check(client):
            with patch("cliptude_mcp.creation.post_form") as post:
                result = await client.call_tool("clone_voice", {"provider": "elevenlabs", "voice_name": "Me",
                                                                "consent": False, "audio_base64": "UklGRg=="})
                post.assert_not_called()
            self.assertIn("must confirm", json.loads(result.content[0].text)["error"])

        run(_with_client(check))

    def test_avatar_review_comes_back_as_a_428_and_the_token_is_sent_on(self):
        from cliptude_mcp.client import CliptudeApiError

        review = {"error": "review_required", "review": {"credits": {"credits_required": 55}}, "review_token": "t1"}
        calls = []

        def fake_post(_client, path, data, files=None):
            calls.append((path, data, files))
            if not data.get("review_token"):
                raise CliptudeApiError("review_required", status=428, payload=review)
            return {"avatar": {"id": 5, "name": data["display_name"], "section": "mine", "status": "pending"}}

        async def check(client):
            with patch("cliptude_mcp.creation.post_form", side_effect=fake_post):
                first = await client.call_tool("create_avatar", {"display_name": "Me", "appearance": "A presenter",
                                                                 "gender": "female"})
                body = json.loads(first.content[0].text)
                self.assertEqual(body["status"], 428)
                self.assertEqual(body["detail"]["review_token"], "t1")
                second = await client.call_tool("create_avatar", {"display_name": "Me", "appearance": "A presenter",
                                                                  "gender": "female", "review_token": "t1"})
                self.assertEqual(json.loads(second.content[0].text)["avatar"]["status"], "pending")
            self.assertEqual(calls[0][0], "/api/v1/avatars/new")
            self.assertEqual(calls[1][1]["review_token"], "t1")

        run(_with_client(check))

    def test_design_previews_keep_audio_out_of_the_models_text(self):
        payload = {"previews": [{"preview_id": "p1", "audio_url": "data:audio/mpeg;base64," + "A" * 5000}],
                   "rounds_left_today": 19}

        async def check(client):
            with patch("cliptude_mcp.creation.post_form", return_value=payload):
                result = await client.call_tool("design_voice_previews", {"provider": "elevenlabs",
                                                                          "description": "A calm deep narrator"})
            self.assertNotIn("base64", result.content[0].text)
            self.assertIn("p1", result.content[0].text)
            self.assertTrue(result.structured_content["previews"][0]["audio_url"].startswith("data:audio"))

        run(_with_client(check))

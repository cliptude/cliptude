"""Browser fallback for clients without MCP Apps (cliptude_mcp/picks.py): browser_picker_url, pick_id, get_pick."""

import asyncio
import json
import os
from unittest import TestCase
from unittest.mock import patch

from mcp import Client
from mcp.client.extension import advertise
from mcp.types import Implementation

from cliptude_mcp import picks
from cliptude_mcp.client import CliptudeApiError, CliptudeClient
from cliptude_mcp.server import build_server

APPS = advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})
PICK = {"pick_id": "3f1c2a8e-0000-4000-8000-000000000001", "url": "https://cliptude.com/pick/abc",
        "expires_at": "2026-11-02T12:15:00+00:00"}
VOICES = {"provider": "elevenlabs", "voices": [{"voice_id": "v1", "name": "Mike"}]}
JOB = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"


def setUpModule():
    # Never reach a real Cliptude: every call that is not patched fails on an invalid host.
    global _env
    _env = patch.dict(os.environ, {"CLIPTUDE_BASE_URL": "https://api.test.invalid", "CLIPTUDE_API_KEY": "clp_test",
                                   "CLIPTUDE_BROWSER_PICKER": "1"})
    _env.start()


def tearDownModule():
    _env.stop()


def call(tool, args, *, extensions=None, mode="auto", client_info=None, patches=()):
    async def go():
        kwargs = {"mode": mode}
        if extensions:
            kwargs["extensions"] = extensions
        if client_info:
            kwargs["client_info"] = client_info
        async with Client(build_server(with_http_auth=False), **kwargs) as client:
            result = await client.call_tool(tool, args)
            return json.loads(result.content[0].text), getattr(client, "_session", None)

    return asyncio.run(go())


class FallbackTests(TestCase):
    def test_a_client_without_apps_gets_a_browser_picker(self):
        with patch.object(CliptudeClient, "list_voices", return_value=VOICES), \
             patch.object(CliptudeClient, "create_pick", return_value=PICK) as create:
            out, _ = call("list_voices", {"provider": "elevenlabs"})
        create.assert_called_once_with("voices", {"provider": "elevenlabs"})
        self.assertEqual(out["browser_picker_url"], PICK["url"])
        self.assertEqual(out["pick_id"], PICK["pick_id"])
        self.assertIn("get_pick", out["browser_picker"])
        self.assertEqual(out["voices"], VOICES["voices"])

    def test_a_client_with_apps_gets_the_old_result(self):
        with patch.object(CliptudeClient, "list_voices", return_value=VOICES), \
             patch.object(CliptudeClient, "create_pick", return_value=PICK) as create:
            out, _ = call("list_voices", {"provider": "elevenlabs"}, extensions=[APPS])
        create.assert_not_called()
        self.assertEqual(out, VOICES)

    def test_protocol_2025_11_25_handshake_still_shows_the_clients_extension(self):
        # The server cannot advertise the extension at this version, but it reads the client's from initialize.
        for extensions, expect_picker in (([APPS], False), (None, True)):
            with patch.object(CliptudeClient, "list_voices", return_value=VOICES), \
                 patch.object(CliptudeClient, "create_pick", return_value=PICK) as create:
                out, session = call("list_voices", {"provider": "elevenlabs"}, extensions=extensions, mode="legacy")
            self.assertEqual(session.protocol_version, "2025-11-25")
            self.assertEqual("browser_picker_url" in out, expect_picker, extensions)
            self.assertEqual(create.called, expect_picker)

    def test_known_widget_hosts_by_name_get_no_picker(self):
        with patch.object(CliptudeClient, "list_voices", return_value=VOICES), \
             patch.object(CliptudeClient, "create_pick", return_value=PICK) as create:
            out, _ = call("list_voices", {}, mode="legacy", client_info=Implementation(name="claude-ai", version="1"))
        create.assert_not_called()
        self.assertNotIn("browser_picker_url", out)

    def test_the_switch_turns_it_off(self):
        with patch.dict(os.environ, {"CLIPTUDE_BROWSER_PICKER": "0"}), \
             patch.object(CliptudeClient, "list_voices", return_value=VOICES), \
             patch.object(CliptudeClient, "create_pick", return_value=PICK) as create:
            out, _ = call("list_voices", {})
        create.assert_not_called()
        self.assertEqual(out, VOICES)

    def test_a_failed_pick_keeps_the_tools_answer(self):
        with patch.object(CliptudeClient, "list_voices", return_value=VOICES), \
             patch.object(CliptudeClient, "create_pick", side_effect=CliptudeApiError("boom", status=500)):
            out, _ = call("list_voices", {})
        self.assertEqual(out, VOICES)

    def test_a_review_sends_where_to_find_it_never_its_content(self):
        review = {"error": "review_required", "review": {"settings": [{"label": "Voice", "value": "Mike"}],
                                                         "acknowledgements_required": []}, "review_token": "tok"}

        def fake_continue(job_id, *, thumbnail=None, review_token=None):
            raise CliptudeApiError("Review required", status=428, payload=review)

        with patch.object(CliptudeClient, "continue_job", side_effect=fake_continue), \
             patch.object(CliptudeClient, "create_pick", return_value=PICK) as create:
            out, _ = call("continue_job", {"job_id": JOB, "review_token": "old"})
        kind, source = create.call_args.args
        self.assertEqual(kind, "review")
        self.assertEqual(source["tool"], "continue_job")
        self.assertEqual(source["review_token"], "tok")
        self.assertEqual(source["arguments"]["job_id"], JOB)
        self.assertNotIn("review_token", source["arguments"])
        self.assertNotIn("review", source)
        self.assertEqual((out["status"], out["tool"], out["pick_id"]), (428, "continue_job", PICK["pick_id"]))

    def test_job_polls_get_a_picker_only_when_there_is_something_to_see(self):
        cases = (
            ({"id": JOB, "status": "running", "progress": 40}, None),
            ({"id": JOB, "status": "completed", "output_kind": "script", "script": "Hello."}, "script"),
            ({"id": JOB, "status": "completed", "output_kind": "video", "output": {"url": "https://x/v.mp4"}}, "job"),
        )
        for job, kind in cases:
            with patch.object(CliptudeClient, "get_job", return_value=job), \
                 patch.object(CliptudeClient, "create_pick", return_value=PICK) as create:
                out, _ = call("get_job", {"job_id": JOB})
            if kind is None:
                create.assert_not_called()
                self.assertNotIn("browser_picker_url", out)
            else:
                self.assertEqual(create.call_args.args, (kind, {"job_id": JOB, "product": False}))

    def test_errors_get_no_picker(self):
        with patch.object(CliptudeClient, "list_voices", side_effect=CliptudeApiError("nope", status=403)), \
             patch.object(CliptudeClient, "create_pick", return_value=PICK) as create:
            out, _ = call("list_voices", {})
        create.assert_not_called()
        self.assertEqual(out["status"], 403)

    def test_get_pick_reads_the_pick_and_is_read_only(self):
        done = {"pick_id": PICK["pick_id"], "status": "done", "result": {"message": "Use the voice Mike (voice_id v1).",
                                                                       "choice": {"voice_id": "v1"}}}
        with patch.object(CliptudeClient, "get_pick", return_value=done) as get:
            out, _ = call("get_pick", {"pick_id": PICK["pick_id"]})
        get.assert_called_once_with(PICK["pick_id"])
        self.assertEqual(out, done)

        async def annotations():
            async with Client(build_server(with_http_auth=False)) as client:
                return {t.name: t for t in (await client.list_tools()).tools}["get_pick"]

        tool = asyncio.run(annotations())
        self.assertTrue(tool.annotations.read_only_hint)
        self.assertNotIn("ui", tool.meta or {})

    def test_every_widget_tool_has_the_fallback(self):
        from cliptude_mcp.apps import BOUND_TOOLS

        server = build_server(with_http_auth=False)
        wrapped = picks.add_browser_fallback(server, lambda: None)  # idempotent: already wrapped
        names = {n for n in BOUND_TOOLS if n not in ("get_narration",)} | {"show_choices", "show_video", "show_publish_kit"}
        for name in names:
            self.assertIn(name, wrapped, name)
        self.assertNotIn("get_narration", wrapped)


class SourceTests(TestCase):
    def test_file_contents_stay_with_the_agent(self):
        kind, source = picks.pick_source("create_avatar", {"display_name": "Me", "photo_base64": "A" * 50000},
                                         {"status": 428, "detail": {"review": {"settings": []}, "review_token": "t"}})
        self.assertEqual(kind, "review")
        self.assertEqual(source["arguments"]["photo_base64"], "<the same file as before>")

    def test_unready_renders_and_videos_have_nothing_to_show(self):
        self.assertIsNone(picks.pick_source("get_render", {"render_id": 5}, {"render": {"status": "running"}}))
        self.assertIsNone(picks.pick_source("show_video", {"job_id": JOB}, {"url": ""}))
        self.assertEqual(picks.pick_source("get_render", {"render_id": 5},
                                           {"render": {"output_url": "https://a.r2.cloudflarestorage.com/x.mp4"}}),
                         ("video", {"render_id": 5}))


class InstructionTests(TestCase):
    def test_instructions_and_both_skills_explain_the_browser_picker(self):
        from pathlib import Path

        server = build_server(with_http_auth=False)
        text = server.instructions if hasattr(server, "instructions") else server._mcp_server.instructions
        root = Path(__file__).resolve().parents[2]
        skills = [(root / p).read_text(encoding="utf-8") for p in
                  (".agents/skills/cliptude/SKILL.md", "mcp_server/skills/cliptude/SKILL.md")]
        for doc in (text, *skills):
            for phrase in ("browser_picker_url", "get_pick", "cliptude pick", "15 minutes"):
                self.assertIn(phrase, doc)
            self.assertRegex(doc, r"Only .{0,2}status:? .{0,2}done.{0,2} carries")
            self.assertIn("explicit OK", doc)


class PickCliTests(TestCase):
    def test_cliptude_pick_opens_the_link_and_waits_for_the_pick(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from cliptude_mcp import cli

        done = {"pick_id": PICK["pick_id"], "status": "done", "result": {"choice": {"voice_id": "v1"}}}
        out = StringIO()
        with patch.object(CliptudeClient, "create_pick", return_value=PICK) as create, \
             patch.object(CliptudeClient, "get_pick", side_effect=[{"status": "open"}, done]), \
             patch("webbrowser.open") as opened, patch("time.sleep"), redirect_stdout(out), \
             redirect_stderr(StringIO()):
            cli.main(["pick", "--kind", "voices", "--source", '{"provider": "elevenlabs"}'])
        create.assert_called_once_with("voices", {"provider": "elevenlabs"})
        opened.assert_called_once_with(PICK["url"])
        self.assertEqual(json.loads(out.getvalue())["result"]["choice"], {"voice_id": "v1"})

    def test_an_expired_pick_exits_non_zero_and_odd_links_are_refused(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from cliptude_mcp import cli

        with patch.object(CliptudeClient, "get_pick", return_value={"status": "expired"}), \
             patch("webbrowser.open") as opened, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli.main(["pick", PICK["pick_id"], "--url", PICK["url"]])
            self.assertEqual(raised.exception.code, 1)
            with self.assertRaises(SystemExit):
                cli.main(["pick", PICK["pick_id"], "--url", "javascript:alert(1)"])
        opened.assert_called_once_with(PICK["url"])

"""Sponsored segments on MCP and the CLI: the sponsor params become the sentence the server reads
(videos/sponsor_segments.py) on every path."""

import asyncio
from unittest import TestCase
from unittest.mock import patch

from cliptude_mcp import cli
from cliptude_mcp.client import CliptudeClient, merge_sponsor


class MergeSponsorTests(TestCase):
    def test_sentence(self):
        self.assertEqual(merge_sponsor("Calm.", "@NordVPN_ad.mp4", "after the intro", False),
                         "Calm.\n\nSponsor: @nordvpn_ad after the intro, no bumpers.")
        self.assertEqual(merge_sponsor(None, "nordvpn_ad"), "Sponsor: @nordvpn_ad.")
        self.assertEqual(merge_sponsor("x", None), "x")
        already = "Sponsor: @nordvpn_ad halfway."
        self.assertEqual(merge_sponsor(already, "nordvpn_ad", "at 2:00"), already)

    def test_client_puts_it_in_the_prompt(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "request", return_value={"id": "abc"}) as req:
            client.create_job(template_id="t", title="T", script="Words.", sponsor="nordvpn_ad",
                              sponsor_placement="at 2:00")
        self.assertEqual(req.call_args.kwargs["json_body"]["prompt"], "Sponsor: @nordvpn_ad at 2:00.")
        with patch.object(client, "request", return_value={}) as req:
            client.estimate_credits(template_id="t", script="Words.", sponsor="nordvpn_ad")
        self.assertEqual(req.call_args.kwargs["json_body"]["prompt"], "Sponsor: @nordvpn_ad.")

    def test_cli_flags(self):
        with patch.object(cli.CliptudeClient, "list_templates", return_value={"results": [{"id": "t1", "name": "Doc"}]}), \
                patch.object(cli.CliptudeClient, "create_job", return_value={"id": "j1"}) as create, \
                patch.object(cli, "_print"):
            cli.main(["generate", "--template", "Doc", "--title", "T", "--prompt", "Tea history.",
                      "--sponsor", "@nordvpn_ad", "--sponsor-placement", "halfway", "--no-sponsor-bumpers"])
        kwargs = create.call_args.kwargs
        self.assertEqual((kwargs["sponsor"], kwargs["sponsor_placement"], kwargs["sponsor_bumpers"]),
                         ("@nordvpn_ad", "halfway", False))

    def test_mcp_tools_take_sponsor(self):
        from cliptude_mcp.server import build_server

        tools = {t.name: t for t in asyncio.run(build_server().list_tools())}
        for name in ("create_job", "create_plan_job", "estimate_credits"):
            props = tools[name].input_schema["properties"]
            for key in ("sponsor", "sponsor_placement", "sponsor_bumpers"):
                self.assertIn(key, props, (name, key))
        self.assertIn("add_sponsor", str(tools["edit_timeline"].input_schema))

    def test_get_job_relays_the_skipped_sponsor_warning(self):
        from cliptude_mcp.server import build_server

        warning = {"code": "sponsor_skipped", "sponsor": "@nordvpn_ad",
                   "message": "Your sponsored segment (@nordvpn_ad) couldn't be added: it is longer than the "
                              "3-minute limit. The video was finished without it. You can add it in the editor."}
        server = build_server()
        tools = {t.name: t for t in asyncio.run(server.list_tools())}
        self.assertIn("warnings", tools["get_job"].description)
        self.assertIn("sponsor_skipped", tools["get_job"].description)
        with patch.object(CliptudeClient, "get_job", return_value={"id": "j1", "status": "completed",
                                                                  "warnings": [warning]}):
            out = asyncio.run(server.call_tool("get_job", {"job_id": "12345678-aaaa"}))
        self.assertIn("couldn't be added", str(out))

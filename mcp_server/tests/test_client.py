from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from cliptude_mcp.auth import CliptudeTokenVerifier
from cliptude_mcp.client import CliptudeApiError, CliptudeClient, django_proxy_headers


class ClientTests(TestCase):
    def test_internal_http_base_sets_forwarded_proto(self):
        self.assertEqual(django_proxy_headers("http://web:5021"), {"X-Forwarded-Proto": "https"})
        self.assertEqual(django_proxy_headers("https://cliptude.com"), {})
        client = CliptudeClient(base_url="http://web:5021", api_key="clp_test")
        self.assertEqual(client._headers()["X-Forwarded-Proto"], "https")

    def test_create_job_posts_script(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "get_template", return_value={"format": "youtube"}):
            with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
                client.create_job(template_id="tmpl", title="T", script="Spoken words.")
        req.assert_called_once()
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(kwargs["json_body"]["script"], "Spoken words.")
        self.assertNotIn("prompt", kwargs["json_body"])
        self.assertNotIn("faceless_coding", kwargs["json_body"])

    def test_create_template_posts_format_and_settings(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "request", return_value={"id": "t1"}) as req:
            client.create_template(name="Doc", format="faceless_documentary", settings={"voice_id": "v"})
        req.assert_called_once_with(
            "POST",
            "/api/v1/templates",
            json_body={"name": "Doc", "format": "faceless_documentary", "settings": {"voice_id": "v"}},
        )

    def test_run_agent_uses_current_revision_and_idempotency_key(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "get_editor_project", return_value={"project": {"revision": 7}}):
            with patch.object(client, "request", return_value={"run": {"id": "r1"}}) as req:
                client.run_agent(12, prompt="Swap the shot", referenced_item_ids=["clip-1"], tier="smart")
        args, kwargs = req.call_args
        self.assertEqual(args, ("POST", "/api/v1/editor-projects/12/agent/runs"))
        body = kwargs["json_body"]
        self.assertEqual(body["expected_revision"], 7)
        self.assertEqual(body["referenced_item_ids"], ["clip-1"])
        self.assertEqual(body["tier"], "smart")
        self.assertTrue(body["idempotency_key"])

    def test_cli_wait_stops_on_terminal_status(self):
        from cliptude_mcp.cli import _wait

        states = iter([{"run": {"status": "running"}}, {"run": {"status": "completed"}}])
        with patch("time.sleep"):
            out = _wait(lambda: next(states), lambda p: p["run"]["status"], interval=0)
        self.assertEqual(out["run"]["status"], "completed")

    def test_cli_set_args_parse_json_values(self):
        from cliptude_mcp.cli import _parse_set_args

        self.assertEqual(
            _parse_set_args(["a=true", "b=#E11D48", "c=0.4", 'd=["x"]', "e=Rachel Voice"]),
            {"a": True, "b": "#E11D48", "c": 0.4, "d": ["x"], "e": "Rachel Voice"},
        )
        with self.assertRaises(CliptudeApiError):
            _parse_set_args(["novalue"])

    def test_create_job_defaults_external_for_faceless_explainer(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "get_template", return_value={"format": "faceless_explainer"}):
            with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
                client.create_job(template_id="tmpl", title="T", script="Spoken words.")
        self.assertEqual(req.call_args.kwargs["json_body"]["faceless_coding"], "external")

    def test_create_job_omits_external_on_other_formats(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "get_template", return_value={"format": "youtube"}):
            with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
                client.create_job(
                    template_id="tmpl",
                    title="T",
                    prompt="topic",
                    faceless_coding="external",
                )
        self.assertNotIn("faceless_coding", req.call_args.kwargs["json_body"])

    def test_create_job_posts_explicit_cliptude(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "get_template") as get_tmpl:
            with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
                client.create_job(
                    template_id="tmpl",
                    title="T",
                    prompt="topic",
                    faceless_coding="cliptude",
                )
        get_tmpl.assert_not_called()
        self.assertEqual(req.call_args.kwargs["json_body"]["faceless_coding"], "cliptude")

    def test_create_job_posts_faceless_coding(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "get_template", return_value={"format": "faceless_explainer"}):
            with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
                client.create_job(
                    template_id="tmpl",
                    title="T",
                    prompt="topic",
                    faceless_coding="external",
                )
        self.assertEqual(req.call_args.kwargs["json_body"]["faceless_coding"], "external")

    def test_create_job_posts_avatar_overrides(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "get_template", return_value={"format": "avatar_video"}):
            with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
                client.create_job(
                    template_id="tmpl",
                    title="T",
                    script="Spoken words.",
                    avatar_intro_only=True,
                    avatar_ai_footage=False,
                )
        body = req.call_args.kwargs["json_body"]
        self.assertTrue(body["avatar_intro_only"])
        self.assertFalse(body["avatar_ai_footage"])

    def test_create_job_omits_avatar_overrides_when_unset(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "get_template", return_value={"format": "avatar_video"}):
            with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
                client.create_job(template_id="tmpl", title="T", script="Spoken words.")
        body = req.call_args.kwargs["json_body"]
        self.assertNotIn("avatar_intro_only", body)
        self.assertNotIn("avatar_ai_footage", body)

    def test_create_job_posts_script_output(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "get_template", return_value={"format": "youtube"}):
            with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
                client.create_job(
                    template_id="tmpl",
                    title="T",
                    prompt="topic",
                    output="script",
                )
        self.assertEqual(req.call_args.kwargs["json_body"]["output"], "script")
        self.assertEqual(req.call_args.kwargs["json_body"]["prompt"], "topic")

    def test_create_job_posts_voiceover_output(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "get_template", return_value={"format": "youtube"}):
            with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
                client.create_job(
                    template_id="tmpl",
                    title="T",
                    script="Spoken words.",
                    output="voiceover",
                )
        self.assertEqual(req.call_args.kwargs["json_body"]["output"], "voiceover")
        self.assertEqual(req.call_args.kwargs["json_body"]["script"], "Spoken words.")

    def test_create_job_rejects_partial_with_upload(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with self.assertRaises(CliptudeApiError):
            client.create_job(
                template_id="tmpl",
                title="T",
                voiceover_path="/tmp/x.mp3",
                output="script",
            )

    def test_continue_job_posts_to_continue_endpoint(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "request", return_value={"id": "abc", "status": "queued", "output_kind": "video"}) as req:
            client.continue_job("abc")
        req.assert_called_once()
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "/api/v1/jobs/abc/continue")
        self.assertIsNone(kwargs.get("json_body"))

    def test_continue_job_posts_faceless_coding(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
            client.continue_job("abc", faceless_coding="external")
        self.assertEqual(req.call_args.kwargs["json_body"]["faceless_coding"], "external")

    def test_create_job_multipart_voiceover(self):
        import tempfile
        from pathlib import Path

        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp.write(b"\xff\xfb" * 32)
            path = Path(tmp.name)
        try:
            with patch.object(client, "get_template", return_value={"format": "youtube"}):
                with patch("httpx.post") as post:
                    resp = MagicMock()
                    resp.status_code = 201
                    resp.json.return_value = {"id": "abc", "status": "queued", "has_uploaded_voice": True}
                    post.return_value = resp
                    client.create_job(
                        template_id="tmpl",
                        title="T",
                        voiceover_path=path,
                    )
            self.assertEqual(post.call_args.kwargs["data"]["template_id"], "tmpl")
            self.assertIn("voiceover", post.call_args.kwargs["files"])
            self.assertNotIn("json", post.call_args.kwargs)
        finally:
            path.unlink(missing_ok=True)

    def test_create_job_multipart_talking_head_fallback(self):
        import tempfile
        from pathlib import Path

        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"\x00" * 64)
            path = Path(tmp.name)
        try:
            with patch.object(client, "get_template", return_value={"format": "talking_head"}):
                with patch.object(
                    client,
                    "request",
                    return_value={"fallback": True, "reason": "r2_unavailable"},
                ) as req:
                    with patch("httpx.post") as post:
                        resp = MagicMock()
                        resp.status_code = 201
                        resp.json.return_value = {
                            "id": "abc",
                            "status": "queued",
                            "has_uploaded_talking_head": True,
                        }
                        post.return_value = resp
                        client.create_job(
                            template_id="tmpl",
                            title="T",
                            talking_head_path=path,
                        )
            req.assert_called_once()
            self.assertEqual(req.call_args.args[1], "/api/v1/uploads/talking-head/init")
            self.assertIn("talking_head", post.call_args.kwargs["files"])
        finally:
            path.unlink(missing_ok=True)

    def test_create_job_talking_head_r2_then_json(self):
        import tempfile
        from pathlib import Path

        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"\x00" * 64)
            path = Path(tmp.name)
        requests = []

        def fake_request(method, url, **kwargs):
            requests.append((method, url, kwargs.get("json_body")))
            if url.endswith("/init"):
                return {
                    "key": "talking_head/uploads/1/abc.mp4",
                    "upload_id": "up-1",
                    "part_size": 16 * 1024 * 1024,
                    "part_count": 1,
                    "size": 64,
                }
            if url.endswith("/sign-parts"):
                return {"urls": {"1": "https://r2.example/part1"}}
            if url.endswith("/complete"):
                return {"ok": True, "key": "talking_head/uploads/1/abc.mp4"}
            if url.endswith("/jobs"):
                return {"id": "abc", "status": "queued", "has_uploaded_talking_head": True}
            raise AssertionError(url)

        try:
            with patch.object(client, "get_template", return_value={"format": "talking_head"}):
                with patch.object(client, "request", side_effect=fake_request):
                    with patch(
                        "cliptude_mcp.client._probe_local_video_duration_seconds",
                        return_value=95.5,
                    ):
                        with patch("httpx.put") as put:
                            put.return_value = MagicMock(status_code=200)
                            result = client.create_job(
                                template_id="tmpl",
                                title="Weekly recap",
                                talking_head_path=path,
                            )
            self.assertEqual(result["id"], "abc")
            put.assert_called_once()
            job_calls = [row for row in requests if row[1] == "/api/v1/jobs"]
            self.assertEqual(len(job_calls), 1)
            body = job_calls[0][2]
            self.assertEqual(body["talking_head_r2_key"], "talking_head/uploads/1/abc.mp4")
            self.assertEqual(body["talking_head_duration"], "95.5")
            self.assertNotIn("prompt", body)
        finally:
            path.unlink(missing_ok=True)

    def test_download_coding_kit_returns_url_not_bytes(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(
            client,
            "get_job",
            return_value={"coding_kit": {"url": "https://cdn.example/kit.zip", "voice_duration_s": 12}},
        ):
            kit = client.download_coding_kit("job-1")
        self.assertEqual(kit["url"], "https://cdn.example/kit.zip")
        self.assertNotIn("bytes", kit)

    def test_missing_auth_asks_for_login(self):
        with patch.dict("os.environ", {"CLIPTUDE_API_KEY": ""}, clear=False):
            with patch("cliptude_mcp.client.stored_access_token", return_value=""):
                client = CliptudeClient(base_url="https://cliptude.com", api_key="")
                with self.assertRaises(Exception) as raised:
                    client._headers()
        self.assertIn("cliptude auth login", str(raised.exception))


class TokenVerifierTests(IsolatedAsyncioTestCase):
    async def test_rejects_empty_token(self):
        verifier = CliptudeTokenVerifier(base_url="https://cliptude.com")
        self.assertIsNone(await verifier.verify_token(""))

    async def test_accepts_account_200(self):
        verifier = CliptudeTokenVerifier(base_url="https://cliptude.com")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"email": "a@example.com"}
        mock_client = AsyncMock()
        mock_client.get.return_value = resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await verifier.verify_token("clp_abc")
        self.assertIsNotNone(result)
        self.assertEqual(result.token, "clp_abc")
        self.assertEqual(result.scopes, ["jobs"])
        self.assertNotIn("X-Forwarded-Proto", mock_client.get.call_args.kwargs["headers"])

    async def test_internal_http_verifier_sets_forwarded_proto(self):
        verifier = CliptudeTokenVerifier(base_url="http://web:5021")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"email": "a@example.com"}
        mock_client = AsyncMock()
        mock_client.get.return_value = resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False
        with patch("httpx.AsyncClient", return_value=mock_client):
            self.assertIsNotNone(await verifier.verify_token("oauth-token"))
        self.assertEqual(
            mock_client.get.call_args.kwargs["headers"]["X-Forwarded-Proto"],
            "https",
        )

    async def test_rejects_401(self):
        verifier = CliptudeTokenVerifier(base_url="https://cliptude.com")
        resp = MagicMock()
        resp.status_code = 401
        mock_client = AsyncMock()
        mock_client.get.return_value = resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False
        with patch("httpx.AsyncClient", return_value=mock_client):
            self.assertIsNone(await verifier.verify_token("bad"))


class DiscoveryMetadataTests(TestCase):
    def test_protected_resource_points_at_cliptude_issuer(self):
        from cliptude_mcp.auth import protected_resource_metadata

        data = protected_resource_metadata()
        self.assertEqual(data["resource"], "https://mcp.cliptude.com/mcp")
        self.assertEqual(data["authorization_servers"], ["https://cliptude.com"])

    def test_authorization_server_token_endpoint_is_not_mcp_origin(self):
        from cliptude_mcp.auth import authorization_server_metadata

        data = authorization_server_metadata()
        self.assertEqual(data["issuer"], "https://cliptude.com")
        self.assertEqual(data["token_endpoint"], "https://cliptude.com/o/token/")
        self.assertNotIn("mcp.cliptude.com", data["token_endpoint"])

    def test_discovery_wrapper_runs_inner_lifespan(self):
        from contextlib import asynccontextmanager

        try:
            from starlette.applications import Starlette
        except ImportError:
            self.skipTest("starlette not installed")

        from cliptude_mcp.server import _discovery_starlette

        started = {"count": 0}

        @asynccontextmanager
        async def inner_lifespan(app):
            started["count"] += 1
            yield

        inner = Starlette(lifespan=inner_lifespan)
        outer = _discovery_starlette(inner)

        async def _run():
            async with outer.router.lifespan_context(outer):
                pass

        import asyncio

        asyncio.run(_run())
        self.assertEqual(started["count"], 1)

    def test_public_mcp_host_is_allowlisted(self):
        from cliptude_mcp.server import mcp_allowed_hosts, mcp_allowed_origins

        hosts = mcp_allowed_hosts("https://mcp.cliptude.com/mcp")
        self.assertIn("mcp.cliptude.com", hosts)
        self.assertIn("mcp.cliptude.com:*", hosts)
        origins = mcp_allowed_origins("https://mcp.cliptude.com/mcp")
        self.assertIn("https://mcp.cliptude.com", origins)


class AgentPlanClientTests(TestCase):
    def setUp(self):
        self.client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")

    def test_create_plan_job_sends_output_plan(self):
        with patch.object(self.client, "get_template", return_value={"format": "faceless_documentary"}):
            with patch.object(self.client, "request", return_value={"id": "j1"}) as req:
                self.client.create_job(template_id="tmpl", title="T", script="Words.", output="plan")
        self.assertEqual(req.call_args.kwargs["json_body"]["output"], "plan")

    def test_check_plan_returns_the_report_on_400(self):
        report = {"valid": False, "errors": [{"code": "phrase_not_found"}]}
        with patch.object(self.client, "request", side_effect=CliptudeApiError("bad", status=400, payload=report)) as req:
            out = self.client.put_plan("j1", {"version": "cliptude.plan/v1"}, dry_run=True)
        self.assertEqual(req.call_args.args[:2], ("PUT", "/api/v1/jobs/j1/plan?dry_run=1"))
        self.assertEqual(out["errors"][0]["code"], "phrase_not_found")
        self.assertEqual(out["http_status"], 400)

    def test_lock_plan_and_other_errors(self):
        with patch.object(self.client, "request", return_value={"locked": True}) as req:
            self.client.put_plan("j1", {}, dry_run=False)
        self.assertEqual(req.call_args.args[1], "/api/v1/jobs/j1/plan")
        with patch.object(self.client, "request", side_effect=CliptudeApiError("Not found", status=404, payload={"error": "x"})):
            with self.assertRaises(CliptudeApiError):
                self.client.put_plan("j1", {}, dry_run=True)

    def test_catalog_params(self):
        with patch.object(self.client, "request", return_value={}) as req:
            self.client.get_kind_catalog(format="faceless_top_10", compact=True)
            self.client.get_kind_catalog(types=["price_callout", "date_overlay"])
        self.assertEqual(req.call_args_list[0].kwargs["params"], {"format": "faceless_top_10", "compact": "1"})
        self.assertEqual(req.call_args_list[1].kwargs["params"], {"types": "price_callout,date_overlay"})

    def test_cli_plan_check_reads_the_file(self):
        import json
        import tempfile

        from cliptude_mcp import cli

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"version": "cliptude.plan/v1"}, fh)
        with patch.object(cli.CliptudeClient, "put_plan", return_value={"valid": True}) as put, \
                patch.object(cli, "_print"):
            cli.main(["plan-check", "j1", fh.name])
        put.assert_called_once_with("j1", {"version": "cliptude.plan/v1"}, dry_run=True)

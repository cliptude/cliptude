from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from cliptude_mcp.auth import CliptudeTokenVerifier
from cliptude_mcp.client import CliptudeClient, django_proxy_headers


class ClientTests(TestCase):
    def test_internal_http_base_sets_forwarded_proto(self):
        self.assertEqual(django_proxy_headers("http://web:5021"), {"X-Forwarded-Proto": "https"})
        self.assertEqual(django_proxy_headers("https://cliptude.com"), {})
        client = CliptudeClient(base_url="http://web:5021", api_key="clp_test")
        self.assertEqual(client._headers()["X-Forwarded-Proto"], "https")

    def test_create_job_posts_script(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
            client.create_job(template_id="tmpl", title="T", script="Spoken words.")
        req.assert_called_once()
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(kwargs["json_body"]["script"], "Spoken words.")
        self.assertNotIn("prompt", kwargs["json_body"])

    def test_create_job_posts_faceless_coding(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
            client.create_job(
                template_id="tmpl",
                title="T",
                prompt="topic",
                faceless_coding="external",
            )
        self.assertEqual(req.call_args.kwargs["json_body"]["faceless_coding"], "external")

    def test_create_job_posts_duration(self):
        client = CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")
        with patch.object(client, "request", return_value={"id": "abc", "status": "queued"}) as req:
            client.create_job(template_id="tmpl", title="T", prompt="topic", duration="3-4")
        self.assertEqual(req.call_args.kwargs["json_body"]["duration"], "3-4")

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

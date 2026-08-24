import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cliptude_mcp.auth_login import authorize_url, pkce_pair
from cliptude_mcp.cli import main
from cliptude_mcp.client import CliptudeClient
from cliptude_mcp.credentials import clear_credentials, load_credentials, save_credentials


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cred_path = str(Path(self._tmp.name) / "credentials.json")
        self._env = patch.dict(os.environ, {"CLIPTUDE_CREDENTIALS": self.cred_path}, clear=False)
        self._env.start()
        os.environ.pop("CLIPTUDE_API_KEY", None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_roundtrip(self):
        save_credentials({"access_token": "tok_1", "client_id": "abc"})
        data = load_credentials()
        self.assertEqual(data["access_token"], "tok_1")
        clear_credentials()
        self.assertIsNone(load_credentials())

    def test_client_reads_stored_token(self):
        save_credentials({"access_token": "file-token"})
        client = CliptudeClient(base_url="https://cliptude.com")
        self.assertEqual(client.api_key, "file-token")

    def test_env_key_wins_over_file(self):
        save_credentials({"access_token": "file-token"})
        with patch.dict(os.environ, {"CLIPTUDE_API_KEY": "clp_env"}):
            client = CliptudeClient(base_url="https://cliptude.com")
        self.assertEqual(client.api_key, "clp_env")
        self.assertFalse(client._session_file)

    def test_auth_status_without_login(self):
        with self.assertRaises(SystemExit) as raised:
            main(["auth", "status"])
        self.assertEqual(raised.exception.code, 1)

    def test_auth_logout(self):
        save_credentials({"access_token": "tok"})
        main(["auth", "logout"])
        self.assertIsNone(load_credentials())


class PkceTests(unittest.TestCase):
    def test_pkce_pair_is_s256(self):
        import base64
        import hashlib

        verifier, challenge = pkce_pair()
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        self.assertEqual(challenge, expected)
        self.assertGreaterEqual(len(verifier), 32)

    def test_authorize_url_includes_pkce(self):
        url = authorize_url(
            "https://cliptude.com",
            client_id="cid",
            redirect_uri="http://127.0.0.1:9/callback",
            challenge="abc",
            state="st",
        )
        self.assertIn("/o/authorize/", url)
        self.assertIn("code_challenge=abc", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("scope=jobs", url)

"""Desktop app vs cliptude.com: where requests go and which token each one gets."""
import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from cliptude_mcp import client as c

DESKTOP = {"base_url": "http://127.0.0.1:50568", "key": "clp_desktop_local"}


class DesktopTargetTests(TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLIPTUDE_BASE_URL", "CLIPTUDE_API_KEY", "CLIPTUDE_TARGET", "CLIPTUDE_DESKTOP_DATA_DIR")}
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        for target in (patch.object(Path, "home", return_value=Path(self.home.name)),
                       patch.object(c, "stored_access_token", return_value="cloud_oauth_token")):
            target.start()
            self.addCleanup(target.stop)

    def test_running_app_is_used_with_its_own_key(self):
        with patch.object(c, "discover_desktop", return_value=DESKTOP):
            client = c.CliptudeClient(client_kind="mcp")
        self.assertEqual(client.base_url, DESKTOP["base_url"])
        self.assertEqual(client.runtime, "desktop")
        self.assertEqual(client._headers()["Authorization"], "Bearer clp_desktop_local")
        self.assertFalse(client._session_file)  # never refreshes a cloud login against the app

    def test_cloud_login_never_reaches_the_app_and_vice_versa(self):
        with patch.object(c, "discover_desktop", return_value=None):
            cloud = c.CliptudeClient()
        self.assertEqual(cloud.base_url, c.DEFAULT_SAAS_URL)
        self.assertEqual(cloud._headers()["Authorization"], "Bearer cloud_oauth_token")
        with patch.object(c, "discover_desktop", return_value={**DESKTOP, "key": ""}):
            desk = c.CliptudeClient()
        with self.assertRaisesRegex(c.CliptudeApiError, "Sign in to the Cliptude desktop app"):
            desk._headers()

    def test_desktop_chosen_but_closed_says_open_it(self):
        c.save_target("desktop")
        with patch.object(c, "discover_desktop", return_value=None):
            client = c.CliptudeClient()
        with self.assertRaisesRegex(c.CliptudeApiError, "desktop app is not running"):
            client._headers()

    def test_cloud_choice_and_cloud_key_env_skip_the_app(self):
        discover = MagicMock(return_value=DESKTOP)
        with patch.object(c, "discover_desktop", discover):
            c.save_target("cloud")
            self.assertEqual(c.resolve_connection()["runtime"], "cloud")
            c.save_target("auto")
            with patch.dict(os.environ, {"CLIPTUDE_API_KEY": "clp_cloud"}):
                conn = c.resolve_connection()
        self.assertEqual((conn["runtime"], conn["token"]), ("cloud", "clp_cloud"))
        discover.assert_not_called()

    def test_explicit_base_url_wins(self):
        with patch.dict(os.environ, {"CLIPTUDE_BASE_URL": "http://web:5021/", "CLIPTUDE_API_KEY": "clp_x"}):
            conn = c.resolve_connection()
        self.assertEqual((conn["base_url"], conn["token"], conn["runtime"]), ("http://web:5021", "clp_x", "custom"))

    def test_discovery_reads_the_apps_runtime_file(self):
        data = Path(self.home.name) / "app-data"
        data.mkdir()
        (data / "desktop-runtime.json").write_text(json.dumps({"django": 50568, "editor": 50569}))
        (data / "local-api.json").write_text(json.dumps({"key": "clp_desktop_local", "email": "a@b.c"}))
        health = MagicMock(status_code=200)
        with patch.dict(os.environ, {"CLIPTUDE_DESKTOP_DATA_DIR": str(data)}), \
                patch.object(c.httpx, "get", return_value=health) as get:
            self.assertEqual(c.discover_desktop(), DESKTOP)
            get.assert_called_once_with("http://127.0.0.1:50568/api/desktop/health/", timeout=2.0)
            get.side_effect = c.httpx.ConnectError("refused")
            self.assertIsNone(c.discover_desktop())  # stale file from a crashed app

    def test_linux_and_windows_data_folders_match_the_app(self):
        home = Path(self.home.name)
        with patch.object(c.sys, "platform", "linux"):
            self.assertEqual(c.desktop_data_dirs(), [home / ".local" / "share" / "cliptude"])
        with patch.object(c.sys, "platform", "win32"), patch.dict(os.environ, {"APPDATA": r"C:\Users\a\AppData\Roaming"}):
            self.assertEqual(c.desktop_data_dirs(), [Path(r"C:\Users\a\AppData\Roaming") / "Cliptude"])

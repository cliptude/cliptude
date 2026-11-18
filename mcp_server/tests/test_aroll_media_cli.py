"""CLI `media` (list / upload / import) and the --aroll / --include-aroll flags, and the client's A-roll sentences."""
from unittest import TestCase
from unittest.mock import patch

from cliptude_mcp import cli
from cliptude_mcp.client import CliptudeApiError, merge_aroll


class MediaCommandTests(TestCase):
    def test_list_upload_import(self):
        with patch.object(cli.CliptudeClient, "list_media", return_value={}) as listing, \
                patch.object(cli.CliptudeClient, "upload_many_to_library",
                             return_value={"uploaded": [], "failed": []}) as many, \
                patch.object(cli.CliptudeClient, "upload_to_library", return_value={}) as one, \
                patch.object(cli.CliptudeClient, "import_media", return_value={}) as imp, \
                patch.object(cli, "_print"):
            cli.main(["media", "list", "--folder", "jensen_followup", "--kind", "video"])
            cli.main(["media", "upload", "a.mp4", "b.png", "--folder", "jensen_followup"])
            cli.main(["media", "upload", "kress.mp4", "--name", "kress_cnbc.mp4"])
            cli.main(["media", "import", "https://example.com/c.mp4", "--folder", "b_roll"])
        listing.assert_called_once_with(folder="jensen_followup", kind="video", limit=50, offset=0)
        many.assert_called_once_with(["a.mp4", "b.png"], folder="jensen_followup")
        one.assert_called_once_with("kress.mp4", folder=None, name="kress_cnbc.mp4")
        imp.assert_called_once_with(url="https://example.com/c.mp4", folder="b_roll", name=None)

    def test_a_failed_file_exits_non_zero(self):
        with patch.object(cli.CliptudeClient, "upload_many_to_library",
                          return_value={"uploaded": [], "failed": [{"path": "x", "error": "no"}]}), \
                patch.object(cli, "_print"), self.assertRaises(SystemExit) as stop:
            cli.main(["media", "upload", "x.mp4"])
        self.assertEqual(stop.exception.code, 1)


class ARollFlagTests(TestCase):
    def test_estimate_parses_specs_and_include(self):
        with patch.object(cli, "_resolve_template_id", return_value="t1"), \
                patch.object(cli.CliptudeClient, "estimate_credits", return_value={}) as est, \
                patch.object(cli, "_print"):
            cli.main(["estimate", "--template", "T", "--script", "words " * 900,
                      "--aroll", '@kress_cnbc after "they refused to take any more questions"',
                      "--aroll", '{"media": "@call", "placement": "halfway", "says": "No."}',
                      "--aroll", "@plain", "--include-aroll"])
        kw = est.call_args.kwargs
        self.assertEqual(kw["aroll"], [
            {"media": "@kress_cnbc", "placement": 'after "they refused to take any more questions"'},
            {"media": "@call", "placement": "halfway", "says": "No."},
            "@plain",
        ])
        self.assertTrue(kw["include_aroll"])

    def test_plan_job_takes_aroll_but_not_include(self):
        with patch.object(cli, "_resolve_template_id", return_value="t1"), \
                patch.object(cli, "_create_reviewed", return_value={}) as create, \
                patch.object(cli, "_print"):
            cli.main(["plan-job", "--template", "T", "--title", "X", "--script", "words " * 900,
                      "--aroll", '@kress after "a line"'])
            with self.assertRaises(SystemExit):
                cli.main(["plan-job", "--template", "T", "--title", "X", "--script", "w", "--include-aroll"])
        kw = create.call_args.kwargs
        self.assertEqual(kw["aroll"], [{"media": "@kress", "placement": 'after "a line"'}])
        self.assertNotIn("include_aroll", kw)

    def test_bad_json(self):
        with patch.object(cli, "_resolve_template_id", return_value="t1"), \
                patch.object(cli.CliptudeClient, "estimate_credits", return_value={}) as est, \
                patch.object(cli, "_print"), self.assertRaises(SystemExit):
            cli.main(["estimate", "--template", "T", "--script", "x", "--aroll", "{not json"])
        est.assert_not_called()


class MergeARollTests(TestCase):
    def test_sentences(self):
        out = merge_aroll("Visual notes.", [
            {"media": "@kress_cnbc", "after": "And then they refused to take any more questions about it."},
            {"media": "phone call.mp3", "placement": "halfway", "says": "We will not. Period."},
        ])
        self.assertIn('A-roll: @kress_cnbc after "And then they refused to take any more questions about it".', out)
        self.assertIn('A-roll: @phone_call halfway says "We will not Period".', out)
        with self.assertRaises(CliptudeApiError):
            merge_aroll("", [f"@a{i}" for i in range(5)])
        self.assertEqual(merge_aroll(None, None), None)

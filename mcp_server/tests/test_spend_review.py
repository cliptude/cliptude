"""Calls that spend credits carry the review_token, and the CLI shows the review and asks before resending."""

import io
from unittest import TestCase
from unittest.mock import patch

from cliptude_mcp import cli
from cliptude_mcp.client import CliptudeApiError, CliptudeClient

REVIEW = {
    "error": "review_required",
    "review": {"action": "continue_job", "summary": "continue_job builds the full video and charges about 160 credits.",
               "settings": [{"label": "Job", "value": "VO done"}],
               "credits": {"credits_required": 160, "credits_available": 900},
               "acknowledgements_required": []},
    "review_token": "tok-1",
}


def _client():
    return CliptudeClient(base_url="https://cliptude.com", api_key="clp_test")


class ClientSendsTheTokenTests(TestCase):
    def _body(self, call):
        client = _client()
        with patch.object(client, "request", return_value={}) as req, \
                patch.object(client, "get_editor_project", return_value={"project": {"revision": 3}}):
            call(client)
        return req.call_args.kwargs.get("json_body") or {}

    def test_every_spend_call_sends_the_review_token(self):
        calls = {
            "continue_job": lambda c: c.continue_job("11111111-1111-4111-8111-111111111111", review_token="t"),
            "render_product_video": lambda c: c.render_product_video("11111111-1111-4111-8111-111111111111", review_token="t"),
            "draft_product_script": lambda c: c.draft_product_script("pid", plan=True, review_token="t",
                                                                     acknowledge=["no_music"]),
            "run_agent": lambda c: c.run_agent(12, prompt="Swap clip-1", review_token="t"),
            "render_project": lambda c: c.render_project(12, review_token="t"),
        }
        for name, call in calls.items():
            with self.subTest(name):
                self.assertEqual(self._body(call)["review_token"], "t")
        self.assertEqual(self._body(calls["draft_product_script"])["acknowledge"], ["no_music"])

    def test_without_a_token_nothing_extra_is_sent(self):
        self.assertNotIn("review_token", self._body(lambda c: c.run_agent(12, prompt="Swap clip-1")))
        self.assertEqual(self._body(lambda c: c.render_project(12)), {})


class CliAsksBeforeSpendingTests(TestCase):
    def _run(self, argv, answer, *, tty=True):
        calls = []

        def continue_job(job_id, *, thumbnail=None, review_token=None):
            calls.append(review_token)
            if review_token != "tok-1":
                raise CliptudeApiError("Review required", status=428, payload=REVIEW)
            return {"id": job_id, "status": "queued"}

        client = _client()
        client.continue_job = continue_job
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli, "CliptudeClient", return_value=client), \
                patch("sys.stdin.isatty", return_value=tty), patch("builtins.input", return_value=answer), \
                patch("sys.stdout", out), patch("sys.stderr", err):
            try:
                cli.main(argv)
            except SystemExit as exc:
                return calls, out.getvalue(), err.getvalue(), exc.code
        return calls, out.getvalue(), err.getvalue(), 0

    def test_continue_shows_the_review_and_resends_after_yes(self):
        calls, out, err, code = self._run(["continue", "job-1"], "y")
        self.assertEqual(code, 0)
        self.assertEqual(calls, [None, "tok-1"])
        self.assertIn("160 credits", err)
        self.assertIn('"queued"', out)

    def test_no_spends_nothing(self):
        calls, _out, _err, code = self._run(["continue", "job-1"], "n")
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [None])

    def test_without_a_terminal_the_review_is_printed_and_nothing_is_resent(self):
        calls, out, _err, code = self._run(["continue", "job-1"], "y", tty=False)
        self.assertEqual(code, 428)
        self.assertEqual(calls, [None])
        self.assertIn("tok-1", out)

    def test_a_given_token_is_sent_at_once(self):
        calls, _out, _err, code = self._run(["continue", "job-1", "--review-token", "tok-1"], "n")
        self.assertEqual((code, calls), (0, ["tok-1"]))

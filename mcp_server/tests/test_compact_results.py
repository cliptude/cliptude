from unittest import TestCase

from cliptude_mcp.server import (
    _compact_agent_run,
    _compact_editor_project,
    _compact_narration,
)


class CompactResultsTests(TestCase):
    def test_editor_project_drops_the_spoken_event_index_but_keeps_the_rest(self):
        data = {
            "project": {
                "id": 7,
                "timeline": [{"id": "a"}],
                "voice_timing": {
                    "fingerprint": "abc",
                    "spokenEventIndex": {"words": [{"w": "x"}] * 500, "numeric_events": []},
                },
            }
        }
        out = _compact_editor_project(data)
        timing = out["project"]["voice_timing"]
        self.assertEqual(timing["fingerprint"], "abc")
        self.assertNotIn("spokenEventIndex", timing)
        self.assertEqual(timing["spoken_words"], 500)
        self.assertEqual(out["project"]["timeline"], [{"id": "a"}])
        # the input is not mutated
        self.assertIn("spokenEventIndex", data["project"]["voice_timing"])

    def test_editor_project_without_voice_timing_is_returned_as_is(self):
        data = {"project": {"id": 1}}
        self.assertEqual(_compact_editor_project(data), data)
        self.assertEqual(_compact_editor_project({"error": "x"}), {"error": "x"})

    def test_agent_run_summarises_voice_segment_arrays_in_operations_and_messages(self):
        op = {"op": "replace_voiceover", "src": "/media/a.mp3", "voice_segments": [{"t": 1}] * 300, "fingerprint": "f"}
        data = {
            "run": {"id": "r", "status": "completed", "applied_operations": [op]},
            "messages": [{"role": "assistant", "content": "done", "metadata": {"operations": [op]}}],
        }
        out = _compact_agent_run(data)
        applied = out["run"]["applied_operations"][0]
        self.assertEqual(applied["voice_segments"], "<300 entries omitted>")
        self.assertEqual(applied["src"], "/media/a.mp3")
        self.assertEqual(out["messages"][0]["metadata"]["operations"][0]["voice_segments"], "<300 entries omitted>")
        self.assertEqual(len(data["run"]["applied_operations"][0]["voice_segments"]), 300)

    def test_agent_run_leaves_small_operations_alone(self):
        op = {"op": "replace_media", "item_id": "scene-1", "src": "/media/x.mp4"}
        out = _compact_agent_run({"run": {"applied_operations": [op]}, "messages": []})
        self.assertEqual(out["run"]["applied_operations"], [op])

    def test_narration_drops_word_timings_unless_asked(self):
        data = {"narration_digest": "sha1:x", "sentences": [{"id": 0, "text": "Hi.", "start": 0, "end": 1, "words": [{"w": "Hi."}]}]}
        compact = _compact_narration(data, include_words=False)
        self.assertNotIn("words", compact["sentences"][0])
        self.assertTrue(compact["words_omitted"])
        self.assertEqual(compact["sentences"][0]["text"], "Hi.")
        self.assertIs(_compact_narration(data, include_words=True), data)

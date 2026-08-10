
import unittest

from stream_mux import CaptionMux, StreamEvent

class CaptionMuxTests(unittest.TestCase):
    def test_incremental_prefix_only_emits_suffix(self):
        mux = CaptionMux()
        self.assertEqual(mux.ingest(StreamEvent(1, "Hel")), "Hel")
        self.assertEqual(mux.ingest(StreamEvent(2, "Hello")), "lo")
        self.assertEqual(mux.visible, "Hello")

    def test_exact_replay_is_suppressed(self):
        mux = CaptionMux()
        mux.ingest(StreamEvent(4, "Ready"))
        self.assertEqual(mux.ingest(StreamEvent(4, "Ready")), "")
        self.assertEqual(mux.visible, "Ready")

    def test_reconnect_prefix_can_extend_without_duplication(self):
        mux = CaptionMux()
        mux.ingest(StreamEvent(10, "The agent is"))
        self.assertEqual(
            mux.ingest(StreamEvent(2, "The agent is working")), " working"
        )
        self.assertEqual(mux.visible, "The agent is working")

    def test_new_sentence_appends_normally(self):
        mux = CaptionMux()
        mux.ingest(StreamEvent(1, "Done."))
        self.assertEqual(
            mux.ingest(StreamEvent(2, " Next step.")), " Next step."
        )
        self.assertEqual(mux.visible, "Done. Next step.")

if __name__ == "__main__":
    unittest.main()

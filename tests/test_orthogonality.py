from pathlib import Path

from jarvisbench.settings.multi_agent import MultiAgentSetting
from jarvisbench.settings.single_agent import SingleAgentSetting
from jarvisbench.tracks.user_interaction import DeterministicReplayResponder, UserInteractionTrack


def test_single_has_no_fake_manager():
    result = SingleAgentSetting().dry_run()
    assert result.execution_nodes == ("worker-0",)
    assert result.manager_is_jarvis is False


def test_multi_preserves_native_gateway_protocol():
    result = MultiAgentSetting().dry_run()
    assert result.execution_nodes == ("parent",)
    assert result.gateway_required is True
    assert MultiAgentSetting.protocol() == (
        "parent_delegation",
        "children_complete",
        "parent_integration",
    )


def test_track2_replay_has_four_turns_and_no_control_handle(tmp_path: Path):
    source = Path(__file__).parent / "fixtures/replay/bounded_episode.jsonl"
    output = tmp_path / "conversation.json"
    track = UserInteractionTrack(DeterministicReplayResponder())
    assert not hasattr(track, "control_store")
    turns = track.run(source, output)
    assert [(turn.checkpoint, turn.kind) for turn in turns] == [
        ("early", "general"),
        ("early", "follow_up"),
        ("late", "general"),
        ("late", "follow_up"),
    ]
    assert turns[0].visible_frame_index < turns[2].visible_frame_index
    assert output.is_file()


def test_track2_questioner_never_receives_trace_frames(tmp_path: Path):
    class SpyQuestioner:
        calls = []

        def general(self, checkpoint, opening_brief):
            self.calls.append(("general", checkpoint, opening_brief))
            return "Status?"

        def follow_up(self, checkpoint, question, answer, opening_brief):
            self.calls.append(("follow", checkpoint, question, answer, opening_brief))
            return "What changed?"

    source = Path(__file__).parent / "fixtures/replay/bounded_episode.jsonl"
    questioner = SpyQuestioner()
    track = UserInteractionTrack(DeterministicReplayResponder(), questioner)
    track.run(source, tmp_path / "conversation.json", opening_brief="Public opening brief")
    assert len(questioner.calls) == 4
    assert all("Public opening brief" in call for call in questioner.calls)
    assert all(not any(hasattr(value, "elapsed_ms") for value in call) for call in questioner.calls)

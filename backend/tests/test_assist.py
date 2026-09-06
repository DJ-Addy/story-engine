"""Unit tests for the edit assistant's pure half (app.render.assist).

No network, no provider, no repository: everything here is prompt building,
tolerant parsing, and the screening pass that decides which of a model's
proposed ops a user is ever allowed to see. The screening tests are the point —
a proposal with an Apply button must be a proposal that applies.
"""

import json

import pytest

from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize
from app.render.assist import (
    MAX_PROPOSED_EDITS,
    AssistTurn,
    build_assist_prompt,
    describe,
    parse_assist_reply,
    screen_edits,
)
from app.render.audio.model import SceneRenderSettings
from app.render.timeline_edits import TimelineEditError, apply_edits
from app.shotlist.schema import SceneShotList

DEFAULT_SETTINGS = SceneRenderSettings()

# Scene 2 of the fixture, which is the interesting one: dialogue on 2, 3, 5, 6
# and 8, action on 1 and 7, a parenthetical on 4 and a transition on 9.
SCENE = 2


@pytest.fixture
def graph(sample_fountain):
    return normalize(parse_fountain(sample_fountain))


@pytest.fixture
def scene(graph):
    return next(s for s in graph.scenes if s.ordinal == SCENE)


@pytest.fixture
def shotlist():
    common = {
        "axis_side": "a",
        "lens_mm": 50,
        "camera_height": "eye",
        "movement": "static",
        "eyeline": "none",
    }
    return SceneShotList(
        scene_ordinal=SCENE,
        action_axis="TOM to MARA across the doorway",
        shots=[
            {
                "ordinal": 1,
                "size": "ws",
                "subjects": ["TOM", "MARA"],
                "covers_lines": [1, 2],
                "intent": "The door opens on the storm",
                **common,
            },
            {
                "ordinal": 2,
                "size": "ms",
                "subjects": ["MARA"],
                "covers_lines": [3, 5, 6],
                "intent": "Mara makes her case",
                **common,
            },
        ],
    )


def screen(graph, candidates, shotlist=None, settings=DEFAULT_SETTINGS):
    return screen_edits(graph, SCENE, shotlist, settings, candidates)


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #
class TestPrompt:
    def test_every_line_reaches_the_model_with_its_speaker_and_emotion(
        self, graph, scene
    ):
        _, user = build_assist_prompt(
            graph, scene, None, DEFAULT_SETTINGS, "who says what?"
        )
        for line in scene.lines:
            assert f"{line.ordinal} | {line.kind} |" in user
        assert "2 | dialogue | TOM | - | You shouldn't be out in this." in user
        assert "Characters in the story graph: MARA, TOM" in user
        assert "pacing 1, ambience duck 0.5" in user
        assert "who says what?" in user

    def test_a_scene_with_no_shot_list_says_shot_ops_are_unavailable(
        self, graph, scene
    ):
        _, user = build_assist_prompt(graph, scene, None, DEFAULT_SETTINGS, "add a shot")
        assert "No shot list has been authored" in user
        assert "insert_shot" in user

    def test_the_shot_list_reaches_the_model_when_there_is_one(
        self, graph, scene, shotlist
    ):
        _, user = build_assist_prompt(
            graph, scene, shotlist, DEFAULT_SETTINGS, "recut this"
        )
        assert "1 | ws | TOM, MARA | covers 1, 2 | The door opens on the storm" in user

    def test_the_system_prompt_names_the_whole_closed_vocabulary(self, graph, scene):
        system, _ = build_assist_prompt(graph, scene, None, DEFAULT_SETTINGS, "hi")
        for op in (
            "reassign_line_character",
            "set_line_emotion",
            "set_scene_pacing",
            "set_ambience_duck",
            "insert_shot",
            "set_shot_coverage",
        ):
            assert op in system
        assert "whispering" in system  # the emotion vocabulary, verbatim

    def test_history_is_replayed_so_the_turn_has_context(self, graph, scene):
        _, user = build_assist_prompt(
            graph,
            scene,
            None,
            DEFAULT_SETTINGS,
            "do it then",
            history=[
                AssistTurn(role="user", content="is Tom too warm here?"),
                AssistTurn(
                    role="assistant", content="I would try 'serious' on line 5."
                ),
            ],
        )
        assert "user: is Tom too warm here?" in user
        assert "assistant: I would try 'serious' on line 5." in user


# --------------------------------------------------------------------------- #
# Parsing what the model said
# --------------------------------------------------------------------------- #
class TestParse:
    def test_plain_envelope(self):
        raw = json.dumps({"reply": "Try this.", "edits": [{"op": "set_scene_pacing"}]})
        reply, candidates, complaints = parse_assist_reply(raw)
        assert reply == "Try this."
        assert candidates == [{"op": "set_scene_pacing"}]
        assert complaints == []

    def test_markdown_fences_are_stripped(self):
        raw = '```json\n{"reply": "Sure.", "edits": []}\n```'
        reply, candidates, _ = parse_assist_reply(raw)
        assert reply == "Sure."
        assert candidates == []

    def test_prose_instead_of_json_is_still_shown_but_proposes_nothing(self):
        reply, candidates, complaints = parse_assist_reply(
            "I would soften Mara's line, but I cannot see the audio."
        )
        assert reply.startswith("I would soften")
        assert candidates == []
        assert complaints and "JSON envelope" in complaints[0]

    def test_edits_that_are_not_a_list_are_refused_not_coerced(self):
        raw = json.dumps({"reply": "ok", "edits": {"op": "set_scene_pacing"}})
        _, candidates, complaints = parse_assist_reply(raw)
        assert candidates == []
        assert any("not a list" in c for c in complaints)

    def test_more_ops_than_the_cap_are_truncated_and_the_truncation_is_reported(self):
        edits = [{"op": "set_scene_pacing", "pacing": 1.1}] * (MAX_PROPOSED_EDITS + 3)
        _, candidates, complaints = parse_assist_reply(
            json.dumps({"reply": "lots", "edits": edits})
        )
        assert len(candidates) == MAX_PROPOSED_EDITS
        assert any(str(MAX_PROPOSED_EDITS) in c for c in complaints)


# --------------------------------------------------------------------------- #
# Screening: what the user is allowed to see
# --------------------------------------------------------------------------- #
class TestScreeningDropsWhatWouldNotApply:
    def test_a_hallucinated_line_ordinal_never_reaches_the_user(self, graph):
        batch = screen(
            graph,
            [
                {"op": "set_line_emotion", "line_ordinal": 40, "emotion": "sad"},
                {"op": "set_line_emotion", "line_ordinal": 2, "emotion": "serious"},
            ],
        )
        assert [p.edit.line_ordinal for p in batch.edits] == [2]
        assert any("line 40 not found" in d for d in batch.dropped)

    def test_an_invented_op_name_is_dropped_by_the_schema(self, graph):
        batch = screen(graph, [{"op": "delete_scene", "scene_ordinal": 2}])
        assert batch.edits == []
        assert any("delete_scene" in d for d in batch.dropped)

    def test_an_emotion_outside_the_vocabulary_is_dropped(self, graph):
        batch = screen(
            graph, [{"op": "set_line_emotion", "line_ordinal": 2, "emotion": "smug"}]
        )
        assert batch.edits == []
        assert any("unknown emotion" in d for d in batch.dropped)

    def test_an_emotion_on_a_line_that_is_never_spoken_is_dropped(self, graph):
        # Line 4 is a parenthetical: it never reaches TTS, so an emotion on it
        # would be a setting with no audible consequence.
        batch = screen(
            graph, [{"op": "set_line_emotion", "line_ordinal": 4, "emotion": "calm"}]
        )
        assert batch.edits == []
        assert any("never spoken" in d for d in batch.dropped)

    def test_reassigning_an_action_line_is_dropped(self, graph):
        batch = screen(
            graph,
            [
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 1,
                    "character_name": "TOM",
                }
            ],
        )
        assert batch.edits == []
        assert any("only dialogue has a speaker" in d for d in batch.dropped)

    def test_an_unknown_character_is_dropped_unless_the_model_says_it_is_new(
        self, graph
    ):
        batch = screen(
            graph,
            [
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 2,
                    "character_name": "GRETA",
                }
            ],
        )
        assert batch.edits == []
        assert any("unknown character" in d for d in batch.dropped)

        allowed = screen(
            graph,
            [
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 2,
                    "character_name": "GRETA",
                    "allow_new": True,
                }
            ],
        )
        assert len(allowed.edits) == 1

    def test_an_op_that_would_change_nothing_is_dropped(self, graph):
        # Scene 1 line 3 already carries "whispering" from its parenthetical.
        batch = screen_edits(
            graph,
            1,
            None,
            DEFAULT_SETTINGS,
            [
                {"op": "set_line_emotion", "line_ordinal": 3, "emotion": "whispering"},
                {"op": "set_scene_pacing", "pacing": 1.0},
            ],
        )
        assert batch.edits == []
        assert len(batch.dropped) == 2
        assert all("would change nothing" in d for d in batch.dropped)

    def test_a_shot_op_in_a_scene_with_no_shot_list_is_dropped(self, graph):
        batch = screen(
            graph, [{"op": "set_shot_coverage", "shot_ordinal": 1, "covers_lines": [2]}]
        )
        assert batch.edits == []
        assert any("no shot list" in d for d in batch.dropped)

    def test_a_shot_covering_a_line_the_scene_does_not_have_is_dropped(
        self, graph, shotlist
    ):
        batch = screen(
            graph,
            [{"op": "set_shot_coverage", "shot_ordinal": 2, "covers_lines": [3, 99]}],
            shotlist=shotlist,
        )
        assert batch.edits == []
        assert any("[99]" in d for d in batch.dropped)


class TestScreeningKeepsTheBatchApplicable:
    def test_the_surviving_batch_applies_all_at_once(self, graph):
        batch = screen(
            graph,
            [
                {"op": "set_line_emotion", "line_ordinal": 2, "emotion": "serious"},
                {"op": "set_line_emotion", "line_ordinal": 99, "emotion": "sad"},
                {"op": "set_scene_pacing", "pacing": 0.85},
            ],
        )
        assert len(batch.edits) == 2
        result = apply_edits(
            graph, SCENE, None, DEFAULT_SETTINGS, [p.edit for p in batch.edits]
        )
        assert result.graph_changed and result.settings_changed
        assert result.invalidates_audio

    def test_screening_leaves_the_callers_state_untouched(self, graph, shotlist):
        before = graph.model_dump()
        shots_before = shotlist.model_dump()
        screen(
            graph,
            [
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 2,
                    "character_name": "MARA",
                },
                {"op": "set_shot_coverage", "shot_ordinal": 2, "covers_lines": [3]},
            ],
            shotlist=shotlist,
        )
        assert graph.model_dump() == before
        assert shotlist.model_dump() == shots_before

    def test_each_op_is_judged_against_the_state_the_one_before_it_leaves(self, graph):
        # The same reassignment twice: the second is a no-op *because* the first
        # was kept, which is only visible if screening carries the running state.
        batch = screen(
            graph,
            [
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 2,
                    "character_name": "MARA",
                },
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 2,
                    "character_name": "MARA",
                },
            ],
        )
        assert len(batch.edits) == 1
        assert any("would change nothing" in d for d in batch.dropped)

    def test_an_op_that_only_becomes_valid_after_an_insert_is_kept(
        self, graph, shotlist
    ):
        # Inserting at the head renumbers the list, so shot 3 exists only after
        # the insert. Screening against the running state sees that; screening
        # against the original state would have dropped it.
        batch = screen(
            graph,
            [
                {
                    "op": "insert_shot",
                    "after_ordinal": None,
                    "shot": {
                        "size": "cu",
                        "subjects": ["TOM"],
                        "covers_lines": [2],
                        "intent": "Tom in the doorway",
                    },
                },
                {"op": "set_shot_coverage", "shot_ordinal": 3, "covers_lines": [5, 6]},
            ],
            shotlist=shotlist,
        )
        assert [p.edit.op for p in batch.edits] == ["insert_shot", "set_shot_coverage"]
        result = apply_edits(
            graph, SCENE, shotlist, DEFAULT_SETTINGS, [p.edit for p in batch.edits]
        )
        assert [s.covers_lines for s in result.shotlist.shots] == [[2], [1, 2], [5, 6]]


# --------------------------------------------------------------------------- #
# The wording the panel renders
# --------------------------------------------------------------------------- #
class TestSummaries:
    def test_reassignment_names_both_speakers(self, graph):
        batch = screen(
            graph,
            [
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 2,
                    "character_name": "mara",
                }
            ],
        )
        assert batch.edits[0].summary == "Reassign line 2 from TOM to MARA"

    def test_un_attributing_a_line_says_narrator(self, graph):
        batch = screen(
            graph,
            [
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 2,
                    "character_name": None,
                }
            ],
        )
        assert batch.edits[0].summary == "Reassign line 2 from TOM to the narrator"

    def test_emotion_carries_the_value_it_replaces(self, graph):
        batch = screen(
            graph, [{"op": "set_line_emotion", "line_ordinal": 2, "emotion": "urgent"}]
        )
        assert batch.edits[0].summary == "Set line 2 delivery to urgent (was neutral)"

    def test_pacing_says_which_way_it_moves_and_what_it_does_not_touch(self, graph):
        batch = screen(graph, [{"op": "set_scene_pacing", "pacing": 0.8}])
        summary = batch.edits[0].summary
        assert summary.startswith("Tighten scene pacing to 0.8x (was 1x)")
        assert "never the clips" in summary

    def test_duck_is_a_percentage(self, graph):
        batch = screen(graph, [{"op": "set_ambience_duck", "depth": 0.8}])
        assert batch.edits[0].summary == "Duck ambience 80% under speech (was 50%)"

    def test_insert_names_size_subject_position_and_coverage(self, graph, shotlist):
        batch = screen(
            graph,
            [
                {
                    "op": "insert_shot",
                    "after_ordinal": 1,
                    "shot": {
                        "size": "cu",
                        "subjects": ["MARA"],
                        "covers_lines": [3],
                        "intent": "Her reaction",
                    },
                }
            ],
            shotlist=shotlist,
        )
        assert (
            batch.edits[0].summary
            == "Insert a CU of MARA after shot 1, covering line 3"
        )

    def test_coverage_says_what_it_was(self, graph, shotlist):
        batch = screen(
            graph,
            [{"op": "set_shot_coverage", "shot_ordinal": 2, "covers_lines": [5, 6]}],
            shotlist=shotlist,
        )
        assert (
            batch.edits[0].summary
            == "Repoint shot 2 to cover lines 5, 6 (was lines 3, 5, 6)"
        )

    def test_emptying_coverage_is_called_uncovering(self, graph, shotlist):
        batch = screen(
            graph,
            [{"op": "set_shot_coverage", "shot_ordinal": 1, "covers_lines": []}],
            shotlist=shotlist,
        )
        assert batch.edits[0].summary == "Uncover shot 1 (was lines 1, 2)"


# --------------------------------------------------------------------------- #
# Undo
# --------------------------------------------------------------------------- #
class TestUndo:
    def test_the_inverse_restores_every_value_the_batch_changed(self, graph):
        batch = screen(
            graph,
            [
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 2,
                    "character_name": "MARA",
                },
                {"op": "set_line_emotion", "line_ordinal": 3, "emotion": "urgent"},
                {"op": "set_scene_pacing", "pacing": 0.7},
            ],
        )
        forward = apply_edits(
            graph, SCENE, None, DEFAULT_SETTINGS, [p.edit for p in batch.edits]
        )
        back = apply_edits(
            forward.graph, SCENE, forward.shotlist, forward.settings, batch.undo
        )

        def state(g):
            scene = next(s for s in g.scenes if s.ordinal == SCENE)
            return [(l.ordinal, l.character_name, l.emotion) for l in scene.lines]

        assert state(back.graph) == state(graph)
        assert back.settings == DEFAULT_SETTINGS
        assert [c.line_count for c in back.graph.characters] == [
            c.line_count for c in graph.characters
        ]

    def test_undo_restores_the_speaker_but_not_the_provenance(self, graph):
        """A human touched the line; undoing the value does not unmake that."""
        batch = screen(
            graph,
            [
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 2,
                    "character_name": "MARA",
                }
            ],
        )
        forward = apply_edits(
            graph, SCENE, None, DEFAULT_SETTINGS, [p.edit for p in batch.edits]
        )
        back = apply_edits(forward.graph, SCENE, None, DEFAULT_SETTINGS, batch.undo)
        line = next(
            l
            for l in next(s for s in back.graph.scenes if s.ordinal == SCENE).lines
            if l.ordinal == 2
        )
        assert line.character_name == "TOM"
        assert line.attribution_source == "manual"
        assert line.attribution_confidence == 1.0

    def test_undo_runs_last_op_first(self, graph):
        batch = screen(
            graph,
            [
                {"op": "set_scene_pacing", "pacing": 0.7},
                {"op": "set_ambience_duck", "depth": 0.9},
            ],
        )
        assert [e.op for e in batch.undo] == ["set_ambience_duck", "set_scene_pacing"]

    def test_undoing_a_reassignment_may_reintroduce_a_dropped_character(self, graph):
        # Scene 3 line 2 is MARA's only line there, but she speaks elsewhere;
        # allow_new is set regardless, because an inverse that cannot be applied
        # is not an undo.
        batch = screen(
            graph,
            [
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 2,
                    "character_name": "MARA",
                }
            ],
        )
        assert batch.undo[0].allow_new is True
        assert batch.undo[0].character_name == "TOM"

    def test_a_batch_with_an_insert_is_honestly_not_undoable(self, graph, shotlist):
        batch = screen(
            graph,
            [
                {"op": "set_scene_pacing", "pacing": 0.7},
                {
                    "op": "insert_shot",
                    "after_ordinal": 1,
                    "shot": {
                        "size": "cu",
                        "subjects": ["MARA"],
                        "covers_lines": [3],
                        "intent": "Her reaction",
                    },
                },
            ],
            shotlist=shotlist,
        )
        assert len(batch.edits) == 2
        assert batch.undo == []
        assert batch.undo_summary == []
        assert "no op that removes a shot" in batch.undo_blocked_by

    def test_undo_summaries_are_worded_for_the_panel(self, graph):
        batch = screen(
            graph, [{"op": "set_line_emotion", "line_ordinal": 2, "emotion": "urgent"}]
        )
        assert batch.undo_summary == ["Set line 2 delivery to neutral (was urgent)"]


# --------------------------------------------------------------------------- #
# Guards on the module's own contract
# --------------------------------------------------------------------------- #
def test_screening_an_unknown_scene_proposes_nothing(graph):
    batch = screen_edits(graph, 99, None, DEFAULT_SETTINGS, [{"op": "set_scene_pacing", "pacing": 2}])
    assert batch.edits == []
    assert "scene 99 is not in this story graph" in batch.dropped[0]


def test_describe_and_the_engine_agree_on_what_is_applicable(graph, scene):
    """Anything `describe` can word, `apply_edits` can apply — and vice versa."""
    batch = screen(
        graph, [{"op": "set_line_emotion", "line_ordinal": 2, "emotion": "sad"}]
    )
    edit = batch.edits[0].edit
    assert describe(edit, scene, None, DEFAULT_SETTINGS) == batch.edits[0].summary
    with pytest.raises(TimelineEditError):
        apply_edits(graph, 99, None, DEFAULT_SETTINGS, [edit])

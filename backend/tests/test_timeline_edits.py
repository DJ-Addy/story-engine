"""Unit tests for the pure timeline edit ops (no API, no repo, no audio)."""

import pytest

from app.ingest.elements import (
    AttributedLine,
    NormalizedCharacter,
    NormalizedScene,
    StoryGraph,
)
from app.render.audio import dsp
from app.render.audio.model import SceneRenderSettings, SpeechClip
from app.render.audio.timing import plan_speech_bus
from app.render.timeline_edits import (
    InsertShot,
    NewShot,
    ReassignLineCharacter,
    SetAmbienceDuck,
    SetLineEmotion,
    SetScenePacing,
    SetShotCoverage,
    TimelineEditError,
    apply_edits,
)
from app.shotlist.schema import SceneShotList, ShotSpec

DEFAULTS = SceneRenderSettings()


def _graph() -> StoryGraph:
    scene = NormalizedScene(
        ordinal=1,
        slugline="INT. LIGHTHOUSE - NIGHT",
        interior=True,
        location="LIGHTHOUSE",
        time_of_day="NIGHT",
        lines=[
            AttributedLine(ordinal=1, kind="action", text="Rain hammers the glass."),
            AttributedLine(
                ordinal=2,
                kind="dialogue",
                text="You should not be out.",
                character_name="TOM",
                attribution_confidence=1.0,
                attribution_source="cue",
            ),
            AttributedLine(
                ordinal=3,
                kind="dialogue",
                text="Then tonight it burns.",
                character_name="MARA",
                attribution_confidence=1.0,
                attribution_source="cue",
            ),
            AttributedLine(ordinal=4, kind="transition", text="CUT TO:"),
        ],
    )
    return StoryGraph(
        scenes=[scene],
        characters=[
            NormalizedCharacter(canonical_name="TOM", line_count=1),
            NormalizedCharacter(canonical_name="MARA", line_count=1),
        ],
    )


def _shotlist() -> SceneShotList:
    common = {
        "axis_side": "a",
        "lens_mm": 50,
        "camera_height": "eye",
        "movement": "static",
        "eyeline": "none",
    }
    return SceneShotList(
        scene_ordinal=1,
        action_axis="MARA to TOM",
        shots=[
            ShotSpec(
                ordinal=1, size="ws", subjects=["TOM"], covers_lines=[1, 2],
                intent="Establish the room", **common,
            ),
            ShotSpec(
                ordinal=2, size="ms", subjects=["MARA"], covers_lines=[3],
                intent="Mara answers", **common,
            ),
        ],
    )


def _apply(edits, graph=None, shotlist=None, settings=DEFAULTS):
    return apply_edits(graph or _graph(), 1, shotlist, settings, edits)


def _characters(result) -> dict[str, int]:
    return {c.canonical_name: c.line_count for c in result.graph.characters}


# -- reassign_line_character -------------------------------------------------


def test_reassign_stamps_manual_attribution_and_recounts_characters():
    graph = _graph()
    graph.scenes[0].lines.append(
        AttributedLine(
            ordinal=5, kind="dialogue", text="Fetch the oil.", character_name="MARA",
            attribution_confidence=1.0, attribution_source="cue",
        )
    )
    result = _apply([ReassignLineCharacter(op="reassign_line_character",
                                           line_ordinal=3, character_name="TOM")], graph)

    line = result.graph.scenes[0].lines[2]
    assert line.character_name == "TOM"
    # Human corrections are ground truth, exactly like the manual line patch.
    assert line.attribution_source == "manual"
    assert line.attribution_confidence == 1.0
    # MARA still speaks line 5, so she stays — with a recomputed count.
    assert _characters(result) == {"TOM": 2, "MARA": 1}
    assert result.graph_changed and result.invalidates_audio
    assert "reassigned from MARA to TOM" in result.stale_reasons[0]


def test_reassign_drops_a_character_left_with_no_lines():
    result = _apply([ReassignLineCharacter(op="reassign_line_character",
                                           line_ordinal=3, character_name="TOM")])
    assert _characters(result) == {"TOM": 2}


def test_reassign_to_none_unattributes_the_line():
    result = _apply([ReassignLineCharacter(op="reassign_line_character",
                                           line_ordinal=3, character_name=None)])
    assert result.graph.scenes[0].lines[2].character_name is None
    assert _characters(result) == {"TOM": 1}


def test_reassign_uppercases_to_the_canonical_name():
    result = _apply([ReassignLineCharacter(op="reassign_line_character",
                                           line_ordinal=3, character_name=" tom ")])
    assert result.graph.scenes[0].lines[2].character_name == "TOM"
    assert set(_characters(result)) == {"TOM"}


def test_reassign_to_unknown_character_is_rejected():
    with pytest.raises(TimelineEditError, match="unknown character"):
        _apply([ReassignLineCharacter(op="reassign_line_character",
                                      line_ordinal=3, character_name="TOMM")])


def test_reassign_to_new_character_when_allowed():
    result = _apply([ReassignLineCharacter(op="reassign_line_character", line_ordinal=3,
                                           character_name="KEEPER", allow_new=True)])
    assert _characters(result) == {"TOM": 1, "KEEPER": 1}


def test_reassign_non_dialogue_line_is_rejected():
    with pytest.raises(TimelineEditError, match="only dialogue has a speaker"):
        _apply([ReassignLineCharacter(op="reassign_line_character",
                                      line_ordinal=1, character_name="TOM")])


def test_unknown_line_ordinal_is_rejected_with_its_batch_index():
    with pytest.raises(TimelineEditError) as excinfo:
        _apply([
            SetLineEmotion(op="set_line_emotion", line_ordinal=2, emotion="angry"),
            ReassignLineCharacter(op="reassign_line_character",
                                  line_ordinal=99, character_name="TOM"),
        ])
    assert excinfo.value.index == 1
    assert "line 99 not found" in str(excinfo.value)


def test_a_failed_batch_leaves_the_caller_state_untouched():
    graph = _graph()
    with pytest.raises(TimelineEditError):
        _apply([
            ReassignLineCharacter(op="reassign_line_character",
                                  line_ordinal=3, character_name="TOM"),
            SetLineEmotion(op="set_line_emotion", line_ordinal=3, emotion="furious"),
        ], graph)
    assert graph.scenes[0].lines[2].character_name == "MARA"
    assert [c.line_count for c in graph.characters] == [1, 1]


def test_reassign_to_the_same_speaker_changes_nothing():
    result = _apply([ReassignLineCharacter(op="reassign_line_character",
                                           line_ordinal=3, character_name="MARA")])
    assert not result.graph_changed
    assert result.stale_reasons == []


# -- set_line_emotion --------------------------------------------------------


def test_set_and_clear_line_emotion():
    result = _apply([SetLineEmotion(op="set_line_emotion", line_ordinal=3, emotion="Angry")])
    assert result.graph.scenes[0].lines[2].emotion == "angry"
    assert result.invalidates_audio

    cleared = apply_edits(result.graph, 1, None, DEFAULTS,
                          [SetLineEmotion(op="set_line_emotion", line_ordinal=3, emotion=None)])
    assert cleared.graph.scenes[0].lines[2].emotion is None


def test_unknown_emotion_is_rejected():
    with pytest.raises(TimelineEditError, match="unknown emotion"):
        _apply([SetLineEmotion(op="set_line_emotion", line_ordinal=3, emotion="furious")])


def test_emotion_on_an_unspoken_line_is_rejected():
    # Transitions never reach TTS, so an emotion there would be a silent no-op.
    with pytest.raises(TimelineEditError, match="never spoken"):
        _apply([SetLineEmotion(op="set_line_emotion", line_ordinal=4, emotion="angry")])


# -- render settings ---------------------------------------------------------


def test_pacing_and_duck_produce_new_settings_and_stale_reasons():
    result = _apply([
        SetScenePacing(op="set_scene_pacing", pacing=0.88),
        SetAmbienceDuck(op="set_ambience_duck", depth=0.9),
    ])
    assert result.settings.pacing == 0.88
    assert result.settings.ambience_duck == 0.9
    assert result.settings_changed and result.invalidates_audio
    assert not result.graph_changed
    assert len(result.stale_reasons) == 2


def test_default_settings_are_a_no_op_on_the_engine():
    # Defaults must land exactly on the DSP's own ducking ratio and leave the
    # PRD gap table untouched, or unedited scenes would render differently.
    assert SceneRenderSettings().duck_ratio == dsp.DUCK_RATIO_DEFAULT
    assert SceneRenderSettings(ambience_duck=0.0).duck_ratio == 1.0
    clips = [
        SpeechClip(line_ordinal=i, character_name=name, duration_ms=1000,
                   beat_index=0, scene_ordinal=1, block_id=i)
        for i, name in enumerate(["TOM", "MARA"], 1)
    ]
    assert plan_speech_bus(clips, gap_scale=1.0) == plan_speech_bus(clips)
    tight = plan_speech_bus(clips, gap_scale=0.5)
    assert tight.total_ms < plan_speech_bus(clips).total_ms


# -- shot ops ----------------------------------------------------------------


def test_insert_shot_renumbers_and_never_stales_the_audio():
    result = _apply(
        [InsertShot(op="insert_shot", after_ordinal=1,
                    shot=NewShot(size="cu", subjects=["MARA"], covers_lines=[3, 3],
                                 intent="Mara reacts"))],
        shotlist=_shotlist(),
    )
    shots = result.shotlist.shots
    assert [s.ordinal for s in shots] == [1, 2, 3]
    assert shots[1].size == "cu"
    # Duplicates collapse; ordinals stay pointed at real lines.
    assert shots[1].covers_lines == [3]
    # Defaults keep an assisted insert short without committing to an axis side.
    assert shots[1].axis_side == "neutral"
    assert result.shotlist_changed
    # The visual lane is projected at read time, so shots never stale the WAV.
    assert not result.invalidates_audio


def test_insert_shot_at_the_head():
    result = _apply(
        [InsertShot(op="insert_shot", after_ordinal=None,
                    shot=NewShot(size="ews", subjects=[], covers_lines=[1],
                                 intent="Cold open on the storm"))],
        shotlist=_shotlist(),
    )
    assert [s.size for s in result.shotlist.shots] == ["ews", "ws", "ms"]


def test_insert_shot_requires_an_existing_shot_list():
    with pytest.raises(TimelineEditError, match="no shot list"):
        _apply([InsertShot(op="insert_shot", after_ordinal=None,
                           shot=NewShot(size="cu", subjects=["MARA"], covers_lines=[3],
                                        intent="Reaction"))])


def test_insert_shot_after_unknown_shot_is_rejected():
    with pytest.raises(TimelineEditError, match="shot 9 not found"):
        _apply(
            [InsertShot(op="insert_shot", after_ordinal=9,
                        shot=NewShot(size="cu", subjects=["MARA"], covers_lines=[3],
                                     intent="Reaction"))],
            shotlist=_shotlist(),
        )


def test_shot_coverage_must_reference_real_lines():
    with pytest.raises(TimelineEditError, match=r"line\(s\) \[42\]"):
        _apply(
            [SetShotCoverage(op="set_shot_coverage", shot_ordinal=2, covers_lines=[42])],
            shotlist=_shotlist(),
        )


def test_set_shot_coverage_repoints_one_shot():
    result = _apply(
        [SetShotCoverage(op="set_shot_coverage", shot_ordinal=2, covers_lines=[2, 3])],
        shotlist=_shotlist(),
    )
    assert result.shotlist.shots[1].covers_lines == [2, 3]
    assert result.shotlist.shots[0].covers_lines == [1, 2]
    assert result.shotlist_changed and not result.invalidates_audio


def test_unknown_scene_is_rejected():
    with pytest.raises(TimelineEditError, match="scene 7 not found"):
        apply_edits(_graph(), 7, None, DEFAULTS,
                    [SetScenePacing(op="set_scene_pacing", pacing=0.9)])

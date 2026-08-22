"""Tests for visual prompt assembly (app/render/visual/) — PRD §4.6.

Prompt assembly is mechanical, not creative: golden strings are asserted
exactly, and omission of empty parts must leave no dangling commas or None.
"""

import pytest

from app.render.visual.prompts import (
    SIZE_PHRASES,
    STYLE_SUFFIXES,
    board_prompt,
    charsheet_prompt,
)
from app.render.visual.variants import (
    CharacterVariantSpec,
    VariantGapError,
    VariantOverlapError,
    variant_for,
)
from app.shotlist.schema import ShotSpec


def make_variant(**overrides) -> CharacterVariantSpec:
    base = dict(
        character_name="ALICE",
        label="default",
        scene_from=1,
        scene_to=None,
        wardrobe=None,
        condition=None,
        props=[],
    )
    base.update(overrides)
    return CharacterVariantSpec(**base)


class TestVariantFor:
    def test_exact_match_within_bounded_range(self) -> None:
        variants = [
            make_variant(label="act1", scene_from=1, scene_to=5),
            make_variant(label="act2", scene_from=6, scene_to=10),
        ]
        assert variant_for(variants, 3).label == "act1"
        assert variant_for(variants, 6).label == "act2"

    def test_range_bounds_are_inclusive(self) -> None:
        variants = [make_variant(label="act1", scene_from=2, scene_to=4)]
        assert variant_for(variants, 2).label == "act1"
        assert variant_for(variants, 4).label == "act1"

    def test_open_ended_range_matches_any_later_scene(self) -> None:
        variants = [make_variant(label="final", scene_from=7, scene_to=None)]
        assert variant_for(variants, 7).label == "final"
        assert variant_for(variants, 9001).label == "final"

    def test_gap_raises(self) -> None:
        variants = [make_variant(label="act1", scene_from=1, scene_to=5)]
        with pytest.raises(VariantGapError):
            variant_for(variants, 6)

    def test_empty_list_raises_gap(self) -> None:
        with pytest.raises(VariantGapError):
            variant_for([], 1)

    def test_overlap_raises(self) -> None:
        variants = [
            make_variant(label="a", scene_from=1, scene_to=5),
            make_variant(label="b", scene_from=4, scene_to=None),
        ]
        with pytest.raises(VariantOverlapError):
            variant_for(variants, 5)


class TestPhraseTables:
    def test_all_nine_sizes_have_phrases(self) -> None:
        expected = {
            "ecu": "extreme close-up",
            "cu": "close-up",
            "mcu": "medium close-up",
            "ms": "medium shot",
            "mws": "medium wide shot",
            "ws": "wide shot",
            "ews": "extreme wide shot",
            "insert": "insert detail shot",
            "pov": "point-of-view shot",
        }
        assert SIZE_PHRASES == expected

    def test_all_four_profiles_have_suffixes(self) -> None:
        assert set(STYLE_SUFFIXES) == {"classical", "handheld", "symmetrical", "anime"}
        assert STYLE_SUFFIXES["symmetrical"] == "symmetrical composition, centered framing"
        for suffix in STYLE_SUFFIXES.values():
            assert suffix and isinstance(suffix, str)


FULL_SHOT = ShotSpec(
    ordinal=1,
    size="mcu",
    subjects=["ALICE"],
    axis_side="a",
    lens_mm=50,
    camera_height="eye",
    movement="static",
    eyeline="left",
    covers_lines=[1],
    intent="reaction",
)


class TestBoardPrompt:
    def test_golden_fully_populated(self) -> None:
        variant = make_variant(wardrobe="worn leather jacket", condition="mud-spattered")
        prompt = board_prompt(
            shot=FULL_SHOT,
            subject_descriptions={"ALICE": "a tall woman with short red hair"},
            variant_by_subject={"ALICE": variant},
            location="abandoned warehouse",
            time_of_day="night",
            weather="rain",
            grammar_profile="classical",
        )
        assert prompt == (
            "medium close-up, 50mm lens, eye level, static camera, "
            "ALICE: a tall woman with short red hair, wearing worn leather jacket, "
            "mud-spattered, at abandoned warehouse, night, rain, eyeline left, "
            "classical composition, smooth deliberate framing"
        )

    def test_omits_empty_parts_cleanly(self) -> None:
        shot = FULL_SHOT.model_copy(update={"eyeline": "none"})
        prompt = board_prompt(
            shot=shot,
            subject_descriptions={"ALICE": "a tall woman"},
            variant_by_subject={},
            location=None,
            time_of_day=None,
            weather=None,
            grammar_profile="anime",
        )
        assert "None" not in prompt
        assert ", ," not in prompt
        assert not prompt.endswith(",")
        assert "eyeline" not in prompt
        assert "at " not in prompt

    def test_variant_without_wardrobe_or_condition(self) -> None:
        prompt = board_prompt(
            shot=FULL_SHOT,
            subject_descriptions={"ALICE": "a tall woman"},
            variant_by_subject={"ALICE": make_variant()},
            location="beach",
            time_of_day="day",
            weather=None,
            grammar_profile="handheld",
        )
        assert "wearing" not in prompt
        assert "None" not in prompt
        assert "ALICE: a tall woman" in prompt
        assert "at beach, day" in prompt

    def test_multiple_subjects_all_present(self) -> None:
        shot = FULL_SHOT.model_copy(update={"subjects": ["ALICE", "BOB"]})
        prompt = board_prompt(
            shot=shot,
            subject_descriptions={"ALICE": "a tall woman", "BOB": "a stocky man"},
            variant_by_subject={},
            location=None,
            time_of_day=None,
            weather=None,
            grammar_profile="classical",
        )
        assert "ALICE: a tall woman" in prompt
        assert "BOB: a stocky man" in prompt

    def test_style_suffix_matches_profile(self) -> None:
        for profile, suffix in STYLE_SUFFIXES.items():
            prompt = board_prompt(
                shot=FULL_SHOT,
                subject_descriptions={},
                variant_by_subject={},
                location=None,
                time_of_day=None,
                weather=None,
                grammar_profile=profile,
            )
            assert prompt.endswith(suffix)


class TestCharsheetPrompt:
    def test_contains_angle_wording(self) -> None:
        variant = make_variant(wardrobe="red cloak")
        expected_wording = {
            "front": "front view",
            "three_quarter": "three-quarter view",
            "profile": "profile view",
            "back": "back view",
        }
        for angle, wording in expected_wording.items():
            prompt = charsheet_prompt("a tall woman", variant, angle)
            assert wording in prompt

    def test_contains_description_wardrobe_condition_props(self) -> None:
        variant = make_variant(
            wardrobe="red cloak", condition="scarred", props=["lantern", "rope"]
        )
        prompt = charsheet_prompt("a tall woman", variant, "front")
        assert "a tall woman" in prompt
        assert "wearing red cloak" in prompt
        assert "scarred" in prompt
        assert "lantern" in prompt and "rope" in prompt

    def test_omits_empty_parts(self) -> None:
        prompt = charsheet_prompt("a tall woman", make_variant(), "back")
        assert "None" not in prompt
        assert ", ," not in prompt
        assert "wearing" not in prompt
        assert "props" not in prompt

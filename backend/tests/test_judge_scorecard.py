"""The scorecard renderers: does the page actually explain the numbers?

These exercise the pure formatting layer with hand-built judge results, so a
failure here is a failure of the writing rather than of a judge or a route.
Nothing touches the network, the repo, or the clock — every test that renders a
whole page pins ``generated_at``, which is the one moving part on it.
"""

from datetime import UTC, datetime

import pytest

from app.judge.model import (
    AnimaticFinding,
    AnimaticJudgment,
    CastingProposal,
    CastingProposalEntry,
    CharacterVoiceFit,
    SceneAnimaticScore,
    VoiceFinding,
    VoiceFitResult,
    VoiceSuggestion,
)
from app.judge.scorecard import (
    render_animatic_scorecard,
    render_casting_scorecard,
    render_full_scorecard,
    render_voice_fit_scorecard,
    scorecard_filename,
)

FIXED_TIME = datetime(2026, 9, 9, 14, 3, 11, tzinfo=UTC)


def flat(text: str) -> str:
    """Collapse the wrapping, so a sentence can be asserted on as a sentence.

    The page is wrapped to 78 columns, which means any quoted rationale is
    split across lines at whatever word happens to land there. Asserting on the
    wrapped form would make these tests fail every time a label was reworded.
    """
    return " ".join(text.split())


@pytest.fixture
def proposal():
    """A narrator with no tonal evidence and two characters with some."""
    return CastingProposal(
        rationale="Cast 3 part(s) across 3 of 3 available voice(s).",
        entries=[
            CastingProposalEntry(
                character=None,
                is_narrator=True,
                voice_id="narr1",
                voice_name="Guy",
                tone=None,
                tone_evidence="none",
                confidence=0.0,
                voice_fit=0.72,
                line_count=6,
                rationale=(
                    "The narrator speaks 6 line(s) and no line carries a delivery "
                    "tag or any tonal cue in its words, so no tone is proposed."
                ),
            ),
            CastingProposalEntry(
                character="MARA",
                voice_id="soft1",
                voice_name="Bella",
                tone="whispering",
                tone_evidence="emotion_tags",
                confidence=0.51,
                voice_fit=0.64,
                line_count=5,
                rationale="MARA speaks 5 line(s) and 1 of them carries a delivery tag.",
            ),
            CastingProposalEntry(
                character="TOM",
                voice_id="strong1",
                voice_name="Domi",
                tone="urgent",
                tone_evidence="text_cues",
                confidence=0.28,
                voice_fit=0.0,
                line_count=4,
                rationale="TOM speaks 4 line(s); no line carries a delivery tag.",
            ),
        ],
    )


@pytest.fixture
def voice_fit():
    return VoiceFitResult(
        overall_score=0.71,
        rationale="Judged 2 cast character(s); overall casting fit 0.71.",
        characters=[
            CharacterVoiceFit(
                character="MARA",
                voice_id="soft1",
                voice_name="Bella",
                score=0.62,
                rationale="MARA speaks 5 line(s), mostly whispering; 'Bella' is an adequate fit.",
                line_count=5,
                speaks_share=0.556,
                dominant_emotions=["whispering", "serious"],
                findings=[
                    VoiceFinding(
                        code="WARMTH_MISMATCH",
                        severity="info",
                        message="MARA's delivery reads cooler than voice 'Bella'.",
                    )
                ],
                suggestions=[
                    VoiceSuggestion(voice_id="strong1", voice_name="Domi", score=0.81)
                ],
            ),
            CharacterVoiceFit(
                character="TOM",
                voice_id="narr1",
                voice_name="Guy",
                score=0.83,
                rationale="TOM speaks 4 line(s), emotionally neutral; 'Guy' is a strong fit.",
                line_count=4,
                speaks_share=0.444,
                dominant_emotions=[],
            ),
        ],
        uncast_characters=["HARBORMASTER"],
    )


@pytest.fixture
def judgment():
    return AnimaticJudgment(
        overall_score=0.66,
        rationale="Judged 1 scene(s); overall animatic quality 0.66 with 2 finding(s).",
        coverage_score=1.0,
        continuity_score=0.49,
        variety_score=0.8,
        pacing_score=0.75,
        scenes=[
            SceneAnimaticScore(
                scene_ordinal=1,
                score=0.66,
                coverage_score=1.0,
                continuity_score=0.49,
                variety_score=0.8,
                pacing_score=0.75,
                shot_count=2,
                findings=[
                    AnimaticFinding(
                        code="AXIS_CROSS",
                        severity="error",
                        message="Shot 2 crosses the action axis.",
                        scene_ordinal=1,
                        shot_ordinal=2,
                    ),
                    AnimaticFinding(
                        code="PACING_LONG_TAKE",
                        severity="info",
                        message="Shot 1 runs ~24.0s across 3 line(s).",
                        scene_ordinal=1,
                        shot_ordinal=1,
                    ),
                ],
            )
        ],
        findings=[
            AnimaticFinding(
                code="AXIS_CROSS",
                severity="error",
                message="Shot 2 crosses the action axis.",
                scene_ordinal=1,
                shot_ordinal=2,
            ),
            AnimaticFinding(
                code="PACING_LONG_TAKE",
                severity="info",
                message="Shot 1 runs ~24.0s across 3 line(s).",
                scene_ordinal=1,
                shot_ordinal=1,
            ),
        ],
    )


class TestCastingScorecard:
    def test_every_part_appears_with_its_voice_and_reasoning(self, proposal):
        text = render_casting_scorecard(proposal)
        assert "THE NARRATOR" in text
        for entry in proposal.entries[1:]:
            assert entry.character in text
            assert entry.voice_name in text
            assert entry.rationale in text

    def test_absent_tone_is_explained_rather_than_left_blank(self, proposal):
        text = render_casting_scorecard(proposal)
        assert "no tone proposed" in text
        assert "the text gave no signal" in text
        # And the confidence that goes with it is never dressed up as a score.
        assert "not applicable" in text

    def test_tone_evidence_tier_is_named(self, proposal):
        text = render_casting_scorecard(proposal)
        assert "'whispering'" in text and "wrote into the script" in text
        assert "'urgent'" in text and "inferred from the words" in text

    def test_unrecorded_tag_fit_is_not_printed_as_a_zero_score(self, proposal):
        """TOM's entry came back from storage, which drops the fit number."""
        text = render_casting_scorecard(proposal)
        assert "not recorded" in text
        assert "0.00" not in text

    def test_an_empty_cast_says_so(self):
        text = render_casting_scorecard(CastingProposal())
        assert "nothing was cast" in text


class TestVoiceFitScorecard:
    def test_names_every_character_with_its_score(self, voice_fit):
        text = render_voice_fit_scorecard(voice_fit)
        for fit in voice_fit.characters:
            assert fit.character in text
            assert f"{fit.score:.2f}" in text
            assert fit.rationale in text

    def test_reports_line_count_share_and_emotions(self, voice_fit):
        text = render_voice_fit_scorecard(voice_fit)
        assert "5 dialogue lines" in text
        assert "56%" in text  # speaks_share 0.556, rendered as a percentage
        assert "whispering, serious" in text

    def test_absent_emotions_are_explained(self, voice_fit):
        text = render_voice_fit_scorecard(voice_fit)
        assert "none recorded" in text

    def test_better_fitting_voice_is_quoted_with_its_own_score(self, voice_fit):
        text = render_voice_fit_scorecard(voice_fit)
        assert "'Domi'" in text and "0.81" in text

    def test_uncast_speakers_are_listed(self, voice_fit):
        assert "HARBORMASTER" in render_voice_fit_scorecard(voice_fit)

    def test_note_is_carried_through(self, voice_fit):
        text = render_voice_fit_scorecard(voice_fit, note="Scored from storage.")
        assert "Scored from storage." in text


class TestAnimaticScorecard:
    def test_breaks_out_all_four_axes_with_their_weights(self, judgment):
        text = render_animatic_scorecard(judgment)
        for axis in ("coverage", "continuity", "variety", "pacing"):
            assert axis in text
        assert "0.49" in text and "0.80" in text and "0.75" in text
        assert "weight 0.30" in text  # the continuity weight the judge applies

    def test_findings_sit_under_the_axis_they_cost_worst_first(self, judgment):
        text = render_animatic_scorecard(judgment)
        # The error outranks the note, and both name where they happened.
        assert text.index("AXIS_CROSS") < text.index("PACING_LONG_TAKE")
        assert "scene 1, shot 2" in text

    def test_untouched_axis_says_so(self, judgment):
        assert "nothing pushed this axis down" in render_animatic_scorecard(judgment)

    def test_scene_breakdown_is_present(self, judgment):
        text = render_animatic_scorecard(judgment)
        assert "Scene 1" in text and "2 shots" in text


class TestFullScorecard:
    def test_header_names_the_project_and_the_time(self, proposal):
        text = render_full_scorecard(
            project_title="The Lighthouse Wager",
            casting=proposal,
            generated_at=FIXED_TIME,
        )
        assert "The Lighthouse Wager" in text
        assert "Generated 2026-09-09 14:03:11 UTC" in text

    def test_missing_work_is_a_titled_section_naming_its_endpoint(self):
        text = render_full_scorecard(project_title="Empty", generated_at=FIXED_TIME)
        assert "CASTING — not decided yet" in text
        assert "VOICE FIT — not judged yet" in text
        assert "ANIMATIC — not judged yet" in text
        assert "/judge/casting" in text
        assert "/judge/voices" in text
        assert "/shotlist" in text

    def test_is_deterministic_for_the_same_input(self, proposal, voice_fit, judgment):
        kwargs = dict(
            project_title="The Lighthouse Wager",
            casting=proposal,
            voice_fit=voice_fit,
            animatic=judgment,
            generated_at=FIXED_TIME,
        )
        assert render_full_scorecard(**kwargs) == render_full_scorecard(**kwargs)

    def test_wraps_inside_78_columns(self, proposal, voice_fit, judgment):
        text = render_full_scorecard(
            project_title="The Lighthouse Wager",
            casting=proposal,
            voice_fit=voice_fit,
            animatic=judgment,
            voice_fit_note="A note long enough to need wrapping " * 4,
            generated_at=FIXED_TIME,
        )
        assert [line for line in text.splitlines() if len(line) > 78] == []


class TestFilename:
    def test_slugifies_the_title(self):
        assert scorecard_filename("The Lighthouse Wager") == "the-lighthouse-wager-scorecard.txt"

    def test_falls_back_when_nothing_survives_the_slug(self):
        # A header is no place to discover a title had no ASCII in it.
        assert scorecard_filename("☃ ☃") == "project-scorecard.txt"

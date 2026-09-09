"""Plain-text scorecards: the judges' numbers, written out in prose.

The judges already return everything a reviewer needs — a score, the material
it came from, and a sentence about it — but they return it as JSON, and a
number in JSON persuades nobody. This module renders those results as a page a
person reads top to bottom, where every figure is followed by what produced it
and the judge's own words about it.

Two rules hold the module together:

* **Nothing is scored here.** Every number printed came out of
  :mod:`app.judge.voices`, :mod:`app.judge.casting` or
  :mod:`app.judge.animatic`. If this file ever computes a score, the scorecard
  stops describing the system and starts being a second opinion about it.
* **An absent value is stated, never blanked.** A missing tone is a real
  answer ("the text gave no signal"), and printing an empty column or a 0.00
  in its place would turn the one honest part of a casting into a number a
  director cannot check. Every renderer here says what is missing and why.

Pure formatting: no I/O, no HTTP, no clock except the one timestamp the caller
may inject, so the same inputs always produce the same page.
"""

from __future__ import annotations

import re
import textwrap
from datetime import UTC, datetime

# Intra-package reuse of the judges' own tuning constants, for the same reason
# app.judge.casting reaches into app.judge.voices: prose that quotes a weight
# or a threshold has to quote the one the judge actually applied. Copying the
# numbers into sentences here is how a scorecard ends up confidently explaining
# a rule that was retuned three commits ago.
from app.judge.animatic import (
    _AXIS_WEIGHTS,
    _LINES_PER_SHOT_GATE,
    _LONG_TAKE_MS,
    _SEVERITY_PENALTY,
)
from app.judge.casting import _TAG_CONFIDENCE_CEILING, _TEXT_CONFIDENCE_CEILING
from app.judge.model import (
    AnimaticFinding,
    AnimaticJudgment,
    CastingProposal,
    CastingProposalEntry,
    CharacterVoiceFit,
    VoiceFitResult,
)
from app.judge.voices import _SUGGESTION_MARGIN

# 78 leaves a two-column margin inside an 80-column terminal, which is where a
# reviewer who curls this endpoint will read it.
_WIDTH = 78
_INDENT = "  "
_ITEM = "      "
# The dotted label column: wide enough for "confidence" and "continuity" with
# leaders left over, so the values line up into a readable second column.
_LABEL_WIDTH = 15

# Endpoints named by the "not produced yet" sections. Spelled with the literal
# ``{project_id}`` placeholder because these renderers are pure and never see a
# project id — a reviewer substitutes the one from the URL they just called.
_CASTING_ENDPOINT = "POST /api/v1/projects/{project_id}/judge/casting"
_VOICES_ENDPOINT = "POST /api/v1/projects/{project_id}/judge/voices"
_SHOTLIST_ENDPOINT = "POST /api/v1/projects/{project_id}/scenes/{scene}/shotlist"
_ANIMATIC_ENDPOINT = "POST /api/v1/projects/{project_id}/judge/animatic"


# --------------------------------------------------------------------------- #
# Text plumbing
# --------------------------------------------------------------------------- #
def _rule(char: str = "-") -> str:
    return char * _WIDTH


def _wrap(text: str, initial: str, subsequent: str) -> list[str]:
    return textwrap.wrap(
        text,
        width=_WIDTH,
        initial_indent=initial,
        subsequent_indent=subsequent,
        break_on_hyphens=False,
    ) or [initial.rstrip()]


def _para(text: str, indent: str = _INDENT) -> list[str]:
    return _wrap(text, indent, indent)


def _field(label: str, value: str, indent: str = _ITEM) -> list[str]:
    """One ``label ....... value`` row, wrapped under a hanging indent."""
    head = indent + (label + " ").ljust(_LABEL_WIDTH, ".") + " "
    return _wrap(value, head, " " * len(head))


def _title(text: str) -> list[str]:
    return [_rule(), text, _rule()]


def _score(value: float) -> str:
    """Two decimals, matching the precision the judges quote in their own prose."""
    return f"{value:.2f}"


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _finding_line(finding: AnimaticFinding, indent: str) -> list[str]:
    where = []
    if finding.scene_ordinal is not None:
        where.append(f"scene {finding.scene_ordinal}")
    if finding.shot_ordinal is not None:
        where.append(f"shot {finding.shot_ordinal}")
    locus = f" ({', '.join(where)})" if where else ""
    text = f"[{finding.severity}] {finding.code}{locus} — {finding.message}"
    return _wrap(text, indent, indent + "    ")


def scorecard_filename(project_title: str) -> str:
    """A download filename for one project's scorecard.

    ASCII-only by construction: the slug is what goes into a
    ``Content-Disposition`` header, and a header is no place to discover that a
    project was titled in Cyrillic.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", project_title.lower()).strip("-")
    return f"{slug or 'project'}-scorecard.txt"


# --------------------------------------------------------------------------- #
# Casting
# --------------------------------------------------------------------------- #
_TONE_EVIDENCE_PHRASE = {
    "emotion_tags": (
        "read off a delivery tag the writer wrote into the script — the strong "
        f"tier, which is why it may reach {_TAG_CONFIDENCE_CEILING:.2f}"
    ),
    "text_cues": (
        "inferred from the words of the lines themselves, since no line carried "
        "a delivery tag — the weak tier, capped at "
        f"{_TEXT_CONFIDENCE_CEILING:.2f} so a guess can never out-rank a "
        "stated intention"
    ),
}


def _tone_row(entry: CastingProposalEntry) -> list[str]:
    if entry.tone is None:
        return _field(
            "tone",
            "no tone proposed — the text gave no signal. A plausible-sounding "
            "guess would be the one part of this casting a director could not "
            "check, so the part is left untoned on purpose.",
        )
    evidence = _TONE_EVIDENCE_PHRASE.get(
        entry.tone_evidence,
        "the evidence tier is not recorded with the saved casting; the sentence "
        "under 'why' names what the script showed",
    )
    return _field("tone", f"'{entry.tone}' — {evidence}")


def _confidence_row(entry: CastingProposalEntry) -> list[str]:
    if entry.tone is None:
        return _field(
            "confidence",
            "not applicable — a part with no tone has nothing to be confident "
            "about, so the number is 0.00 rather than an invented figure.",
        )
    return _field(
        "confidence",
        f"{_score(entry.confidence)} — how far the tone can be trusted: how "
        "much of the part voted for it, how much of it carried any evidence at "
        "all, and how many lines that was.",
    )


def _voice_fit_row(entry: CastingProposalEntry) -> list[str]:
    # A saved casting round-trips through app.api.repo.CastEntry, which keeps
    # the rationale but has no column for the tag fit — so 0.0 here means "not
    # recorded", not "a terrible voice". A real fit cannot be 0.00: it would
    # need the voice to sit at the far end of both axes at once, which no tag
    # in app.judge.voices' tables reaches.
    if entry.voice_fit <= 0.0:
        return _field(
            "tag fit",
            "not recorded — this casting was read back from storage, which "
            "keeps the reasoning but not the fit number; the sentence below "
            "quotes it as it stood when the part was cast.",
        )
    return _field(
        "tag fit",
        f"{_score(entry.voice_fit)} — how far the voice's tag-derived energy "
        "and warmth sit from what this part's lines demand (energy is the "
        "heavier of the two).",
    )


def _casting_entry_block(index: int, entry: CastingProposalEntry) -> list[str]:
    who = "THE NARRATOR" if entry.is_narrator else (entry.character or "UNNAMED PART")
    lines = [f"{_INDENT}[{index}] {who}"]
    lines += _field("voice", f"'{entry.voice_name}' (id {entry.voice_id})")
    if entry.is_narrator:
        counted = (
            f"{_plural(entry.line_count, 'narration line')} — the action and "
            "narration a narrator reads, which is every spoken line no "
            "character owns"
        )
    else:
        counted = f"{_plural(entry.line_count, 'dialogue line')} in this draft"
    lines += _field("lines", counted)
    lines += _tone_row(entry)
    lines += _confidence_row(entry)
    lines += _voice_fit_row(entry)
    lines += _field("why", entry.rationale)
    return lines


def render_casting_scorecard(proposal: CastingProposal) -> str:
    """The cast list as a page: every part, its voice, its tone, and the evidence.

    Parts appear in the order the proposer dealt them — the narrator first,
    then the biggest speaking part down to the walk-ons — because that order
    *is* the reasoning: each part took the best voice still free when its turn
    came, so reading top to bottom is reading the decisions as they were made.
    """
    lines = _title("CASTING — who speaks, in which voice, and in what tone")
    lines.append("")
    if proposal.rationale:
        lines += _para(proposal.rationale)
        lines.append("")
    if not proposal.entries:
        lines += _para("This casting has no parts in it — nothing was cast.")
        return "\n".join(lines)

    for index, entry in enumerate(proposal.entries, start=1):
        lines += _casting_entry_block(index, entry)
        lines.append("")
    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------- #
# Voice fit
# --------------------------------------------------------------------------- #
def _character_block(fit: CharacterVoiceFit) -> list[str]:
    lines = [f"{_INDENT}{fit.character} — {_score(fit.score)}"]
    lines += _field("voice", f"'{fit.voice_name}' (id {fit.voice_id})")
    lines += _field(
        "lines",
        f"{_plural(fit.line_count, 'dialogue line')}, "
        f"{_pct(fit.speaks_share)} of all the dialogue in the script — the "
        "weight this character's score carries in the overall figure.",
    )
    if fit.dominant_emotions:
        lines += _field(
            "emotions",
            ", ".join(fit.dominant_emotions)
            + " — the deliveries that recur most across those lines, and what "
            "the voice is being asked to reach.",
        )
    else:
        lines += _field(
            "emotions",
            "none recorded — no line of this character's carries a delivery "
            "tag, so the need is read as neutral on both axes rather than "
            "guessed at.",
        )
    lines += _field("why", fit.rationale)

    if fit.findings:
        for finding in fit.findings:
            lines += _field(
                "finding",
                f"[{finding.severity}] {finding.code} — {finding.message}",
            )
    else:
        lines += _field(
            "findings",
            "none — nothing about this pairing tripped an arousal, warmth or "
            "narration check.",
        )

    if fit.suggestions:
        for suggestion in fit.suggestions:
            lines += _field(
                "better fit",
                f"'{suggestion.voice_name}' (id {suggestion.voice_id}) scores "
                f"{_score(suggestion.score)} for this character, against "
                f"{_score(fit.score)} for the voice actually assigned.",
            )
    else:
        lines += _field(
            "better fit",
            "nothing in the pool beats the assigned voice by the "
            f"{_SUGGESTION_MARGIN:.2f} margin a suggestion has to clear before "
            "it is worth a recast.",
        )
    return lines


def render_voice_fit_scorecard(result: VoiceFitResult, *, note: str | None = None) -> str:
    """Per-character casting fit, with the alternative each character was measured against.

    ``note`` is for the caller that knows something about *how* this judgement
    was obtained which the result itself cannot carry — most of all, whether
    the voices it scored still had their provider tags. Without that the reader
    has no way to tell a neutral score from an informed one.
    """
    lines = _title("VOICE FIT — does each assigned voice suit the character speaking it?")
    lines.append("")
    lines += _para(
        f"Overall {_score(result.overall_score)} — the mean of the per-character "
        "scores below, weighted by how much each character speaks, so a "
        "miscast lead costs more than a miscast walk-on."
    )
    lines.append("")
    lines += _para(result.rationale)
    if note:
        lines.append("")
        lines += _para(note)
    lines.append("")

    if not result.characters:
        lines += _para("No character was cast, so there was no pairing to score.")
    for fit in result.characters:
        lines += _character_block(fit)
        lines.append("")

    if result.uncast_characters:
        lines += _para(
            f"{_plural(len(result.uncast_characters), 'speaking character')} "
            "carry no voice at all and so appear in no score above: "
            + ", ".join(result.uncast_characters)
            + "."
        )
    else:
        lines += _para("Every speaking character in the script has a voice.")
    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------- #
# Animatic
# --------------------------------------------------------------------------- #
# Which axis a finding was raised by. app.judge.animatic composes findings one
# axis at a time and then flattens them into a single ranked list, so the axis
# has to be recovered from the code to put each finding back under the number
# it pushed down. Anything unrecognized belongs to continuity: that is the one
# axis whose codes come from the continuity validator's open-ended rule
# vocabulary (AXIS_CROSS, EYELINE_MISMATCH, ...) rather than from this module's
# own fixed set.
_AXIS_OF_CODE = {
    "COVERAGE_GAP": "coverage",
    "SHOT_MONOTONY": "variety",
    "PACING_LONG_TAKE": "pacing",
    "PACING_UNDERCOVERED": "pacing",
}

_AXIS_BLURB = {
    "coverage": (
        "the share of the scene's dialogue beats that some shot actually covers"
    ),
    "continuity": (
        "1.00 less a penalty for every continuity finding — "
        f"{_SEVERITY_PENALTY['error']:.2f} per error, "
        f"{_SEVERITY_PENALTY['warn']:.2f} per warning, "
        f"{_SEVERITY_PENALTY['info']:.2f} per note"
    ),
    "variety": (
        "how many distinct shot sizes and camera movements the scene reaches "
        "against what a scene of its length could reach, sizes counting the "
        "heavier"
    ),
    "pacing": (
        "1.00 less 0.20 for every shot estimated to run past "
        f"{_LONG_TAKE_MS // 1000}s and 0.25 when the scene averages more than "
        f"{_LINES_PER_SHOT_GATE:.0f} dialogue lines to a shot"
    ),
}


def _axis_block(judgment: AnimaticJudgment) -> list[str]:
    """The four axes worst first, each followed by the findings that cost it."""
    by_axis: dict[str, list[AnimaticFinding]] = {name: [] for name in _AXIS_WEIGHTS}
    for finding in judgment.findings:
        by_axis[_AXIS_OF_CODE.get(finding.code, "continuity")].append(finding)

    scores = {
        "coverage": judgment.coverage_score,
        "continuity": judgment.continuity_score,
        "variety": judgment.variety_score,
        "pacing": judgment.pacing_score,
    }
    lines: list[str] = []
    # Worst axis first: the reader wants the number that cost the most, and a
    # tie falls back to the weight so the axis with more of the score at stake
    # is read first.
    for axis in sorted(scores, key=lambda a: (scores[a], -_AXIS_WEIGHTS[a])):
        lines += _field(
            axis,
            f"{_score(scores[axis])} at weight {_AXIS_WEIGHTS[axis]:.2f} — "
            f"{_AXIS_BLURB[axis]}.",
            indent=_INDENT + "  ",
        )
        findings = by_axis[axis]
        if not findings:
            lines += _para(
                "nothing pushed this axis down.", indent=_INDENT + "      "
            )
            continue
        # Already ranked worst first by the judge (error before warn before
        # info, then scene and shot order); keeping that order keeps the two
        # documents telling the same story.
        for finding in findings:
            lines += _finding_line(finding, _INDENT + "      ")
    return lines


def render_animatic_scorecard(judgment: AnimaticJudgment) -> str:
    """The previz judgement: the overall number, its four axes, then each scene."""
    lines = _title("ANIMATIC — is the previz coverage worth shooting?")
    lines.append("")
    lines += _para(
        f"Overall {_score(judgment.overall_score)} across "
        f"{_plural(len(judgment.scenes), 'scene')} — the blend of the four axes "
        "below, averaged over the scenes weighted by how many shots each holds."
    )
    lines.append("")
    lines += _para(judgment.rationale)
    lines.append("")

    if not judgment.scenes:
        lines += _para(
            "No scene was scored, so the four axes have nothing behind them."
        )
        return "\n".join(lines).rstrip()

    lines += _para("The four axes, worst first, and what pushed each one down:")
    lines.append("")
    lines += _axis_block(judgment)
    lines.append("")

    lines += _para("Scene by scene:")
    lines.append("")
    for scene in judgment.scenes:
        lines.append(
            f"{_INDENT}  Scene {scene.scene_ordinal} — {_score(scene.score)} "
            f"from {_plural(scene.shot_count, 'shot')}"
        )
        lines += _field(
            "axes",
            f"coverage {_score(scene.coverage_score)}, "
            f"continuity {_score(scene.continuity_score)}, "
            f"variety {_score(scene.variety_score)}, "
            f"pacing {_score(scene.pacing_score)}",
            indent=_INDENT + "      ",
        )
        if scene.findings:
            for finding in scene.findings:
                lines += _finding_line(finding, _INDENT + "      ")
        else:
            lines += _para(
                "no finding was raised against this scene.",
                indent=_INDENT + "      ",
            )
        lines.append("")
    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------- #
# The whole page
# --------------------------------------------------------------------------- #
def _missing(title: str, explanation: str) -> str:
    lines = _title(title)
    lines.append("")
    lines += _para(explanation)
    return "\n".join(lines)


_NO_CASTING = (
    "No casting has been decided for this project yet, so there is nothing to "
    f"explain here. {_CASTING_ENDPOINT} reads the script and decides one — a "
    "voice and a delivery tone for the narrator and every speaking character — "
    "and saves it, which is the copy the renderer reads."
)
_NO_VOICE_FIT = (
    "No casting is saved, so there is no pairing of characters to voices to "
    f"score. Decide one with {_CASTING_ENDPOINT} and this section fills in by "
    f"itself, or score a casting of your own with {_VOICES_ENDPOINT}."
)
_NO_ANIMATIC = (
    "No scene has a shot list, so there is no previz coverage to judge. Author "
    f"one with {_SHOTLIST_ENDPOINT} — the judge then scores every scene that "
    f"has one, here or at {_ANIMATIC_ENDPOINT}."
)

_PREAMBLE = (
    "Every score below is printed with the material it was computed from and "
    "the judge's own sentence about it. Nothing is scored on this page: it "
    "renders results the judges returned, and each of those is a deterministic "
    "heuristic over the story graph — no model was asked for an opinion, so "
    "the same script scores the same way every time."
)


def render_full_scorecard(
    *,
    project_title: str,
    casting: CastingProposal | None = None,
    voice_fit: VoiceFitResult | None = None,
    animatic: AnimaticJudgment | None = None,
    voice_fit_note: str | None = None,
    generated_at: datetime | None = None,
) -> str:
    """One page covering whatever has been judged, and what has not.

    A section is never dropped for want of data: a project that has been cast
    but never shot-listed still gets an ANIMATIC heading, saying so and naming
    the endpoint that would fill it. A reviewer reading the page should never
    have to wonder whether a section is missing because the work was not done
    or because the report forgot it.

    ``generated_at`` defaults to now, which is the one non-deterministic thing
    on the page; callers that need byte-identical output (tests, diffs between
    two drafts) pass their own.
    """
    stamp = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [_rule("=")]
    lines += _wrap(f"JUDGE SCORECARD — {project_title}", "", "  ")
    lines.append(f"Generated {stamp}")
    lines.append(_rule("="))
    lines.append("")
    lines += _para(_PREAMBLE, indent="")
    lines.append("")

    sections = [
        render_casting_scorecard(casting)
        if casting is not None
        else _missing("CASTING — not decided yet", _NO_CASTING),
        render_voice_fit_scorecard(voice_fit, note=voice_fit_note)
        if voice_fit is not None
        else _missing("VOICE FIT — not judged yet", _NO_VOICE_FIT),
        render_animatic_scorecard(animatic)
        if animatic is not None
        else _missing("ANIMATIC — not judged yet", _NO_ANIMATIC),
    ]
    return "\n".join(lines) + "\n\n".join(sections) + "\n"

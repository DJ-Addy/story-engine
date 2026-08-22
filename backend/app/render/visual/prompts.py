"""Mechanical visual prompt assembly (PRD §4.6).

Prompt assembly is deterministic string composition from the shot spec and
continuity data — no creativity, no LLM. Empty parts are omitted cleanly.
"""

from typing import Literal

from app.render.visual.variants import CharacterVariantSpec
from app.shotlist.schema import ShotSpec

SIZE_PHRASES: dict[str, str] = {
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

STYLE_SUFFIXES: dict[str, str] = {
    "classical": "classical composition, smooth deliberate framing",
    "handheld": "handheld energy, documentary realism",
    "symmetrical": "symmetrical composition, centered framing",
    "anime": "anime style, cel-shaded rendering",
}

_HEIGHT_PHRASES: dict[str, str] = {
    "low": "low angle",
    "eye": "eye level",
    "high": "high angle",
    "overhead": "overhead angle",
}

_MOVEMENT_PHRASES: dict[str, str] = {
    "static": "static camera",
    "pan": "panning camera",
    "tilt": "tilting camera",
    "dolly": "dolly movement",
    "handheld": "handheld camera",
    "crane": "crane movement",
}

_EYELINE_PHRASES: dict[str, str] = {
    "left": "eyeline left",
    "right": "eyeline right",
    "to_camera": "looking to camera",
}

_ANGLE_PHRASES: dict[str, str] = {
    "front": "front view",
    "three_quarter": "three-quarter view",
    "profile": "profile view",
    "back": "back view",
}


def _subject_block(
    subject: str,
    subject_descriptions: dict[str, str],
    variant_by_subject: dict[str, CharacterVariantSpec],
) -> str | None:
    description = subject_descriptions.get(subject)
    if not description:
        return None
    parts = [f"{subject}: {description}"]
    variant = variant_by_subject.get(subject)
    if variant is not None:
        if variant.wardrobe:
            parts.append(f"wearing {variant.wardrobe}")
        if variant.condition:
            parts.append(variant.condition)
    return ", ".join(parts)


def board_prompt(
    shot: ShotSpec,
    subject_descriptions: dict[str, str],
    variant_by_subject: dict[str, CharacterVariantSpec],
    location: str | None,
    time_of_day: str | None,
    weather: str | None,
    grammar_profile: str,
) -> str:
    """Assemble a storyboard image prompt from the shot spec and continuity data."""
    parts: list[str] = [
        SIZE_PHRASES[shot.size],
        f"{shot.lens_mm}mm lens",
        _HEIGHT_PHRASES[shot.camera_height],
        _MOVEMENT_PHRASES[shot.movement],
    ]

    for subject in shot.subjects:
        block = _subject_block(subject, subject_descriptions, variant_by_subject)
        if block:
            parts.append(block)

    setting: list[str] = []
    if location:
        setting.append(f"at {location}")
    if time_of_day:
        setting.append(time_of_day)
    if weather:
        setting.append(weather)
    parts.extend(setting)

    eyeline_phrase = _EYELINE_PHRASES.get(shot.eyeline)
    if eyeline_phrase:
        parts.append(eyeline_phrase)

    parts.append(STYLE_SUFFIXES[grammar_profile])
    return ", ".join(parts)


def charsheet_prompt(
    character_description: str,
    variant: CharacterVariantSpec,
    angle: Literal["front", "three_quarter", "profile", "back"],
) -> str:
    """Assemble a character-sheet reference image prompt for one angle."""
    parts = [
        "character reference sheet",
        _ANGLE_PHRASES[angle],
        character_description,
    ]
    if variant.wardrobe:
        parts.append(f"wearing {variant.wardrobe}")
    if variant.condition:
        parts.append(variant.condition)
    if variant.props:
        parts.append(f"holding {', '.join(variant.props)}")
    parts.append("full body, neutral background")
    return ", ".join(parts)

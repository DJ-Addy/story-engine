"""Turn a flat ``RawElement`` list into the normalized story graph.

Scenes split on sluglines (1-based ordinals, with an ordinal-0 preamble scene
for any elements before the first slugline). Dialogue attribution comes from
character cues, which on screenplays is near-deterministic: confidence 1.0,
source "cue".
"""

from __future__ import annotations

from app.ingest.elements import (
    AttributedLine,
    ElementKind,
    NormalizedCharacter,
    NormalizedScene,
    RawElement,
    StoryGraph,
)
from app.nlp.emotion import emotion_from_parenthetical

_INTERIOR_PREFIXES: list[tuple[str, bool | None]] = [
    ("INT./EXT.", None),
    ("INT/EXT", None),
    ("I/E", None),
    ("INT.", True),
    ("EXT.", False),
]

_LINE_KINDS = {
    ElementKind.DIALOGUE: "dialogue",
    ElementKind.PARENTHETICAL: "parenthetical",
    ElementKind.ACTION: "action",
    ElementKind.TRANSITION: "transition",
    ElementKind.UNKNOWN: "action",
}

_SPEAKER_LINE_KINDS = {"dialogue", "parenthetical"}


def normalize(elements: list[RawElement]) -> StoryGraph:
    characters: dict[str, NormalizedCharacter] = {}
    buckets = _split_scenes(elements)
    next_ordinal = 0 if buckets and buckets[0][0] is None else 1

    scenes: list[NormalizedScene] = []
    for slugline, body in buckets:
        scenes.append(_build_scene(next_ordinal, slugline, body, characters))
        next_ordinal += 1

    return StoryGraph(scenes=scenes, characters=list(characters.values()))


def _split_scenes(
    elements: list[RawElement],
) -> list[tuple[RawElement | None, list[RawElement]]]:
    buckets: list[tuple[RawElement | None, list[RawElement]]] = []
    slugline: RawElement | None = None
    body: list[RawElement] = []
    for element in elements:
        if element.kind is ElementKind.SLUGLINE:
            if slugline is not None or body:
                buckets.append((slugline, body))
            slugline, body = element, []
        else:
            body.append(element)
    if slugline is not None or body:
        buckets.append((slugline, body))
    return buckets


def _build_scene(
    ordinal: int,
    slugline: RawElement | None,
    body: list[RawElement],
    characters: dict[str, NormalizedCharacter],
) -> NormalizedScene:
    interior: bool | None = None
    location: str | None = None
    time_of_day: str | None = None
    if slugline is not None:
        interior, location, time_of_day = _parse_slugline(slugline.text)

    lines: list[AttributedLine] = []
    speaker: str | None = None
    # Emotion from the most recent parenthetical in the current speech block;
    # governs the dialogue line(s) that follow it, until the cue changes or
    # we leave the speech block entirely (action/transition/unknown).
    pending_emotion: str | None = None
    for element in body:
        if element.kind is ElementKind.CHARACTER_CUE:
            speaker = _canonical_name(element)
            pending_emotion = None
            characters.setdefault(
                speaker, NormalizedCharacter(canonical_name=speaker)
            )
            continue
        if element.kind not in _LINE_KINDS:
            continue

        kind = _LINE_KINDS[element.kind]
        if kind not in _SPEAKER_LINE_KINDS:
            speaker = None
            pending_emotion = None
        name = speaker if kind in _SPEAKER_LINE_KINDS else None

        emotion: str | None = None
        if kind == "parenthetical":
            resolved = emotion_from_parenthetical(element.text)
            if resolved is not None:
                pending_emotion = resolved
            emotion = resolved
        elif kind == "dialogue":
            emotion = pending_emotion

        lines.append(
            AttributedLine(
                ordinal=len(lines) + 1,
                kind=kind,
                text=element.text,
                character_name=name,
                emotion=emotion,
                attribution_confidence=1.0 if name else None,
                attribution_source="cue" if name else None,
            )
        )
        if kind == "dialogue" and name:
            characters[name].line_count += 1

    return NormalizedScene(
        ordinal=ordinal,
        slugline=slugline.text if slugline is not None else None,
        interior=interior,
        location=location,
        time_of_day=time_of_day,
        lines=lines,
    )


def _canonical_name(cue: RawElement) -> str:
    return (cue.cue_name or cue.text).strip().upper()


def _parse_slugline(text: str) -> tuple[bool | None, str | None, str | None]:
    stripped = text.strip()
    upper = stripped.upper()
    interior: bool | None = None
    rest = stripped
    for prefix, value in _INTERIOR_PREFIXES:
        if upper.startswith(prefix):
            interior = value
            rest = stripped[len(prefix) :]
            break

    rest = rest.lstrip(". ").strip()
    if not rest:
        return interior, None, None

    parts = [part.strip() for part in rest.split(" - ")]
    if len(parts) >= 2:
        return interior, " - ".join(parts[:-1]), parts[-1]
    return interior, rest, None

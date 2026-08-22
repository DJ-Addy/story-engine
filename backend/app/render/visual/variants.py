"""Character appearance variants scoped to inclusive scene ranges.

``variant_for`` mirrors the DB gist-exclusion guarantee for the in-memory
path: exactly one variant must apply to any given scene ordinal.
"""

from pydantic import BaseModel, Field


class VariantGapError(Exception):
    """No variant covers the requested scene ordinal."""


class VariantOverlapError(Exception):
    """More than one variant covers the requested scene ordinal."""


class CharacterVariantSpec(BaseModel):
    character_name: str
    label: str
    scene_from: int
    scene_to: int | None = None
    wardrobe: str | None = None
    condition: str | None = None
    props: list[str] = Field(default_factory=list)


def variant_for(
    variants: list[CharacterVariantSpec], scene_ordinal: int
) -> CharacterVariantSpec:
    """Return the single variant whose inclusive scene range covers the ordinal.

    A ``scene_to`` of None means the variant is open-ended (applies to all
    scenes from ``scene_from`` onward).
    """
    matches = [
        v
        for v in variants
        if v.scene_from <= scene_ordinal and (v.scene_to is None or v.scene_to >= scene_ordinal)
    ]
    if not matches:
        raise VariantGapError(f"no variant covers scene {scene_ordinal}")
    if len(matches) > 1:
        labels = ", ".join(v.label for v in matches)
        raise VariantOverlapError(f"scene {scene_ordinal} covered by multiple variants: {labels}")
    return matches[0]

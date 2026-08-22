"""Final Draft (FDX) parser emitting flat ``RawElement`` lists.

FDX is XML: FinalDraft > Content > Paragraph[@Type] > Text. Paragraph types
map directly onto element kinds; anything unrecognized becomes UNKNOWN.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from app.ingest.elements import ElementKind, RawElement
from app.ingest.fountain import split_cue

_TYPE_MAP = {
    "Scene Heading": ElementKind.SLUGLINE,
    "Action": ElementKind.ACTION,
    "Character": ElementKind.CHARACTER_CUE,
    "Parenthetical": ElementKind.PARENTHETICAL,
    "Dialogue": ElementKind.DIALOGUE,
    "Transition": ElementKind.TRANSITION,
}


def parse_fdx(xml_text: str) -> list[RawElement]:
    root = ET.fromstring(xml_text)
    content = root.find("Content")
    if content is None:
        return []

    elements: list[RawElement] = []
    for paragraph in content.iter("Paragraph"):
        text = "".join(
            part for node in paragraph.findall("Text") for part in node.itertext()
        ).strip()
        if not text:
            continue
        kind = _TYPE_MAP.get(paragraph.get("Type", ""), ElementKind.UNKNOWN)
        cue_name: str | None = None
        cue_extension: str | None = None
        if kind is ElementKind.CHARACTER_CUE:
            cue_name, cue_extension = split_cue(text)
        elements.append(
            RawElement(
                kind=kind,
                text=text,
                cue_name=cue_name,
                cue_extension=cue_extension,
            )
        )
    return elements

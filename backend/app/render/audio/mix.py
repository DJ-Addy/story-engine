"""ffmpeg command assembly, as data. Execution happens in workers later.

Nothing in this module touches audio or requires ffmpeg to be installed.
"""

from __future__ import annotations

import math
from typing import Literal

Preset = Literal["subtle", "standard", "cinematic"]

AMBIENCE_BED_MS = 30_000

# PRD sidechain presets, pre-formatted as filter-string fragments.
_SIDECHAIN_PRESETS: dict[Preset, dict[str, str]] = {
    "standard": {"threshold": "0.035", "ratio": "6", "attack": "12", "release": "380"},
    "subtle": {"threshold": "0.035", "ratio": "3", "attack": "12", "release": "500"},
    "cinematic": {"threshold": "0.035", "ratio": "8", "attack": "12", "release": "300"},
}

LOUDNORM_FILTER = "loudnorm=I=-18:TP=-1.5:LRA=9"


def scene_mix_filter_graph(preset: Preset = "standard") -> str:
    """PRD filter graph: duck ambience under speech, mix, then loudness-normalize."""
    p = _SIDECHAIN_PRESETS[preset]
    sidechain = (
        f"sidechaincompress=threshold={p['threshold']}:ratio={p['ratio']}"
        f":attack={p['attack']}:release={p['release']}:makeup=1"
    )
    return (
        "[1:a]volume=0.5[amb];"
        f"[amb][0:a]{sidechain}[ducked];"
        "[0:a][ducked]amix=inputs=2:duration=first:normalize=0[mixed];"
        f"[mixed]{LOUDNORM_FILTER}[out]"
    )


def build_scene_mix_command(
    speech_path: str,
    ambience_path: str,
    out_path: str,
    preset: Preset = "standard",
) -> list[str]:
    """argv for mixing a scene: speech is input 0, ambience is input 1."""
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(speech_path),
        "-i",
        str(ambience_path),
        "-filter_complex",
        scene_mix_filter_graph(preset),
        "-map",
        "[out]",
        "-c:a",
        "pcm_s24le",
        str(out_path),
    ]


def extra_loop_count(target_ms: int, bed_ms: int = AMBIENCE_BED_MS) -> int:
    """Extra -stream_loop passes needed to cover target: ceil(target/bed) - 1."""
    if target_ms <= 0:
        return 0
    return max(math.ceil(target_ms / bed_ms) - 1, 0)


def build_loop_extend_command(bed_path: str, target_ms: int, out_path: str) -> list[str]:
    """argv to extend a 30 s ambience bed to at least ``target_ms``.

    TODO: replace hard looping with an equal-power acrossfade at each loop
    seam so the bed repeats seamlessly.
    """
    return [
        "ffmpeg",
        "-y",
        "-stream_loop",
        str(extra_loop_count(target_ms)),
        "-i",
        str(bed_path),
        "-t",
        f"{target_ms / 1000:.3f}",
        str(out_path),
    ]

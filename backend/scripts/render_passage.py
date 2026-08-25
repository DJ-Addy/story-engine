"""Render an entire screenplay passage (all scenes) into one continuous WAV.

Each scene is rendered by the normal pipeline (per-character voices + inferred
ambience, sidechain-ducked), then the scenes are concatenated with a
scene-change gap (PRD boundary table: 1600 ms). The TTS provider is auto-selected
from the environment via get_tts (ElevenLabs > Azure > Edge) — needs network.
Writes backend/out/full_example.wav.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np

from app.api.deps import get_tts
from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize
from app.render.audio import dsp
from app.render.audio.pipeline import render_scene_audio

FIXTURE = BACKEND_ROOT / "tests" / "fixtures" / "sample.fountain"
OUT_WAV = BACKEND_ROOT / "out" / "full_example.wav"

# None = narrator (action/description + narration/V.O. that has no speaker).
VOICE_MAP: dict[str | None, str] = {
    None: "en-US-ChristopherNeural",
    "MARA": "en-US-AriaNeural",   # expressive — carries the whispered line
    "TOM": "en-GB-RyanNeural",
}

SCENE_GAP_MS = 1600  # PRD boundary table: scene change
_SPOKEN = {"dialogue", "action", "narration"}


async def main() -> int:
    graph = normalize(parse_fountain(FIXTURE.read_text(encoding="utf-8")))
    scenes = [s for s in graph.scenes if any(l.kind in _SPOKEN and l.text.strip() for l in s.lines)]
    tts = get_tts()  # provider auto-selected from the environment (ElevenLabs > Azure > Edge)
    print(f"tts provider: {type(tts).__name__}")
    gap = np.zeros(round(SCENE_GAP_MS * dsp.SR / 1000), dtype=np.float32)

    pieces: list[np.ndarray] = []
    for scene in scenes:
        result = await render_scene_audio(scene, VOICE_MAP, tts, seed=7)
        samples, _ = dsp.read_wav_bytes(result.wav_bytes)
        emos = sorted({l.emotion for l in scene.lines if l.emotion})
        print(
            f"scene {scene.ordinal}: {scene.slugline or '(preamble)'}\n"
            f"    {result.clip_count} clips, {result.duration_ms} ms, "
            f"ambience={result.ambience_tags}"
            + (f", emotion={emos}" if emos else "")
            + (f", sfx={result.sfx_events}" if result.sfx_events else "")
        )
        if pieces:
            pieces.append(gap)
        pieces.append(samples)

    full = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    OUT_WAV.parent.mkdir(parents=True, exist_ok=True)
    OUT_WAV.write_bytes(dsp.wav_bytes(full, dsp.SR))
    print(f"\nwrote {OUT_WAV}")
    print(f"scenes={len(scenes)} total_ms={round(len(full) / dsp.SR * 1000)} bytes={OUT_WAV.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

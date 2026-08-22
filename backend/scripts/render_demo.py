"""Render the keeper's-room scene from the sample Fountain fixture.

Uses EdgeTTSAdapter (real Microsoft neural voices) and the numpy mix engine.
Writes backend/out/demo_scene.wav.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.edge import EdgeTTSAdapter
from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize
from app.render.audio.pipeline import render_scene_audio

FIXTURE = BACKEND_ROOT / "tests" / "fixtures" / "sample.fountain"
OUT_DIR = BACKEND_ROOT / "out"
OUT_WAV = OUT_DIR / "demo_scene.wav"

VOICE_MAP: dict[str | None, str] = {
    None: "en-US-ChristopherNeural",
    "MARA": "en-US-JennyNeural",
    "TOM": "en-GB-RyanNeural",
}

SCENE_ORDINAL = 2  # INT. LIGHTHOUSE - KEEPER'S ROOM - NIGHT
MAX_ATTEMPTS = 2


async def main() -> int:
    fountain = FIXTURE.read_text(encoding="utf-8")
    graph = normalize(parse_fountain(fountain))
    scene = next((s for s in graph.scenes if s.ordinal == SCENE_ORDINAL), None)
    if scene is None:
        print(f"scene ordinal {SCENE_ORDINAL} not found")
        return 1

    tts = EdgeTTSAdapter()
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = await render_scene_audio(scene, VOICE_MAP, tts, seed=7)
            break
        except Exception as exc:  # network / provider — retry once, then report
            last_error = exc
            print(f"attempt {attempt} failed: {exc}")
            result = None
    else:
        print(f"demo render aborted after {MAX_ATTEMPTS} failures: {last_error}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_WAV.write_bytes(result.wav_bytes)
    print(f"wrote {OUT_WAV}")
    print(f"bytes={OUT_WAV.stat().st_size}")
    print(f"duration_ms={result.duration_ms}")
    print(f"clip_count={result.clip_count}")
    print(f"ambience_tags={result.ambience_tags}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

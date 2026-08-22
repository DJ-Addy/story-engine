"""Real-network integration test for EdgeTTSAdapter.

Deselected by default via addopts = "-m 'not network'"; run explicitly with:
    python -m pytest tests/test_edge_integration.py -m network
"""

import io

import pytest
import soundfile as sf

from app.adapters.edge import EdgeTTSAdapter

pytestmark = pytest.mark.network


async def test_synthesize_real_voice() -> None:
    result = await EdgeTTSAdapter().synthesize(
        "The lighthouse beacon burned through the storm.",
        "en-US-ChristopherNeural",
        None,
        {},
    )
    assert result.cost_cents == 0
    assert result.duration_ms > 1000
    if result.gen_params["format"] == "wav":
        samples, sr = sf.read(io.BytesIO(result.audio_bytes), dtype="float32")
        assert len(samples) / sr > 1.0

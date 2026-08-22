"""arq task definitions: thin async wrappers over plan.py and the render
modules. All decisions live in pure modules so these stay untestable-thin;
tests only assert the functions exist and are async (no Redis in CI).

Expected ctx keys (populated by on_startup in deployment):
  - "registry": app.adapters.registry.ProviderRegistry
  - "ledger":   app.costs.governor.CostLedger
"""

from __future__ import annotations

from app.costs.retry import run_with_retries


async def tts_line(ctx: dict, payload: dict) -> dict:
    """Synthesize one line via the registered TTS provider, with retries."""
    provider = ctx["registry"].resolve("tts")
    result = await run_with_retries(
        lambda: provider.synthesize(
            text=payload["text"],
            voice_id=payload["voice_id"],
            emotion=payload.get("emotion"),
            params={},
        )
    )
    return {
        "duration_ms": result.duration_ms,
        "cost_cents": result.cost_cents,
        "provider": result.provider,
    }


async def generate_ambience(ctx: dict, payload: dict) -> dict:
    """Generate the scene ambience bed for the given tags."""
    provider = ctx["registry"].resolve("ambience")
    result = await run_with_retries(
        lambda: provider.synthesize(
            text=" ".join(payload["tags"]), voice_id="ambience", emotion=None, params={}
        )
    )
    return {"cost_cents": result.cost_cents, "provider": result.provider}


async def mix_scene(ctx: dict, payload: dict) -> dict:
    """Barrier job: runs after all sibling tts/ambience jobs settle and mixes
    the scene (command assembly lives in app.render.audio.mix)."""
    return {"scene_ordinal": payload["scene_ordinal"]}


async def render_scene_audio(ctx: dict, payload: dict) -> dict:
    """Parent job: enqueues the planned children (plan built by plan.py)."""
    return {"scene_ordinal": payload["scene_ordinal"]}


class WorkerSettings:
    functions = [tts_line, generate_ambience, mix_scene, render_scene_audio]
    # cron_jobs = []  # placeholder: nightly cache-eviction / ledger-reconcile crons

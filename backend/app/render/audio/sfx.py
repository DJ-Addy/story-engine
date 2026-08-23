"""One-shot sound-effect synthesis (pure numpy, no samples, no network).

These are discrete, foreground events — a door knock, a thunder crack, wind
bursting in — as opposed to the continuous ambience beds in ``dsp``. The
pipeline detects events in a scene's action text (``app.nlp.sound_events``)
and places these on the timeline in sync with the narration, so the sound
lands when the line describes it.

Every generator returns mono float32 at ``dsp.SR``, peak-normalized to a
sensible foreground level, and is deterministic given its ``rng``.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from app.render.audio import dsp

SR = dsp.SR


def _unit(x: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    return (x / peak).astype(np.float32) if peak > 0.0 else x.astype(np.float32)


def _noise(length: int, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(length).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


# --------------------------------------------------------------------------
# Impacts: knocks, slams, footsteps
# --------------------------------------------------------------------------


def _wood_hit(rng: np.random.Generator, decay: float, low_hz: float, high_hz: float) -> np.ndarray:
    """A damped wooden impact: band-passed noise body + a low thump."""
    dur = 0.16
    length = int(dur * SR)
    t = np.linspace(0.0, 1.0, length, dtype=np.float32)
    body = dsp.band_pass(_noise(length, rng), low_hz, high_hz)
    thump = np.sin(2.0 * np.pi * rng.uniform(70.0, 100.0) * np.linspace(0.0, dur, length))
    env = np.exp(-decay * t)
    return _unit(env * (0.8 * _unit(body) + 0.6 * thump.astype(np.float32)))


def knock(rng: np.random.Generator, hits: int = 3) -> np.ndarray:
    """Fist on a heavy door: a few firm, spaced thuds."""
    parts: list[np.ndarray] = []
    for i in range(hits):
        parts.append(0.95 * _wood_hit(rng, decay=26.0, low_hz=120.0, high_hz=700.0))
        if i < hits - 1:
            parts.append(_silence(rng.uniform(0.22, 0.32)))
    return np.concatenate(parts)


def door_slam(rng: np.random.Generator) -> np.ndarray:
    """A single heavy door bang: low body, fast decay, a little rattle."""
    dur = 0.35
    length = int(dur * SR)
    t = np.linspace(0.0, 1.0, length, dtype=np.float32)
    body = dsp.band_pass(_noise(length, rng), 55.0, 380.0)
    thump = np.sin(2.0 * np.pi * rng.uniform(60.0, 85.0) * np.linspace(0.0, dur, length))
    env = np.exp(-11.0 * t)
    return 0.97 * _unit(env * (0.7 * _unit(body) + 0.7 * thump.astype(np.float32)))


def _step(rng: np.random.Generator) -> np.ndarray:
    dur = 0.12
    length = int(dur * SR)
    t = np.linspace(0.0, 1.0, length, dtype=np.float32)
    body = dsp.band_pass(_noise(length, rng), 80.0, 550.0)
    return float(rng.uniform(0.55, 0.85)) * _unit(np.exp(-30.0 * t) * _unit(body))


def footsteps(rng: np.random.Generator, steps: int = 4) -> np.ndarray:
    """A short run of footfalls with a natural left/right lilt in spacing."""
    parts: list[np.ndarray] = []
    for i in range(steps):
        parts.append(_step(rng))
        if i < steps - 1:
            parts.append(_silence(rng.uniform(0.28, 0.40)))
    return 0.8 * np.concatenate(parts)


# --------------------------------------------------------------------------
# Doors and weather transients
# --------------------------------------------------------------------------


def door_open(rng: np.random.Generator) -> np.ndarray:
    """A drawn-out creak resolving into a latch click."""
    dur = 0.85
    length = int(dur * SR)
    t = np.linspace(0.0, 1.0, length, dtype=np.float32)
    base = dsp.band_pass(_noise(length, rng), 200.0, 950.0)
    wobble = 0.5 + 0.5 * np.sin(2.0 * np.pi * rng.uniform(6.0, 9.0) * t + rng.uniform(0.0, 6.28))
    env = np.minimum(t / 0.05, 1.0) * np.exp(-1.6 * t)
    creak = env * wobble.astype(np.float32) * _unit(base)

    click_len = int(0.025 * SR)
    ce = np.exp(-40.0 * np.linspace(0.0, 1.0, click_len, dtype=np.float32))
    click = ce * dsp.high_pass(_noise(click_len, rng), 2200.0)
    out = creak.copy()
    out[-click_len:] += 0.7 * _unit(click)
    return 0.8 * _unit(out)


def thunder(rng: np.random.Generator) -> np.ndarray:
    """A sharp crack followed by a long low rumble that rolls away."""
    crack_len = int(0.25 * SR)
    tc = np.linspace(0.0, 1.0, crack_len, dtype=np.float32)
    crack = np.exp(-9.0 * tc) * dsp.band_pass(_noise(crack_len, rng), 300.0, 4500.0)

    rum_len = int(rng.uniform(2.6, 3.6) * SR)
    tr = np.linspace(0.0, 1.0, rum_len, dtype=np.float32)
    env = np.minimum(tr / 0.05, 1.0) * np.exp(-3.0 * tr)
    rumble = env * dsp.low_pass(_noise(rum_len, rng), 110.0)

    out = 0.9 * _unit(rumble)
    out[:crack_len] += 0.6 * _unit(crack)
    return 0.95 * _unit(out)


def wind_gust(rng: np.random.Generator) -> np.ndarray:
    """A single swelling gust that rises and falls once."""
    dur = 1.6
    length = int(dur * SR)
    t = np.linspace(0.0, 1.0, length, dtype=np.float32)
    base = dsp.band_pass(_noise(length, rng), 120.0, 1400.0)
    # clamp before the fractional power: sin(pi) dips slightly negative at the
    # endpoint from float error, and (-x)**1.5 is NaN.
    swell = np.maximum(np.sin(np.pi * t), 0.0) ** 1.5
    return 0.72 * _unit(_unit(base) * swell.astype(np.float32))


_GENERATORS: dict[str, Callable[[np.random.Generator], np.ndarray]] = {
    "knock": knock,
    "door_open": door_open,
    "door_slam": door_slam,
    "footsteps": footsteps,
    "thunder": thunder,
    "wind_gust": wind_gust,
}

EVENTS: frozenset[str] = frozenset(_GENERATORS)


def synth_event(event: str, rng: np.random.Generator) -> np.ndarray:
    """Render one named sound event. Unknown names raise KeyError."""
    return _GENERATORS[event](rng)

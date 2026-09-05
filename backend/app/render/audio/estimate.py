"""Plan a scene's timeline without rendering it (no TTS, no DSP, no spend).

A scene that has never been rendered still has everything a timeline needs
except *measured* durations: the lines, their order, who speaks them, the shot
coverage, the ambience the location implies. This module supplies the one
missing input from the word-count heuristic in
:mod:`app.render.audio.durations` and then walks the *same* planner the render
uses (:func:`app.render.audio.timing.plan_speech_bus`), so an estimated scene
is laid out by the identical gap table and block rules rather than a parallel
guess. Swap the estimated durations for TTS output and the onsets become the
rendered ones.

What comes back is a :class:`SceneTiming` — the same shape the renderer emits —
so the API projects estimated and rendered lanes through one code path. It is
*not* a substitute for a render: durations are a reading-speed heuristic, and
callers must label the result as estimated (see ``SceneTimeline.timing_source``).

Nothing here touches a provider, the network, or the cost governor.
"""

from __future__ import annotations

from app.ingest.elements import NormalizedScene
from app.nlp.ambience import ambience_tags
from app.nlp.sound_events import detect_sound_events
from app.render.audio.durations import estimate_line_duration_ms
from app.render.audio.model import (
    DEFAULT_RENDER_SETTINGS,
    SPEECH_TAIL_MS,
    RenderedClip,
    RenderedSfx,
    SceneRenderSettings,
    SceneTiming,
)
from app.render.audio.timing import plan_speech_bus, speech_clips, spoken_lines

# Kinds whose description can trigger a foreground sound event. Mirrors
# app.render.audio.pipeline._EVENT_KINDS.
_EVENT_KINDS = {"action", "narration"}

# The renderer lays several events from one line back-to-back, each advanced by
# the length of the *synthesized* clip — a length that only exists once the DSP
# has run. Estimating that would mean inventing a per-event duration table, so
# the estimate instead spaces them by a single nominal step: enough that two
# events on one line are distinguishable on the lane, honest about being a
# placeholder for a measurement.
ESTIMATED_SFX_SPACING_MS = 600


def estimate_scene_timing(
    scene: NormalizedScene, settings: SceneRenderSettings | None = None
) -> SceneTiming:
    """Lay out ``scene`` from the IR alone, as the render would place it.

    ``settings`` applies the timeline editor's pacing knob to the gaps exactly
    as the renderer does, so an edit made before any render shows its effect.
    """
    settings = settings or DEFAULT_RENDER_SETTINGS
    lines = spoken_lines(scene)
    clips = speech_clips(
        lines,
        [estimate_line_duration_ms(line.text) for line in lines],
        scene.ordinal,
    )
    plan = plan_speech_bus(clips, gap_scale=settings.pacing)

    rendered_clips = [
        RenderedClip(
            line_ordinal=lines[entry.clip_index].ordinal,
            kind=lines[entry.clip_index].kind,
            character_name=entry.clip.character_name,
            emotion=lines[entry.clip_index].emotion,
            text=lines[entry.clip_index].text,
            start_ms=entry.start_ms,
            duration_ms=entry.clip.duration_ms,
        )
        for entry in plan.entries
    ]

    sfx: list[RenderedSfx] = []
    for entry in plan.entries:
        line = lines[entry.clip_index]
        if line.kind not in _EVENT_KINDS:
            continue
        for index, event in enumerate(detect_sound_events(line.text)):
            sfx.append(
                RenderedSfx(
                    at_ms=entry.start_ms + index * ESTIMATED_SFX_SPACING_MS, name=event
                )
            )

    action_text = " ".join(line.text for line in scene.lines if line.kind == "action")
    tags = ambience_tags(
        scene.location, scene.time_of_day, scene.interior, weather=None, action_text=action_text
    )

    # The same tail the renderer leaves after the last clip, so an estimated
    # scene length is comparable with a rendered one — and a scene with no
    # spoken lines at all still has a non-zero timeline to show ambience on.
    return SceneTiming(
        scene_ordinal=scene.ordinal,
        duration_ms=plan.total_ms + SPEECH_TAIL_MS,
        clips=rendered_clips,
        sfx=sfx,
        ambience_tags=tags,
    )

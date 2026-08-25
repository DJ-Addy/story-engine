"""Animatic / shot-list judge: score the quality of the previz coverage.

Deterministic and offline. Rather than reinventing cinematography checks it
**reuses the modules the project already ships**:

* coverage  — :func:`app.shotlist.coverage.uncovered_lines` /
  :func:`~app.shotlist.coverage.coverage_gaps` over the scene's dialogue beats.
* continuity — :func:`app.continuity.validator.validate_scene` (axis/180-rule,
  eyeline, screen-direction, lens-jump, time-of-day, missing-reverse), mapping
  each :class:`~app.shotlist.schema.ShotSpec` to a :class:`~app.continuity.model.ShotMeta`
  exactly as the scenes router does.
* pacing    — :func:`app.render.audio.durations.shot_duration_ms`, the same
  pre-audio estimate the animatic assembler uses.

Each scene is scored on four axes — coverage, continuity, variety, pacing —
combined into a per-scene score; the overall judgment is the shot-count-weighted
mean, with a single ranked list of the concrete findings behind the numbers.
"""

from __future__ import annotations

from app.continuity.model import Finding, SceneContext, ShotMeta
from app.continuity.validator import validate_scene
from app.ingest.elements import NormalizedScene, StoryGraph
from app.judge.model import AnimaticFinding, AnimaticJudgment, SceneAnimaticScore
from app.render.audio.durations import shot_duration_ms
from app.shotlist.coverage import coverage_gaps, uncovered_lines
from app.shotlist.schema import SceneShotList

# Scene score = weighted blend of the four axes.
_AXIS_WEIGHTS = {"coverage": 0.35, "continuity": 0.30, "variety": 0.15, "pacing": 0.20}

# Continuity penalty per finding, by severity (capped so the axis floors at 0).
_SEVERITY_PENALTY = {"error": 0.34, "warn": 0.17, "info": 0.05}
_SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2}

# Variety targets: how many distinct sizes/movements a scene "should" reach,
# scaled down for scenes with only a couple of shots (they can't vary much).
_SIZE_TARGET = 4
_MOVE_TARGET = 3
_MONOTONY_MIN_SHOTS = 3  # below this, a single repeated size isn't worth flagging

# Pacing: a shot estimated longer than this reads as an unbroken take; a scene
# whose dialogue lines per shot exceed the ratio is under-covered for its length.
_LONG_TAKE_MS = 20_000
_LINES_PER_SHOT_GATE = 4.0


def _round(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def _to_shot_meta(shot_list: SceneShotList) -> list[ShotMeta]:
    """Map ShotSpecs to continuity ShotMeta (mirrors the scenes router)."""
    return [
        ShotMeta(
            ordinal=s.ordinal,
            size=s.size,
            subject_ids=s.subjects,
            axis_side=s.axis_side,
            lens_mm=s.lens_mm,
            camera_height=s.camera_height,
            movement=s.movement,
            eyeline=s.eyeline,
        )
        for s in shot_list.shots
    ]


def _scene_context(graph: StoryGraph, scene: NormalizedScene) -> SceneContext:
    speakers = [
        line.character_name
        for line in scene.lines
        if line.kind == "dialogue" and line.character_name
    ]
    ordered = sorted(graph.scenes, key=lambda s: s.ordinal)
    prev_time = None
    for index, candidate in enumerate(ordered):
        if candidate.ordinal == scene.ordinal:
            prev_time = ordered[index - 1].time_of_day if index > 0 else None
            break
    return SceneContext(
        ordinal=scene.ordinal,
        dialogue_speakers=speakers,
        character_names={name: name for name in speakers},
        time_of_day=scene.time_of_day,
        prev_scene_time_of_day=prev_time,
    )


def _coverage_axis(
    scene: NormalizedScene, shot_list: SceneShotList
) -> tuple[float, list[AnimaticFinding]]:
    dialogue = [line.ordinal for line in scene.lines if line.kind == "dialogue"]
    if not dialogue:
        return 1.0, []  # nothing to cover — vacuously complete
    missing = uncovered_lines(shot_list, dialogue)
    score = (len(dialogue) - len(missing)) / len(dialogue)
    findings: list[AnimaticFinding] = []
    if missing:
        gaps = coverage_gaps(shot_list, dialogue)
        pretty = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in gaps)
        findings.append(
            AnimaticFinding(
                code="COVERAGE_GAP",
                severity="error" if score < 0.75 else "warn",
                message=(
                    f"{len(missing)} of {len(dialogue)} dialogue beat(s) are uncovered "
                    f"(line(s) {pretty}); regenerate only the gap."
                ),
                scene_ordinal=scene.ordinal,
            )
        )
    return score, findings


def _continuity_axis(
    finding_models: list[Finding], scene_ordinal: int
) -> tuple[float, list[AnimaticFinding]]:
    penalty = sum(_SEVERITY_PENALTY.get(f.severity, 0.1) for f in finding_models)
    score = max(0.0, 1.0 - penalty)
    findings = [
        AnimaticFinding(
            code=f.rule_code or "CONTINUITY",
            severity=f.severity,
            message=f.message,
            scene_ordinal=scene_ordinal,
            shot_ordinal=f.shot_ordinal,
        )
        for f in finding_models
    ]
    return score, findings


def _variety_axis(
    scene_ordinal: int, shot_list: SceneShotList
) -> tuple[float, list[AnimaticFinding]]:
    shots = shot_list.shots
    n = len(shots)
    sizes = {s.size for s in shots}
    moves = {s.movement for s in shots}
    size_div = min(1.0, len(sizes) / min(n, _SIZE_TARGET))
    move_div = min(1.0, len(moves) / min(n, _MOVE_TARGET))
    score = 0.6 * size_div + 0.4 * move_div
    findings: list[AnimaticFinding] = []
    if n >= _MONOTONY_MIN_SHOTS and len(sizes) == 1:
        findings.append(
            AnimaticFinding(
                code="SHOT_MONOTONY",
                severity="warn",
                message=(
                    f"All {n} shots use the same size '{next(iter(sizes))}'; "
                    "vary the coverage for visual rhythm."
                ),
                scene_ordinal=scene_ordinal,
            )
        )
    return score, findings


def _pacing_axis(
    scene: NormalizedScene, shot_list: SceneShotList
) -> tuple[float, list[AnimaticFinding]]:
    text_by_ordinal = {line.ordinal: line.text for line in scene.lines}
    findings: list[AnimaticFinding] = []
    penalty = 0.0
    for shot in shot_list.shots:
        texts = [text_by_ordinal.get(o, "") for o in shot.covers_lines]
        duration = shot_duration_ms(texts, boundary_gaps_ms=0)
        if duration > _LONG_TAKE_MS:
            penalty += 0.2
            findings.append(
                AnimaticFinding(
                    code="PACING_LONG_TAKE",
                    severity="info",
                    message=(
                        f"Shot {shot.ordinal} runs ~{duration / 1000:.1f}s across "
                        f"{len(shot.covers_lines)} line(s); consider breaking it up."
                    ),
                    scene_ordinal=scene.ordinal,
                    shot_ordinal=shot.ordinal,
                )
            )

    dialogue = sum(1 for line in scene.lines if line.kind == "dialogue")
    if shot_list.shots and dialogue:
        lines_per_shot = dialogue / len(shot_list.shots)
        if lines_per_shot > _LINES_PER_SHOT_GATE:
            penalty += 0.25
            findings.append(
                AnimaticFinding(
                    code="PACING_UNDERCOVERED",
                    severity="warn",
                    message=(
                        f"{dialogue} dialogue lines across only {len(shot_list.shots)} shot(s) "
                        f"(~{lines_per_shot:.1f} lines/shot); the scene may feel static."
                    ),
                    scene_ordinal=scene.ordinal,
                )
            )
    return max(0.0, 1.0 - penalty), findings


def _rank_findings(findings: list[AnimaticFinding]) -> list[AnimaticFinding]:
    return sorted(
        findings,
        key=lambda f: (
            _SEVERITY_RANK.get(f.severity, 3),
            f.scene_ordinal if f.scene_ordinal is not None else -1,
            f.shot_ordinal if f.shot_ordinal is not None else -1,
            f.code,
        ),
    )


def _score_scene(
    graph: StoryGraph, scene: NormalizedScene, shot_list: SceneShotList, grammar_profile: str
) -> SceneAnimaticScore:
    coverage, cov_findings = _coverage_axis(scene, shot_list)
    continuity_models = validate_scene(
        _scene_context(graph, scene), _to_shot_meta(shot_list), grammar_profile
    )
    continuity, cont_findings = _continuity_axis(continuity_models, scene.ordinal)
    variety, var_findings = _variety_axis(scene.ordinal, shot_list)
    pacing, pace_findings = _pacing_axis(scene, shot_list)

    score = (
        _AXIS_WEIGHTS["coverage"] * coverage
        + _AXIS_WEIGHTS["continuity"] * continuity
        + _AXIS_WEIGHTS["variety"] * variety
        + _AXIS_WEIGHTS["pacing"] * pacing
    )
    return SceneAnimaticScore(
        scene_ordinal=scene.ordinal,
        score=_round(score),
        coverage_score=_round(coverage),
        continuity_score=_round(continuity),
        variety_score=_round(variety),
        pacing_score=_round(pacing),
        shot_count=len(shot_list.shots),
        findings=_rank_findings(cov_findings + cont_findings + var_findings + pace_findings),
    )


def judge_animatic(
    graph: StoryGraph,
    shotlists: list[SceneShotList],
    grammar_profile: str = "classical",
) -> AnimaticJudgment:
    """Judge the previz coverage of every scene that has a shot list.

    ``shotlists`` are matched to their scene by ``scene_ordinal``; shot lists
    with no matching scene in the graph are skipped. The overall scores are the
    shot-count-weighted means of the per-scene axes (every scored scene carries
    a floor weight of 1), and ``findings`` is one ranked list across all scenes.
    """
    scenes_by_ordinal = {s.ordinal: s for s in graph.scenes}
    scene_scores: list[SceneAnimaticScore] = []
    for shot_list in sorted(shotlists, key=lambda s: s.scene_ordinal):
        scene = scenes_by_ordinal.get(shot_list.scene_ordinal)
        if scene is None:
            continue
        scene_scores.append(_score_scene(graph, scene, shot_list, grammar_profile))

    if not scene_scores:
        return AnimaticJudgment(
            overall_score=0.0,
            rationale="No shot lists matched a scene in the story graph; nothing to judge.",
            coverage_score=0.0,
            continuity_score=0.0,
            variety_score=0.0,
            pacing_score=0.0,
            scenes=[],
            findings=[],
        )

    def _weighted(attr: str) -> float:
        num = sum(getattr(s, attr) * max(1, s.shot_count) for s in scene_scores)
        den = sum(max(1, s.shot_count) for s in scene_scores)
        return _round(num / den)

    all_findings = _rank_findings([f for s in scene_scores for f in s.findings])
    overall = _weighted("score")
    warn_or_worse = sum(1 for f in all_findings if f.severity in ("warn", "error"))
    rationale = (
        f"Judged {len(scene_scores)} scene(s); overall animatic quality {overall:.2f} "
        f"with {warn_or_worse} finding(s) at warn or above across coverage, continuity, "
        "variety, and pacing."
    )
    return AnimaticJudgment(
        overall_score=overall,
        rationale=rationale,
        coverage_score=_weighted("coverage_score"),
        continuity_score=_weighted("continuity_score"),
        variety_score=_weighted("variety_score"),
        pacing_score=_weighted("pacing_score"),
        scenes=scene_scores,
        findings=all_findings,
    )

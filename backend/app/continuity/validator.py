"""Scene-level continuity validation entry point."""

from __future__ import annotations

from .model import Finding, SceneContext, ShotMeta
from .profiles import active_rules


def validate_scene(
    scene: SceneContext, shots: list[ShotMeta], profile: str = "classical"
) -> list[Finding]:
    ordered = sorted(shots, key=lambda s: s.ordinal)
    findings: list[Finding] = []
    for r in active_rules(profile):
        findings.extend(r.run(scene, ordered))
    findings.sort(
        key=lambda f: (
            f.shot_ordinal is None,
            f.shot_ordinal if f.shot_ordinal is not None else -1,
            f.rule_code,
        )
    )
    return findings

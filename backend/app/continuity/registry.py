"""Rule registry: @rule decorator and lookup of registered rules."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .model import Finding, SceneContext, Severity, ShotMeta

# Rule bodies yield Finding instances or plain dicts of Finding fields;
# the decorator stamps rule_code and severity either way.
RuleFunc = Callable[[SceneContext, list[ShotMeta]], Iterable[Finding | dict[str, Any]]]


@dataclass(frozen=True)
class Rule:
    code: str
    severity: Severity
    profiles: frozenset[str]
    run: Callable[[SceneContext, list[ShotMeta]], list[Finding]]


_REGISTRY: dict[str, Rule] = {}


def rule(
    code: str, severity: Severity, profiles: set[str]
) -> Callable[[RuleFunc], RuleFunc]:
    def decorator(func: RuleFunc) -> RuleFunc:
        if code in _REGISTRY:
            raise ValueError(f"duplicate rule code: {code}")

        def run(scene: SceneContext, shots: list[ShotMeta]) -> list[Finding]:
            findings: list[Finding] = []
            for item in func(scene, shots):
                if isinstance(item, Finding):
                    findings.append(
                        item.model_copy(update={"rule_code": code, "severity": severity})
                    )
                else:
                    findings.append(Finding(rule_code=code, severity=severity, **item))
            return findings

        _REGISTRY[code] = Rule(code=code, severity=severity, profiles=frozenset(profiles), run=run)
        return func

    return decorator


def registered_rules() -> list[Rule]:
    # Sorted by code so downstream iteration order never depends on import order.
    return sorted(_REGISTRY.values(), key=lambda r: r.code)

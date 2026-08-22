"""Grammar profiles and profile-based rule selection."""

from __future__ import annotations

from .registry import Rule, registered_rules

GRAMMAR_PROFILES: set[str] = {"classical", "handheld", "symmetrical", "anime"}


def active_rules(profile: str) -> list[Rule]:
    if profile not in GRAMMAR_PROFILES:
        raise ValueError(f"unknown grammar profile: {profile!r}")
    # Imported lazily so profiles.py can be imported from rules.py without a cycle.
    from . import rules  # noqa: F401

    return [r for r in registered_rules() if profile in r.profiles]

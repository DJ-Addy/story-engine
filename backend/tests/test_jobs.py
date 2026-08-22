"""Tests for fan-out/fan-in job planning (PRD §5.2) and tasks.py wiring."""

import asyncio
import copy
import math

import pytest

from app.adapters.fake import FakeTTS
from app.costs.governor import CostCapExceeded, guard
from app.workers.plan import (
    JobSpec,
    estimate_render_cost_cents,
    failed_children,
    plan_scene_audio_render,
    reconcile_children,
)

LINES = [
    {"ordinal": 1, "text": "It was a dark and stormy night.", "character_name": None, "emotion": "calm"},
    {"ordinal": 2, "text": "Who goes there?", "character_name": "GUARD", "emotion": "alarmed"},
    {"ordinal": 3, "text": "Only me, the humble traveler.", "character_name": "TRAVELER", "emotion": "weary"},
]

VOICE_MAP = {None: "fake-narrator", "GUARD": "fake-hero", "TRAVELER": "fake-hero"}

AMBIENCE_TAGS = ["rain", "thunder"]


def make_plan(lines=None):
    return plan_scene_audio_render(
        scene_ordinal=7,
        lines=copy.deepcopy(lines if lines is not None else LINES),
        voice_map=dict(VOICE_MAP),
        ambience_tags=list(AMBIENCE_TAGS),
    )


class TestFanOutShape:
    def test_parent_kind_and_barrier(self):
        plan = make_plan()
        assert plan.kind == "render_scene_audio"
        assert plan.barrier is False

    def test_children_count_and_kinds_in_order(self):
        plan = make_plan()
        # 3 tts children, then ambience, then barrier mix — mix strictly last.
        assert len(plan.children) == 5
        assert [c.kind for c in plan.children] == [
            "tts_line",
            "tts_line",
            "tts_line",
            "generate_ambience",
            "mix_scene",
        ]

    def test_tts_children_follow_line_order_and_payload(self):
        plan = make_plan()
        tts = [c for c in plan.children if c.kind == "tts_line"]
        assert [c.payload["line_ordinal"] for c in tts] == [1, 2, 3]
        assert tts[1].payload["text"] == "Who goes there?"
        assert tts[1].payload["voice_id"] == "fake-hero"
        assert tts[1].payload["emotion"] == "alarmed"

    def test_ambience_child_payload(self):
        plan = make_plan()
        amb = plan.children[3]
        assert amb.payload["tags"] == AMBIENCE_TAGS
        assert amb.payload["scene_ordinal"] == 7
        assert amb.barrier is False

    def test_mix_child_is_barrier_and_last(self):
        plan = make_plan()
        mix = plan.children[-1]
        assert mix.kind == "mix_scene"
        assert mix.barrier is True

    def test_narrator_line_resolves_none_key_voice(self):
        plan = make_plan()
        narrator_child = plan.children[0]
        assert LINES[0]["character_name"] is None
        assert narrator_child.payload["voice_id"] == "fake-narrator"

    def test_children_default_empty(self):
        spec = JobSpec(kind="x", idempotency_key="k", payload={})
        assert spec.children == []
        assert spec.barrier is False


class TestIdempotencyKeys:
    def test_keys_stable_across_two_identical_plans(self):
        a, b = make_plan(), make_plan()
        assert a.idempotency_key == b.idempotency_key
        assert [c.idempotency_key for c in a.children] == [
            c.idempotency_key for c in b.children
        ]

    def test_keys_differ_across_different_inputs(self):
        a = make_plan()
        changed = copy.deepcopy(LINES)
        changed[1]["text"] = "WHO GOES THERE?!"
        b = make_plan(changed)
        assert a.idempotency_key != b.idempotency_key
        # The changed line's tts child key differs; untouched lines keep theirs.
        assert a.children[1].idempotency_key != b.children[1].idempotency_key
        assert a.children[0].idempotency_key == b.children[0].idempotency_key

    def test_tts_key_matches_idempotency_key_helper(self):
        from app.costs.retry import idempotency_key

        plan = make_plan()
        child = plan.children[0]
        assert child.idempotency_key == idempotency_key("tts_line", child.payload)

    def test_all_keys_unique_within_plan(self):
        plan = make_plan()
        keys = [plan.idempotency_key] + [c.idempotency_key for c in plan.children]
        assert len(set(keys)) == len(keys)


class TestCostEstimate:
    def test_exact_fake_tts_formula(self):
        provider = FakeTTS()
        expected = sum(math.ceil(len(line["text"]) / 100) for line in LINES) + 10
        assert estimate_render_cost_cents(LINES, provider) == expected

    def test_custom_ambience_flat(self):
        provider = FakeTTS()
        base = estimate_render_cost_cents(LINES, provider, ambience_flat_cents=0)
        assert estimate_render_cost_cents(LINES, provider, ambience_flat_cents=25) == base + 25


class TestGovernorIntegration:
    def test_guard_raises_when_estimate_exceeds_remaining_cap(self):
        estimate = estimate_render_cost_cents(LINES, FakeTTS())
        assert estimate > 0
        with pytest.raises(CostCapExceeded):
            guard(spent_cents=100, cap_cents=100 + estimate - 1, estimated_cents=estimate)

    def test_guard_passes_at_exact_boundary(self):
        estimate = estimate_render_cost_cents(LINES, FakeTTS())
        guard(spent_cents=100, cap_cents=100 + estimate, estimated_cents=estimate)


class TestReconcile:
    def test_all_succeeded(self):
        assert reconcile_children([("a", "succeeded"), ("b", "succeeded")]) == "succeeded"

    def test_some_failed_is_partial_not_failed(self):
        # PRD: a failed child does NOT fail the parent.
        assert reconcile_children([("a", "succeeded"), ("b", "failed")]) == "partial"

    def test_none_succeeded(self):
        assert reconcile_children([("a", "failed"), ("b", "failed")]) == "failed"

    def test_failed_children_extracts_keys(self):
        results = [("a", "succeeded"), ("b", "failed"), ("c", "failed")]
        assert failed_children(results) == ["b", "c"]

    def test_failed_children_empty_when_all_ok(self):
        assert failed_children([("a", "succeeded")]) == []


class TestTasksModule:
    def test_task_functions_exist_and_are_async(self):
        from app.workers import tasks

        for name in ("tts_line", "generate_ambience", "mix_scene", "render_scene_audio"):
            fn = getattr(tasks, name)
            assert asyncio.iscoroutinefunction(fn)

    def test_worker_settings_lists_tasks(self):
        from app.workers import tasks

        assert hasattr(tasks, "WorkerSettings")
        assert tasks.tts_line in tasks.WorkerSettings.functions

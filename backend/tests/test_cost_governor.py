"""Tests for the cost governor: guard function and CostLedger."""

import pytest

from app.costs.governor import CostCapExceeded, CostLedger, guard


class TestGuard:
    def test_passes_when_under_cap(self) -> None:
        guard(spent_cents=10, cap_cents=100, estimated_cents=50)

    def test_passes_exactly_at_cap(self) -> None:
        # spent + estimated == cap must NOT raise (PRD §5.3 boundary).
        guard(spent_cents=60, cap_cents=100, estimated_cents=40)

    def test_raises_one_cent_over_cap(self) -> None:
        with pytest.raises(CostCapExceeded):
            guard(spent_cents=60, cap_cents=100, estimated_cents=41)

    def test_raises_when_already_over_cap(self) -> None:
        with pytest.raises(CostCapExceeded):
            guard(spent_cents=150, cap_cents=100, estimated_cents=0)

    def test_exception_carries_spent_cap_requested(self) -> None:
        with pytest.raises(CostCapExceeded) as exc_info:
            guard(spent_cents=90, cap_cents=100, estimated_cents=20)
        exc = exc_info.value
        assert exc.spent == 90
        assert exc.cap == 100
        assert exc.requested == 20

    def test_zero_cap_zero_estimate_passes(self) -> None:
        guard(spent_cents=0, cap_cents=0, estimated_cents=0)

    def test_zero_cap_nonzero_estimate_raises(self) -> None:
        with pytest.raises(CostCapExceeded):
            guard(spent_cents=0, cap_cents=0, estimated_cents=1)


class TestCostLedger:
    def test_spent_starts_at_zero(self) -> None:
        assert CostLedger().spent("proj-1") == 0

    def test_record_accumulates(self) -> None:
        ledger = CostLedger()
        ledger.record("proj-1", "job-a", 25)
        ledger.record("proj-1", "job-b", 75)
        assert ledger.spent("proj-1") == 100

    def test_projects_are_isolated(self) -> None:
        ledger = CostLedger()
        ledger.record("proj-1", "job-a", 30)
        ledger.record("proj-2", "job-b", 70)
        assert ledger.spent("proj-1") == 30
        assert ledger.spent("proj-2") == 70

    def test_ledger_feeds_guard(self) -> None:
        ledger = CostLedger()
        ledger.record("proj-1", "job-a", 95)
        with pytest.raises(CostCapExceeded):
            guard(spent_cents=ledger.spent("proj-1"), cap_cents=100, estimated_cents=10)

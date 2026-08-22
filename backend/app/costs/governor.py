"""Cost governor: cap enforcement (PRD §5.3) and per-project spend ledger."""


class CostCapExceeded(Exception):
    def __init__(self, spent: int, cap: int, requested: int) -> None:
        self.spent = spent
        self.cap = cap
        self.requested = requested
        super().__init__(
            f"cost cap exceeded: spent {spent}c + requested {requested}c > cap {cap}c"
        )


def guard(spent_cents: int, cap_cents: int, estimated_cents: int) -> None:
    """Raise CostCapExceeded if spending estimated_cents would exceed the cap.

    Pure function. Boundary rule (PRD §5.3): spent + estimated == cap passes;
    strictly greater raises.
    """
    if spent_cents + estimated_cents > cap_cents:
        raise CostCapExceeded(spent=spent_cents, cap=cap_cents, requested=estimated_cents)


class CostLedger:
    """In-memory spend ledger keyed by project.

    A DB-backed implementation will replace this later behind the same
    record()/spent() interface.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[str, str, int]] = []

    def record(self, project_id: str, job_id: str, cost_cents: int) -> None:
        self._entries.append((project_id, job_id, cost_cents))

    def spent(self, project_id: str) -> int:
        return sum(cost for pid, _, cost in self._entries if pid == project_id)

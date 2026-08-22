"""Capability-keyed provider registry with ordered fallback.

Capabilities in use: 'tts', 'image', 'llm' ('video' reserved for later).
Registration order is priority order: resolve() returns the first-registered
provider; resolve_all() returns the full ordered list for fallback chains.
"""

from collections import defaultdict
from typing import Any


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, list[Any]] = defaultdict(list)

    def register(self, capability: str, provider: Any) -> None:
        self._providers[capability].append(provider)

    def resolve(self, capability: str) -> Any:
        providers = self._providers.get(capability)
        if not providers:
            raise KeyError(f"no provider registered for capability {capability!r}")
        return providers[0]

    def resolve_all(self, capability: str) -> list[Any]:
        return list(self._providers.get(capability, []))

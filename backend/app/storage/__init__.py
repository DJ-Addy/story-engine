"""Durable storage for rendered media.

A Cloud Run container's filesystem is ephemeral and its memory is not free, yet
the render path currently keeps every scene WAV and every Veo MP4 in the
in-memory repository: a restart loses them and a long session grows the
container until it is evicted. This package gives those bytes a home in Google
Cloud Storage.

**The switch is a bucket.** ``get_object_store()`` returns ``None`` whenever
``GCS_BUCKET`` is unset, and every call site treats ``None`` as "keep doing what
we did before" — bytes stay in the repository and are served straight back. So
the app runs identically with no cloud account, and the offline test suite never
so much as constructs an HTTP client. See :mod:`app.storage.settings` for the
full environment contract.

**It is a dependency, not a global.** Routers take the store through
``Depends(get_object_store)`` so one ``dependency_overrides`` entry redirects
both render paths at a test double — the same seam the analytics recorder uses.
The concrete implementation is imported lazily inside the factory, keeping
``aiohttp`` and the google-auth stack out of the import graph for offline
collection.

**Best effort, never fatal.** Failing to park a render must not fail the render:
the call sites log and keep the local bytes. Losing durability is a degradation;
losing the render the user just paid a provider for is not.
"""

from __future__ import annotations

from app.storage.base import (
    ObjectNotFound,
    ObjectStore,
    ObjectStoreError,
    ObjectStoreUnavailable,
    parse_gs_uri,
)
from app.storage.settings import StorageSettings

__all__ = [
    "ObjectNotFound",
    "ObjectStore",
    "ObjectStoreError",
    "ObjectStoreUnavailable",
    "StorageSettings",
    "get_object_store",
    "parse_gs_uri",
    "reset_object_store_cache",
]

# Stores are cached by their resolved configuration, not created per request:
# a fresh ``GoogleTokenSource`` would re-resolve ADC and mint a new bearer token
# on every single render. Keyed on the configuration so a monkeypatched
# environment produces a different store rather than a stale one.
_STORES: dict[tuple, ObjectStore] = {}


def get_object_store() -> ObjectStore | None:
    """The configured media store, or ``None`` when there is no bucket.

    ``None`` is the documented, supported state — not an error — and is what
    keeps local development and the offline test suite working unchanged.

    Used as a FastAPI dependency (``Depends(get_object_store)``) by the render
    and serve endpoints. The GCS implementation is imported here rather than at
    module scope so importing ``app.storage`` never pulls in ``aiohttp``.
    """
    settings = StorageSettings.from_env()
    if not settings.enabled:
        return None
    key = settings.cache_key()
    store = _STORES.get(key)
    if store is None:
        from app.storage.gcs import GCSObjectStore

        store = GCSObjectStore(settings)
        _STORES[key] = store
    return store


def reset_object_store_cache() -> None:
    """Drop cached stores. For tests that rewrite the storage environment."""
    _STORES.clear()

"""Object-store contracts: the error taxonomy, the protocol, and ``gs://`` parsing.

Deliberately stdlib + typing only. ``app.api.routers`` imports this module (via
``app.storage``) on every request path, so it must not drag ``aiohttp`` — or any
part of the cloud stack — into the import graph. The one implementation that
does talk HTTP lives in :mod:`app.storage.gcs` and is imported lazily by
``app.storage.get_object_store``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.storage.settings import StorageSettings


class ObjectStoreError(RuntimeError):
    """Base class for object-store failures.

    Separate from ``app.adapters.base.ProviderError`` on purpose: a bucket is
    not a generative provider, it spends no credits, and a caller that wants to
    treat "the render failed" and "we could not park the render" differently
    needs to be able to.
    """


class ObjectNotFound(ObjectStoreError):
    """The object does not exist (HTTP 404). Never worth retrying."""


class ObjectStoreUnavailable(ObjectStoreError):
    """Transient failure — network error, 429, or 5xx. Safe to retry."""


@runtime_checkable
class ObjectStore(Protocol):
    """Everything the render/serve endpoints need from durable media storage.

    Routers depend on this protocol rather than on ``GCSObjectStore`` so tests
    inject an in-memory double and never touch aiohttp, ADC or a real bucket.
    """

    settings: StorageSettings

    async def upload(self, name: str, data: bytes, content_type: str) -> str: ...

    async def fetch(self, name: str, bucket: str | None = None) -> bytes: ...

    async def fetch_uri(self, uri: str) -> bytes: ...

    async def serving_url(self, name: str) -> str: ...


def parse_gs_uri(uri: str) -> tuple[str, str]:
    """Split ``gs://bucket/path/to/object`` into ``(bucket, object_name)``.

    Pure and importable without any network access. Raises ``ValueError`` on
    anything that is not a fully-qualified object URI — a bare ``gs://bucket``
    or ``gs://bucket/`` names a prefix, not an object, and there is nothing
    honest to fetch from it.
    """
    if not uri.startswith("gs://"):
        raise ValueError(f"not a gs:// URI: {uri!r}")
    bucket, _, name = uri[len("gs://") :].partition("/")
    if not bucket or not name:
        raise ValueError(f"gs:// URI names no object: {uri!r}")
    return bucket, name

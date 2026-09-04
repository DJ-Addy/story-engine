"""GCSObjectStore: durable media storage over the raw Cloud Storage JSON API.

Cloud Run's filesystem is ephemeral and every rendered WAV/MP4 currently lives
in process memory, so a restart loses the work and a long session grows the
container. This is where those bytes go instead.

Talks to Cloud Storage over ``aiohttp`` (an installed dep); only ``google-auth``
is used, for ADC bearer tokens (see ``app.adapters.google_auth``). The
``google-cloud-storage`` SDK is deliberately NOT used — the same judgement the
TTS/Veo/Gemini adapters make: three URLs and a bearer token is the entire
surface, the dependency stays out of the image, and the transport is trivial to
mock at the ``ClientSession`` seam so no test ever needs a bucket.

Three operations, matching what the render endpoints need:

* ``upload``   — park finished render bytes and hand back their ``gs://`` URI.
* ``fetch``    — read them back for the serve endpoints.
* ``serving_url`` — a URL a browser can open: a V4 signed URL when a signer
  service account is configured, otherwise the plain (public-object) URL.

Signed URLs are minted through the IAM Credentials ``signBlob`` API rather than
a private key on disk, so a Cloud Run deployment needs no key file at all — just
``roles/iam.serviceAccountTokenCreator`` on the signer account. See
:mod:`app.storage.signing` for the (pure) canonical-request half.

ARCHITECTURAL LAW (PRD): construction never fails on missing credentials, so the
module imports and tests collect with no GCP setup; the first *call* is what
raises.

Docs: https://docs.cloud.google.com/storage/docs/json_api/v1/objects
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp

from app.adapters.base import ProviderError, classify_http_status
from app.adapters.google_auth import GoogleTokenSource, TokenSource
from app.storage.base import (
    ObjectNotFound,
    ObjectStoreError,
    ObjectStoreUnavailable,
    parse_gs_uri,
)
from app.storage.settings import GCS_HOST, StorageSettings
from app.storage.signing import build_signing_material

_IAM_CREDENTIALS_HOST = "https://iamcredentials.googleapis.com"

_NETWORK_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GCSObjectStore:
    """Bytes in, bytes out, plus a URL you can hand to a browser."""

    name = "gcs"

    def __init__(
        self,
        settings: StorageSettings,
        token_source: TokenSource | None = None,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        # Never resolve credentials here: the module must import and tests must
        # collect with no GCP setup. Resolution happens on the first call.
        self.settings = settings
        self._tokens: TokenSource = token_source or GoogleTokenSource()
        # Injected so signed-URL tests are deterministic without freezing time.
        self._clock = clock

    # -- plumbing -----------------------------------------------------------

    async def _bearer(self) -> str:
        try:
            return await self._tokens.token()
        except ProviderError as exc:
            # Credential failure is terminal: retrying without fixing ADC just
            # fails again, and the message already names the env vars to set.
            raise ObjectStoreError(f"gcs auth failed: {exc}") from exc

    async def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {await self._bearer()}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=self.settings.timeout_s)

    @staticmethod
    def _raise_for_status(status: int, data: bytes, op: str) -> None:
        """Raise the right store error for a non-2xx Cloud Storage response."""
        if 200 <= status < 300:
            return
        snippet = data[:200].decode("utf-8", "replace").strip()
        message = f"gcs {op} http {status}: {snippet}"
        if status == 404:
            raise ObjectNotFound(message)
        if classify_http_status(status) == "retryable":
            raise ObjectStoreUnavailable(message)
        raise ObjectStoreError(message)

    # -- operations ---------------------------------------------------------

    async def upload(
        self, name: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        """Write ``data`` to ``name`` in the configured bucket; return its ``gs://`` URI.

        Simple (``uploadType=media``) upload: one request, whole object,
        overwrite-on-write. Render artifacts are single-digit megabytes and a
        re-render is *meant* to replace the previous take, so neither resumable
        uploads nor generation preconditions would buy anything here.
        """
        url = (
            f"{GCS_HOST}/upload/storage/v1/b/{quote(self.settings.bucket, safe='')}"
            f"/o?uploadType=media&name={quote(name, safe='')}"
        )
        headers = await self._headers(content_type)
        try:
            async with aiohttp.ClientSession(timeout=self._timeout()) as session:
                async with session.post(url, data=data, headers=headers) as resp:
                    body = await resp.read()
                    self._raise_for_status(resp.status, body, "upload")
        except _NETWORK_ERRORS as exc:
            raise ObjectStoreUnavailable(f"gcs upload network failure: {exc}") from exc
        return self.settings.gs_uri(name)

    async def fetch(self, name: str, bucket: str | None = None) -> bytes:
        """Read one object's bytes back. Raises ``ObjectNotFound`` on a 404."""
        target = bucket or self.settings.bucket
        url = (
            f"{GCS_HOST}/storage/v1/b/{quote(target, safe='')}"
            f"/o/{quote(name, safe='')}?alt=media"
        )
        headers = await self._headers()
        try:
            async with aiohttp.ClientSession(timeout=self._timeout()) as session:
                async with session.get(url, headers=headers) as resp:
                    body = await resp.read()
                    self._raise_for_status(resp.status, body, "fetch")
        except _NETWORK_ERRORS as exc:
            raise ObjectStoreUnavailable(f"gcs fetch network failure: {exc}") from exc
        return body

    async def fetch_uri(self, uri: str) -> bytes:
        """Read back an object named by a ``gs://bucket/object`` URI.

        Provider-written clips (Veo's ``storageUri`` delivery) report their own
        URIs, which may name a different bucket than the configured one, so the
        bucket comes from the URI rather than from settings.
        """
        try:
            bucket, name = parse_gs_uri(uri)
        except ValueError as exc:
            raise ObjectStoreError(str(exc)) from exc
        return await self.fetch(name, bucket=bucket)

    # -- URLs ---------------------------------------------------------------

    def public_url(self, name: str) -> str:
        """Unsigned object URL; resolves only for publicly readable objects."""
        return self.settings.public_url(name)

    async def _sign(self, payload: str) -> str:
        """RSA-sign a string with the signer service account's own key.

        Uses IAM Credentials ``signBlob`` so no private key is ever present in
        the container. Returns the signature hex-encoded, which is the form a
        V4 signed URL carries.
        """
        service_account = self.settings.signer_service_account or ""
        url = (
            f"{_IAM_CREDENTIALS_HOST}/v1/projects/-/serviceAccounts/"
            f"{quote(service_account, safe='@')}:signBlob"
        )
        body = {"payload": base64.b64encode(payload.encode("utf-8")).decode("ascii")}
        headers = await self._headers("application/json")
        try:
            async with aiohttp.ClientSession(timeout=self._timeout()) as session:
                async with session.post(url, json=body, headers=headers) as resp:
                    data = await resp.read()
                    self._raise_for_status(resp.status, data, "signBlob")
        except _NETWORK_ERRORS as exc:
            raise ObjectStoreUnavailable(f"gcs signBlob network failure: {exc}") from exc
        try:
            signed = json.loads(data).get("signedBlob")
        except ValueError as exc:
            raise ObjectStoreError(f"gcs signBlob returned non-JSON body: {exc}") from exc
        if not signed:
            raise ObjectStoreError("gcs signBlob returned no signature")
        try:
            return base64.b64decode(signed).hex()
        except (ValueError, TypeError) as exc:
            raise ObjectStoreError(f"gcs signBlob signature is not base64: {exc}") from exc

    async def signed_url(
        self, name: str, *, expires_s: int | None = None, method: str = "GET"
    ) -> str:
        """A time-limited V4 signed URL for one object."""
        if not self.settings.signer_service_account:
            raise ObjectStoreError(
                "signed URLs require a signer identity; set GCS_SIGNER_SERVICE_ACCOUNT "
                "to a service account this runtime may impersonate"
            )
        string_to_sign, url = build_signing_material(
            self.settings.bucket,
            name,
            service_account=self.settings.signer_service_account,
            timestamp=self._clock(),
            expires_s=expires_s if expires_s is not None else self.settings.signed_url_ttl_s,
            method=method,
        )
        signature = await self._sign(string_to_sign)
        return f"{url}&X-Goog-Signature={signature}"

    async def serving_url(self, name: str) -> str:
        """The URL to hand a client: signed when a signer is configured, else public.

        Two honest postures rather than one broken one. With a signer the bucket
        stays private and links expire; without one the caller has chosen a
        publicly readable bucket and gets the plain object URL.
        """
        if self.settings.signer_service_account:
            return await self.signed_url(name)
        return self.public_url(name)

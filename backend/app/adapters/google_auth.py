"""Shared Google Cloud credential plumbing for the Google adapters.

Every Google Cloud REST call needs a short-lived OAuth2 bearer token minted from
Application Default Credentials (ADC). ``google-auth`` is the only Google library
the adapters depend on: it resolves ADC (``GOOGLE_APPLICATION_CREDENTIALS``, gcloud
user creds, or the metadata server on GCE/Cloud Run) and refreshes tokens. The
actual API traffic goes over ``aiohttp``, matching every other adapter in this
package — the transport stays trivial to mock and the dependency surface small.

ARCHITECTURAL LAW (PRD): no provider SDK is imported outside app/adapters/.
``google.auth`` is imported *lazily inside* ``_load`` so this module (and the
adapters importing it) still import cleanly when google-auth is not installed —
offline test collection must never require the cloud stack.

``google-auth`` is synchronous, so refreshes are pushed to a worker thread; the
token is cached and only refreshed when actually expired.
"""

from __future__ import annotations

import asyncio
import os
from typing import Protocol, runtime_checkable

from app.adapters.base import TerminalProviderError

# Every Google Cloud API used here (Text-to-Speech, Vertex AI) accepts the
# generic cloud-platform scope; ADC service accounts are scoped by IAM role.
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


@runtime_checkable
class TokenSource(Protocol):
    """Anything that can mint a bearer token and name the billing project.

    Adapters depend on this protocol rather than on ``GoogleTokenSource`` so
    tests inject a stub and never touch google-auth or the network.
    """

    async def token(self) -> str: ...

    def project(self) -> str: ...


class GoogleTokenSource:
    """ADC-backed bearer tokens, resolved and refreshed lazily.

    Construction never raises and never touches credentials, so the module
    imports and tests collect on a machine with no GCP setup at all. The first
    ``token()`` call is what fails, with a message naming the env vars to set.
    """

    def __init__(self, project: str | None = None, scopes: tuple[str, ...] | None = None) -> None:
        self._project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self._scopes = list(scopes or (CLOUD_PLATFORM_SCOPE,))
        self._credentials = None
        self._lock = asyncio.Lock()

    def _load(self) -> tuple[object, str | None]:
        """Resolve ADC. Runs in a worker thread; imports google-auth lazily."""
        try:
            import google.auth
        except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
            raise TerminalProviderError(
                "google-auth is not installed; run `pip install -e .` in backend/ "
                "to pull the Google Cloud dependencies"
            ) from exc
        from google.auth.exceptions import DefaultCredentialsError

        try:
            credentials, adc_project = google.auth.default(scopes=self._scopes)
        except DefaultCredentialsError as exc:
            raise TerminalProviderError(
                "no Google Cloud credentials found; set GOOGLE_APPLICATION_CREDENTIALS "
                "to a service-account key file or run `gcloud auth application-default login`"
            ) from exc
        return credentials, adc_project

    def _refresh(self, credentials: object) -> None:
        """Blocking token refresh. Runs in a worker thread."""
        from google.auth.transport.requests import Request

        credentials.refresh(Request())  # type: ignore[attr-defined]

    async def token(self) -> str:
        # Serialized so concurrent line renders don't stampede the refresh.
        async with self._lock:
            if self._credentials is None:
                self._credentials, adc_project = await asyncio.to_thread(self._load)
                if self._project is None:
                    self._project = adc_project
            credentials = self._credentials
            if not getattr(credentials, "valid", False):
                await asyncio.to_thread(self._refresh, credentials)
            token = getattr(credentials, "token", None)
            if not token:
                raise TerminalProviderError("google credentials produced no access token")
            return str(token)

    def project(self) -> str:
        """The billing/quota project. Raises when it cannot be determined.

        Some ADC flows (a user's gcloud login) carry no project, so an explicit
        ``GOOGLE_CLOUD_PROJECT`` is the reliable answer and the error says so.
        """
        if not self._project:
            raise TerminalProviderError(
                "no Google Cloud project configured; set GOOGLE_CLOUD_PROJECT"
            )
        return self._project

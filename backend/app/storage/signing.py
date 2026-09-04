"""V4 signed-URL construction for Cloud Storage — the pure half.

A signed URL normally needs a service-account *private key* on disk, which is
exactly what Cloud Run deployments are supposed to avoid. The alternative this
module is built for: compute the canonical request and string-to-sign here, then
have the IAM Credentials ``signBlob`` API produce the RSA signature using the
runtime service account's own key (see ``GCSObjectStore._sign``). No key
material ever reaches the container.

Everything here is pure, stdlib-only and deterministic given a timestamp, so the
signature layout is tested without a network call, credentials or a bucket.

Docs: https://docs.cloud.google.com/storage/docs/authentication/signatures
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from urllib.parse import quote

ALGORITHM = "GOOG4-RSA-SHA256"
SIGNED_HEADERS = "host"
DEFAULT_HOST = "storage.googleapis.com"

# Cloud Storage caps a V4 signature's validity at seven days.
MAX_EXPIRES_S = 7 * 24 * 60 * 60


def _canonical_query(params: dict[str, str]) -> str:
    # V4 requires the query to be sorted by encoded key, with every key and
    # value percent-encoded (nothing left unreserved but the RFC 3986 set).
    return "&".join(
        f"{quote(key, safe='')}={quote(value, safe='')}" for key, value in sorted(params.items())
    )


def build_signing_material(
    bucket: str,
    name: str,
    *,
    service_account: str,
    timestamp: datetime,
    expires_s: int,
    method: str = "GET",
    host: str = DEFAULT_HOST,
) -> tuple[str, str]:
    """Return ``(string_to_sign, url_without_signature)`` for one object.

    The caller signs ``string_to_sign`` with the service account's key and
    appends ``&X-Goog-Signature=<hex>`` to the returned URL. ``expires_s`` is
    clamped to Cloud Storage's seven-day maximum rather than being allowed to
    produce a URL the service will reject at use time.
    """
    if not service_account:
        raise ValueError("a signer service account is required to sign a URL")
    expires_s = max(1, min(int(expires_s), MAX_EXPIRES_S))
    datestamp = timestamp.strftime("%Y%m%d")
    request_timestamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    scope = f"{datestamp}/auto/storage/goog4_request"

    canonical_uri = f"/{bucket}/{quote(name, safe='/~')}"
    canonical_query = _canonical_query(
        {
            "X-Goog-Algorithm": ALGORITHM,
            "X-Goog-Credential": f"{service_account}/{scope}",
            "X-Goog-Date": request_timestamp,
            "X-Goog-Expires": str(expires_s),
            "X-Goog-SignedHeaders": SIGNED_HEADERS,
        }
    )
    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri,
            canonical_query,
            f"host:{host}",
            "",
            SIGNED_HEADERS,
            "UNSIGNED-PAYLOAD",
        ]
    )
    string_to_sign = "\n".join(
        [
            ALGORITHM,
            request_timestamp,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    return string_to_sign, f"https://{host}{canonical_uri}?{canonical_query}"

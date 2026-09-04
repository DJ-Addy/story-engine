"""Environment-driven configuration for durable media storage (GCS).

``GCS_BUCKET`` is the on/off switch, exactly as ``CLICKHOUSE_HOST`` is for the
analytics spine: without a bucket there is nowhere to put anything, so the whole
subsystem reports ``enabled is False`` and every call site keeps the bytes where
they are today (in the repository, in the container's memory). That is the
offline/dev default and it is what makes the API behave identically with and
without a cloud account.

Why this exists at all: Cloud Run's filesystem is ephemeral and the current
render path holds every WAV and MP4 in process memory, so a restart loses the
demo and a long session slowly eats the container. A bucket is the durable home;
this module decides whether there is one and what the object layout looks like.

Object layout is deliberately *deterministic* — derivable from
``(project_id, scene_ordinal, shot_ordinal)`` alone — because
``app.api.repo``'s render records carry no storage column to write a key into.
The serve endpoints recompute the key instead of reading it back.

Pure configuration + string building: no network, no aiohttp, no credentials.
"""

from __future__ import annotations

import os
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

# The public/JSON API host. Also the base for unsigned object URLs.
GCS_HOST = "https://storage.googleapis.com"

# Everything Story Engine writes lands under one top-level prefix so a shared
# bucket stays legible and a lifecycle rule can target our media alone.
DEFAULT_PREFIX = "renders"

# One hour is the usual compromise for a media URL handed to a browser: long
# enough to play a clip through, short enough that a leaked link expires.
DEFAULT_SIGNED_URL_TTL_S = 3600

# Uploads are whole render artifacts (a scene WAV can be megabytes), so the
# timeout is generous compared with the JSON API calls the adapters make.
DEFAULT_TIMEOUT_S = 60.0


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _clean_prefix(prefix: str) -> str:
    return prefix.strip().strip("/")


class StorageSettings(BaseModel):
    """Resolved media-storage configuration; construct with :meth:`from_env`."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    bucket: str = ""
    prefix: str = DEFAULT_PREFIX

    # Set to enable V4 signed URLs, minted through the IAM Credentials
    # ``signBlob`` API (no private key on disk — see app.storage.gcs). Unset,
    # ``serving_url`` returns the plain object URL, which only resolves for
    # publicly readable objects.
    signer_service_account: str | None = None
    signed_url_ttl_s: int = DEFAULT_SIGNED_URL_TTL_S

    # Override to put a CDN or a custom domain in front of the bucket.
    public_base_url: str = GCS_HOST
    timeout_s: float = DEFAULT_TIMEOUT_S

    # -- object layout ------------------------------------------------------

    def key(self, *parts: str | int) -> str:
        """Join path segments under the configured prefix."""
        segments = [str(part) for part in parts if str(part) != ""]
        if self.prefix:
            segments.insert(0, self.prefix)
        return "/".join(segments)

    def audio_object(self, project_id: str, scene_ordinal: int) -> str:
        """Where one scene's rendered WAV lives. One object per scene: a
        re-render replaces it, matching the repository's one-record-per-scene
        contract."""
        return self.key("projects", project_id, "scenes", scene_ordinal, "audio.wav")

    def video_object(self, project_id: str, scene_ordinal: int, shot_ordinal: int) -> str:
        """Where one shot's clip lives when *we* upload the bytes (i.e. the
        provider returned them inline instead of writing to the bucket)."""
        return self.key(
            "projects", project_id, "scenes", scene_ordinal, "shots", shot_ordinal, "clip.mp4"
        )

    def video_prefix_uri(self, project_id: str, scene_ordinal: int, shot_ordinal: int) -> str:
        """The ``storageUri`` handed to Veo for direct GCS delivery.

        Veo names the files itself, so this is a *directory* prefix and must end
        in a slash; the finished operation reports the exact ``gs://`` object
        URIs it wrote, which is what gets stored on the render record.
        """
        return self.gs_uri(
            self.key("projects", project_id, "scenes", scene_ordinal, "shots", shot_ordinal) + "/"
        )

    def gs_uri(self, name: str) -> str:
        return f"gs://{self.bucket}/{name}"

    def public_url(self, name: str) -> str:
        """Unsigned object URL. Resolves only if the object is publicly readable."""
        return f"{self.public_base_url.rstrip('/')}/{self.bucket}/{quote(name, safe='/~')}"

    # -- plumbing -----------------------------------------------------------

    def cache_key(self) -> tuple:
        """Identity for the process-wide store cache in ``app.storage``."""
        return (
            self.bucket,
            self.prefix,
            self.signer_service_account,
            self.signed_url_ttl_s,
            self.public_base_url,
            self.timeout_s,
        )

    def describe(self) -> dict[str, object]:
        """Operator-facing summary; carries no credential material."""
        return {
            "enabled": self.enabled,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "url_mode": "signed" if self.signer_service_account else "public",
            "signed_url_ttl_s": self.signed_url_ttl_s,
            "public_base_url": self.public_base_url,
        }

    @classmethod
    def from_env(cls) -> "StorageSettings":
        """Build settings from the process environment.

        ``GCS_BUCKET`` accepts either a bare bucket name or a ``gs://bucket/prefix``
        URI (the form an operator copies out of the console), in which case the
        path half becomes the object prefix unless ``GCS_PREFIX`` overrides it.
        ``STORY_ENGINE_MEDIA_STORAGE_ENABLED=0`` force-disables even when a bucket
        is configured, so a misbehaving bucket can be taken out of the loop
        mid-demo without unsetting anything else.
        """
        raw = os.environ.get("GCS_BUCKET", "").strip()
        uri_prefix = ""
        if raw.startswith("gs://"):
            raw = raw[len("gs://") :]
        if "/" in raw:
            raw, _, uri_prefix = raw.partition("/")
        bucket = raw.strip("/")

        prefix_env = os.environ.get("GCS_PREFIX")
        if prefix_env is None:
            prefix = _clean_prefix(uri_prefix) or DEFAULT_PREFIX
        else:
            prefix = _clean_prefix(prefix_env)

        return cls(
            enabled=bool(bucket) and _env_flag("STORY_ENGINE_MEDIA_STORAGE_ENABLED", True),
            bucket=bucket,
            prefix=prefix,
            signer_service_account=os.environ.get("GCS_SIGNER_SERVICE_ACCOUNT") or None,
            signed_url_ttl_s=_env_int("GCS_SIGNED_URL_TTL_S", DEFAULT_SIGNED_URL_TTL_S),
            public_base_url=os.environ.get("GCS_PUBLIC_BASE_URL") or GCS_HOST,
            timeout_s=_env_float("GCS_TIMEOUT_S", DEFAULT_TIMEOUT_S),
        )

"""Repository abstraction for the API layer.

``app.db.models`` requires PostgreSQL (pg enums, arrays, gist) and there is no
live DB yet, so ``InMemoryRepository`` is the default implementation. The
``Repository`` protocol is deliberately narrow and record-oriented so a
SQLAlchemy-backed implementation can slot in later without touching routers.
"""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel

from app.continuity.model import Finding
from app.ingest.elements import StoryGraph
from app.render.audio.model import SceneTiming
from app.shotlist.schema import SceneShotList


class UserRecord(BaseModel):
    id: str
    email: str
    password_hash: str
    salt: str


class ProjectRecord(BaseModel):
    id: str
    owner_id: str
    title: str
    grammar_profile: str
    validator_mode: str
    rights_attested: bool
    cost_cap_cents: int = 15000
    cost_spent_cents: int = 0


class ScriptRecord(BaseModel):
    id: str
    project_id: str
    format: str
    graph: StoryGraph


class ShotListRecord(BaseModel):
    id: str
    project_id: str
    scene_ordinal: int
    shotlist: SceneShotList


class AudioRenderRecord(BaseModel):
    id: str
    project_id: str
    scene_ordinal: int
    wav_bytes: bytes
    duration_ms: int
    clip_count: int
    ambience_tags: list[str]
    # Time-aligned placement from the same render pass, for the scene timeline.
    timing: SceneTiming


class VideoRenderRecord(BaseModel):
    id: str
    project_id: str
    scene_ordinal: int
    shot_ordinal: int
    video_bytes: bytes  # b"" when the provider returned URLs only
    output_urls: list[str]
    duration_ms: int
    cost_cents: int
    provider: str
    model: str
    source: str  # "image" | "text" — which generation path was taken


class FindingRecord(BaseModel):
    id: str
    project_id: str
    scene_ordinal: int
    rule_code: str
    severity: str
    message: str
    shot_ordinal: int | None = None
    deliberate: bool = False
    deliberate_note: str | None = None


class Repository(Protocol):
    # -- users -------------------------------------------------------------
    def create_user(self, email: str, password_hash: str, salt: str) -> UserRecord: ...
    def get_user(self, user_id: str) -> UserRecord | None: ...
    def get_user_by_email(self, email: str) -> UserRecord | None: ...

    # -- projects ----------------------------------------------------------
    def create_project(
        self,
        owner_id: str,
        title: str,
        grammar_profile: str,
        validator_mode: str,
        rights_attested: bool,
    ) -> ProjectRecord: ...
    def get_project(self, project_id: str) -> ProjectRecord | None: ...
    def list_projects(self, owner_id: str) -> list[ProjectRecord]: ...

    # -- scripts + story graphs ---------------------------------------------
    def save_script(
        self, project_id: str, format: str, graph: StoryGraph
    ) -> ScriptRecord: ...
    def get_script(self, project_id: str) -> ScriptRecord | None: ...
    def update_graph(self, project_id: str, graph: StoryGraph) -> ScriptRecord | None: ...

    # -- shot lists ----------------------------------------------------------
    def save_shotlist(
        self, project_id: str, scene_ordinal: int, shotlist: SceneShotList
    ) -> ShotListRecord: ...
    def get_shotlist(
        self, project_id: str, scene_ordinal: int
    ) -> ShotListRecord | None: ...

    # -- findings ------------------------------------------------------------
    def replace_findings(
        self, project_id: str, scene_ordinal: int, findings: list[Finding]
    ) -> list[FindingRecord]: ...
    def list_findings(
        self, project_id: str, scene_ordinal: int
    ) -> list[FindingRecord]: ...
    def get_finding(self, finding_id: str) -> FindingRecord | None: ...
    def update_finding(
        self, finding_id: str, deliberate: bool, deliberate_note: str | None
    ) -> FindingRecord | None: ...

    # -- audio renders ---------------------------------------------------------
    def save_audio_render(
        self,
        project_id: str,
        scene_ordinal: int,
        wav_bytes: bytes,
        duration_ms: int,
        clip_count: int,
        ambience_tags: list[str],
        timing: SceneTiming,
    ) -> AudioRenderRecord: ...
    def get_audio_render(
        self, project_id: str, scene_ordinal: int
    ) -> AudioRenderRecord | None: ...

    # -- video renders ---------------------------------------------------------
    def save_video_render(
        self,
        project_id: str,
        scene_ordinal: int,
        shot_ordinal: int,
        video_bytes: bytes,
        output_urls: list[str],
        duration_ms: int,
        cost_cents: int,
        provider: str,
        model: str,
        source: str,
    ) -> VideoRenderRecord: ...
    def get_video_render(
        self, project_id: str, scene_ordinal: int, shot_ordinal: int
    ) -> VideoRenderRecord | None: ...

    # -- shot frames (previz boards) -------------------------------------------
    # A per-shot board/frame image, when the previz pipeline has rendered one.
    # Its presence drives image-to-video vs text-to-video in the video renderer.
    def save_shot_frame(
        self, project_id: str, scene_ordinal: int, shot_ordinal: int, image_bytes: bytes
    ) -> None: ...
    def get_shot_frame(
        self, project_id: str, scene_ordinal: int, shot_ordinal: int
    ) -> bytes | None: ...


class InMemoryRepository:
    """Dict-backed repository keyed by uuid4 strings."""

    def __init__(self) -> None:
        self._users: dict[str, UserRecord] = {}
        self._users_by_email: dict[str, str] = {}
        self._projects: dict[str, ProjectRecord] = {}
        # One script (latest upload) per project.
        self._scripts_by_project: dict[str, ScriptRecord] = {}
        self._shotlists: dict[tuple[str, int], ShotListRecord] = {}
        self._findings: dict[str, FindingRecord] = {}
        # One (latest) render per (project, scene).
        self._audio_renders: dict[tuple[str, int], AudioRenderRecord] = {}
        # One (latest) video render per (project, scene, shot).
        self._video_renders: dict[tuple[str, int, int], VideoRenderRecord] = {}
        # Per-shot previz board/frame image bytes (project, scene, shot).
        self._shot_frames: dict[tuple[str, int, int], bytes] = {}

    # -- users -------------------------------------------------------------
    def create_user(self, email: str, password_hash: str, salt: str) -> UserRecord:
        user = UserRecord(
            id=str(uuid4()), email=email, password_hash=password_hash, salt=salt
        )
        self._users[user.id] = user
        self._users_by_email[email.lower()] = user.id
        return user

    def get_user(self, user_id: str) -> UserRecord | None:
        return self._users.get(user_id)

    def get_user_by_email(self, email: str) -> UserRecord | None:
        user_id = self._users_by_email.get(email.lower())
        return self._users.get(user_id) if user_id else None

    # -- projects ----------------------------------------------------------
    def create_project(
        self,
        owner_id: str,
        title: str,
        grammar_profile: str,
        validator_mode: str,
        rights_attested: bool,
    ) -> ProjectRecord:
        project = ProjectRecord(
            id=str(uuid4()),
            owner_id=owner_id,
            title=title,
            grammar_profile=grammar_profile,
            validator_mode=validator_mode,
            rights_attested=rights_attested,
        )
        self._projects[project.id] = project
        return project

    def get_project(self, project_id: str) -> ProjectRecord | None:
        return self._projects.get(project_id)

    def list_projects(self, owner_id: str) -> list[ProjectRecord]:
        return [p for p in self._projects.values() if p.owner_id == owner_id]

    # -- scripts + story graphs ---------------------------------------------
    def save_script(
        self, project_id: str, format: str, graph: StoryGraph
    ) -> ScriptRecord:
        script = ScriptRecord(
            id=str(uuid4()), project_id=project_id, format=format, graph=graph
        )
        self._scripts_by_project[project_id] = script
        return script

    def get_script(self, project_id: str) -> ScriptRecord | None:
        return self._scripts_by_project.get(project_id)

    def update_graph(self, project_id: str, graph: StoryGraph) -> ScriptRecord | None:
        script = self._scripts_by_project.get(project_id)
        if script is None:
            return None
        script.graph = graph
        return script

    # -- shot lists ----------------------------------------------------------
    def save_shotlist(
        self, project_id: str, scene_ordinal: int, shotlist: SceneShotList
    ) -> ShotListRecord:
        record = ShotListRecord(
            id=str(uuid4()),
            project_id=project_id,
            scene_ordinal=scene_ordinal,
            shotlist=shotlist,
        )
        self._shotlists[(project_id, scene_ordinal)] = record
        return record

    def get_shotlist(
        self, project_id: str, scene_ordinal: int
    ) -> ShotListRecord | None:
        return self._shotlists.get((project_id, scene_ordinal))

    # -- findings ------------------------------------------------------------
    def replace_findings(
        self, project_id: str, scene_ordinal: int, findings: list[Finding]
    ) -> list[FindingRecord]:
        stale = [
            fid
            for fid, f in self._findings.items()
            if f.project_id == project_id and f.scene_ordinal == scene_ordinal
        ]
        for fid in stale:
            del self._findings[fid]

        records = [
            FindingRecord(
                id=str(uuid4()),
                project_id=project_id,
                scene_ordinal=scene_ordinal,
                rule_code=f.rule_code,
                severity=f.severity,
                message=f.message,
                shot_ordinal=f.shot_ordinal,
            )
            for f in findings
        ]
        for record in records:
            self._findings[record.id] = record
        return records

    def list_findings(
        self, project_id: str, scene_ordinal: int
    ) -> list[FindingRecord]:
        return [
            f
            for f in self._findings.values()
            if f.project_id == project_id and f.scene_ordinal == scene_ordinal
        ]

    def get_finding(self, finding_id: str) -> FindingRecord | None:
        return self._findings.get(finding_id)

    def update_finding(
        self, finding_id: str, deliberate: bool, deliberate_note: str | None
    ) -> FindingRecord | None:
        finding = self._findings.get(finding_id)
        if finding is None:
            return None
        finding.deliberate = deliberate
        finding.deliberate_note = deliberate_note
        return finding

    # -- audio renders ---------------------------------------------------------
    def save_audio_render(
        self,
        project_id: str,
        scene_ordinal: int,
        wav_bytes: bytes,
        duration_ms: int,
        clip_count: int,
        ambience_tags: list[str],
        timing: SceneTiming,
    ) -> AudioRenderRecord:
        record = AudioRenderRecord(
            id=str(uuid4()),
            project_id=project_id,
            scene_ordinal=scene_ordinal,
            wav_bytes=wav_bytes,
            duration_ms=duration_ms,
            clip_count=clip_count,
            ambience_tags=list(ambience_tags),
            timing=timing,
        )
        self._audio_renders[(project_id, scene_ordinal)] = record
        return record

    def get_audio_render(
        self, project_id: str, scene_ordinal: int
    ) -> AudioRenderRecord | None:
        return self._audio_renders.get((project_id, scene_ordinal))

    # -- video renders ---------------------------------------------------------
    def save_video_render(
        self,
        project_id: str,
        scene_ordinal: int,
        shot_ordinal: int,
        video_bytes: bytes,
        output_urls: list[str],
        duration_ms: int,
        cost_cents: int,
        provider: str,
        model: str,
        source: str,
    ) -> VideoRenderRecord:
        record = VideoRenderRecord(
            id=str(uuid4()),
            project_id=project_id,
            scene_ordinal=scene_ordinal,
            shot_ordinal=shot_ordinal,
            video_bytes=video_bytes,
            output_urls=list(output_urls),
            duration_ms=duration_ms,
            cost_cents=cost_cents,
            provider=provider,
            model=model,
            source=source,
        )
        self._video_renders[(project_id, scene_ordinal, shot_ordinal)] = record
        return record

    def get_video_render(
        self, project_id: str, scene_ordinal: int, shot_ordinal: int
    ) -> VideoRenderRecord | None:
        return self._video_renders.get((project_id, scene_ordinal, shot_ordinal))

    # -- shot frames (previz boards) -------------------------------------------
    def save_shot_frame(
        self, project_id: str, scene_ordinal: int, shot_ordinal: int, image_bytes: bytes
    ) -> None:
        self._shot_frames[(project_id, scene_ordinal, shot_ordinal)] = image_bytes

    def get_shot_frame(
        self, project_id: str, scene_ordinal: int, shot_ordinal: int
    ) -> bytes | None:
        return self._shot_frames.get((project_id, scene_ordinal, shot_ordinal))

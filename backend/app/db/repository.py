"""SQLAlchemy implementation of ``app.api.repo.Repository``.

Lives here rather than in ``app.api.repo`` so that module stays import-cheap and
driver-free for the routers; ``app.api.deps`` imports this one lazily, only when
a database URL is actually configured.

It satisfies the same protocol as ``InMemoryRepository``, method for method, and
``tests/test_repo_conformance.py`` runs one set of assertions against both so
they cannot drift. Where the in-memory version has semantics that come from
being a dict - a replaced record gets a fresh id, findings come back in
insertion order, a record can reference a project id that was never created -
those semantics are reproduced here on purpose.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import PrivateAttr
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.repo import (
    AudioRenderRecord,
    FindingRecord,
    ProjectRecord,
    ScriptRecord,
    ShotListRecord,
    UserRecord,
    VideoRenderRecord,
)
from app.continuity.model import Finding
from app.db import models
from app.ingest.elements import StoryGraph
from app.render.audio.model import DEFAULT_RENDER_SETTINGS, SceneRenderSettings, SceneTiming
from app.shotlist.schema import SceneShotList


class _LiveProjectRecord(ProjectRecord):
    """A project record whose field assignments are written back to the row.

    ``app/api/routers/renders.py`` and ``app/api/routers/scenes.py`` charge a
    render by doing ``project.cost_spent_cents += estimated_cents`` on the
    record the repository handed them - there is no ``update_project`` on the
    protocol, so they lean on ``InMemoryRepository`` returning the very object
    it stores. Returning a detached copy here would make the cost cap
    unenforceable the moment the app is durable, which is exactly backwards.

    So this subclass persists on assignment. It is a bridge, not a design: the
    right fix is an explicit repository method and routers that call it, but
    the routers are owned elsewhere. Reads are unaffected, and the record is
    still a plain ``ProjectRecord`` to every consumer.
    """

    _origin: Any = PrivateAttr(default=None)

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name in ProjectRecord.model_fields and self._origin is not None:
            self._origin._persist_project(self)


def _bind(record: _LiveProjectRecord, repo: SqlAlchemyRepository) -> _LiveProjectRecord:
    record._origin = repo
    return record


class SqlAlchemyRepository:
    """Durable repository backed by the ``store_*`` tables (migration 0002).

    One short transaction per call: the API is request/response and every method
    is a single logical read or write, so holding a session open across calls
    would only buy stale identity-map reads and long-lived locks.
    """

    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        self._session = sessionmaker(engine, expire_on_commit=False, future=True)
        if create_schema:
            from app.db.session import create_store_schema

            create_store_schema(engine)

    # -- helpers -----------------------------------------------------------
    def _to_project(self, row: models.StoredProject) -> _LiveProjectRecord:
        return _bind(
            _LiveProjectRecord(
                id=row.id,
                owner_id=row.owner_id,
                title=row.title,
                grammar_profile=row.grammar_profile,
                validator_mode=row.validator_mode,
                rights_attested=row.rights_attested,
                cost_cap_cents=row.cost_cap_cents,
                cost_spent_cents=row.cost_spent_cents,
            ),
            self,
        )

    def _persist_project(self, record: ProjectRecord) -> None:
        with self._session.begin() as session:
            row = session.get(models.StoredProject, record.id)
            if row is None:
                return
            row.title = record.title
            row.grammar_profile = record.grammar_profile
            row.validator_mode = record.validator_mode
            row.rights_attested = record.rights_attested
            row.cost_cap_cents = record.cost_cap_cents
            row.cost_spent_cents = record.cost_spent_cents

    @staticmethod
    def _replace(session: Session, model: type[Any], **keys: Any) -> None:
        """Drop the row(s) a save is about to supersede.

        Emitted (and flushed) before the insert so the unique key is free -
        the unit of work is free to order its own inserts before its deletes.
        """
        statement = delete(model)
        for column, value in keys.items():
            statement = statement.where(getattr(model, column) == value)
        session.execute(statement)
        session.flush()

    # -- users -------------------------------------------------------------
    def create_user(self, email: str, password_hash: str, salt: str) -> UserRecord:
        record = UserRecord(
            id=str(uuid4()), email=email, password_hash=password_hash, salt=salt
        )
        with self._session.begin() as session:
            session.add(
                models.StoredUser(
                    id=record.id,
                    email=record.email,
                    email_lower=email.lower(),
                    password_hash=record.password_hash,
                    salt=record.salt,
                )
            )
        return record

    def get_user(self, user_id: str) -> UserRecord | None:
        with self._session() as session:
            row = session.get(models.StoredUser, user_id)
            return self._to_user(row) if row else None

    def get_user_by_email(self, email: str) -> UserRecord | None:
        with self._session() as session:
            row = session.execute(
                select(models.StoredUser)
                .where(models.StoredUser.email_lower == email.lower())
                # Last registration wins, as in the in-memory email index.
                .order_by(
                    models.StoredUser.created_at.desc(), models.StoredUser.id.desc()
                )
                .limit(1)
            ).scalar_one_or_none()
            return self._to_user(row) if row else None

    @staticmethod
    def _to_user(row: models.StoredUser) -> UserRecord:
        return UserRecord(
            id=row.id,
            email=row.email,
            password_hash=row.password_hash,
            salt=row.salt,
        )

    # -- projects ----------------------------------------------------------
    def create_project(
        self,
        owner_id: str,
        title: str,
        grammar_profile: str,
        validator_mode: str,
        rights_attested: bool,
    ) -> ProjectRecord:
        record = _LiveProjectRecord(
            id=str(uuid4()),
            owner_id=owner_id,
            title=title,
            grammar_profile=grammar_profile,
            validator_mode=validator_mode,
            rights_attested=rights_attested,
        )
        with self._session.begin() as session:
            session.add(
                models.StoredProject(
                    id=record.id,
                    owner_id=record.owner_id,
                    title=record.title,
                    grammar_profile=record.grammar_profile,
                    validator_mode=record.validator_mode,
                    rights_attested=record.rights_attested,
                    cost_cap_cents=record.cost_cap_cents,
                    cost_spent_cents=record.cost_spent_cents,
                )
            )
        return _bind(record, self)

    def get_project(self, project_id: str) -> ProjectRecord | None:
        with self._session() as session:
            row = session.get(models.StoredProject, project_id)
            return self._to_project(row) if row else None

    def list_projects(self, owner_id: str) -> list[ProjectRecord]:
        with self._session() as session:
            rows = session.execute(
                select(models.StoredProject)
                .where(models.StoredProject.owner_id == owner_id)
                .order_by(
                    models.StoredProject.created_at, models.StoredProject.id
                )
            ).scalars()
            return [self._to_project(row) for row in rows]

    # -- scripts + story graphs ---------------------------------------------
    def save_script(
        self, project_id: str, format: str, graph: StoryGraph
    ) -> ScriptRecord:
        record = ScriptRecord(
            id=str(uuid4()), project_id=project_id, format=format, graph=graph
        )
        with self._session.begin() as session:
            self._replace(session, models.StoredScript, project_id=project_id)
            session.add(
                models.StoredScript(
                    id=record.id,
                    project_id=project_id,
                    format=format,
                    graph=graph.model_dump(mode="json"),
                )
            )
        return record

    def get_script(self, project_id: str) -> ScriptRecord | None:
        with self._session() as session:
            row = self._script_row(session, project_id)
            return self._to_script(row) if row else None

    def update_graph(self, project_id: str, graph: StoryGraph) -> ScriptRecord | None:
        with self._session.begin() as session:
            row = self._script_row(session, project_id)
            if row is None:
                return None
            # Same record, new graph - the id is stable across edits, matching
            # the in-memory repository's in-place mutation.
            row.graph = graph.model_dump(mode="json")
            return ScriptRecord(
                id=row.id, project_id=row.project_id, format=row.format, graph=graph
            )

    @staticmethod
    def _script_row(session: Session, project_id: str) -> models.StoredScript | None:
        return session.execute(
            select(models.StoredScript).where(
                models.StoredScript.project_id == project_id
            )
        ).scalar_one_or_none()

    @staticmethod
    def _to_script(row: models.StoredScript) -> ScriptRecord:
        return ScriptRecord(
            id=row.id,
            project_id=row.project_id,
            format=row.format,
            graph=StoryGraph.model_validate(row.graph),
        )

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
        with self._session.begin() as session:
            self._replace(
                session,
                models.StoredShotList,
                project_id=project_id,
                scene_ordinal=scene_ordinal,
            )
            session.add(
                models.StoredShotList(
                    id=record.id,
                    project_id=project_id,
                    scene_ordinal=scene_ordinal,
                    shotlist=shotlist.model_dump(mode="json"),
                )
            )
        return record

    def get_shotlist(
        self, project_id: str, scene_ordinal: int
    ) -> ShotListRecord | None:
        with self._session() as session:
            row = session.execute(
                select(models.StoredShotList).where(
                    models.StoredShotList.project_id == project_id,
                    models.StoredShotList.scene_ordinal == scene_ordinal,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return ShotListRecord(
                id=row.id,
                project_id=row.project_id,
                scene_ordinal=row.scene_ordinal,
                shotlist=SceneShotList.model_validate(row.shotlist),
            )

    # -- findings ------------------------------------------------------------
    def replace_findings(
        self, project_id: str, scene_ordinal: int, findings: list[Finding]
    ) -> list[FindingRecord]:
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
        with self._session.begin() as session:
            self._replace(
                session,
                models.StoredFinding,
                project_id=project_id,
                scene_ordinal=scene_ordinal,
            )
            for position, record in enumerate(records):
                session.add(
                    models.StoredFinding(
                        id=record.id,
                        project_id=project_id,
                        scene_ordinal=scene_ordinal,
                        position=position,
                        rule_code=record.rule_code,
                        severity=record.severity,
                        message=record.message,
                        shot_ordinal=record.shot_ordinal,
                        deliberate=record.deliberate,
                        deliberate_note=record.deliberate_note,
                    )
                )
        return records

    def list_findings(
        self, project_id: str, scene_ordinal: int
    ) -> list[FindingRecord]:
        with self._session() as session:
            rows = session.execute(
                select(models.StoredFinding)
                .where(
                    models.StoredFinding.project_id == project_id,
                    models.StoredFinding.scene_ordinal == scene_ordinal,
                )
                .order_by(models.StoredFinding.position)
            ).scalars()
            return [self._to_finding(row) for row in rows]

    def get_finding(self, finding_id: str) -> FindingRecord | None:
        with self._session() as session:
            row = session.get(models.StoredFinding, finding_id)
            return self._to_finding(row) if row else None

    def update_finding(
        self, finding_id: str, deliberate: bool, deliberate_note: str | None
    ) -> FindingRecord | None:
        with self._session.begin() as session:
            row = session.get(models.StoredFinding, finding_id)
            if row is None:
                return None
            row.deliberate = deliberate
            row.deliberate_note = deliberate_note
            return self._to_finding(row)

    @staticmethod
    def _to_finding(row: models.StoredFinding) -> FindingRecord:
        return FindingRecord(
            id=row.id,
            project_id=row.project_id,
            scene_ordinal=row.scene_ordinal,
            rule_code=row.rule_code,
            severity=row.severity,
            message=row.message,
            shot_ordinal=row.shot_ordinal,
            deliberate=row.deliberate,
            deliberate_note=row.deliberate_note,
        )

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
        with self._session.begin() as session:
            # A fresh render supersedes the previous one and its staleness -
            # these bytes match the IR by definition.
            self._replace(
                session,
                models.StoredAudioRender,
                project_id=project_id,
                scene_ordinal=scene_ordinal,
            )
            session.add(
                models.StoredAudioRender(
                    id=record.id,
                    project_id=project_id,
                    scene_ordinal=scene_ordinal,
                    wav_bytes=wav_bytes,
                    duration_ms=duration_ms,
                    clip_count=clip_count,
                    ambience_tags=list(ambience_tags),
                    timing=timing.model_dump(mode="json"),
                    stale=False,
                    stale_reasons=[],
                )
            )
        return record

    def get_audio_render(
        self, project_id: str, scene_ordinal: int
    ) -> AudioRenderRecord | None:
        with self._session() as session:
            row = self._audio_row(session, project_id, scene_ordinal)
            return self._to_audio(row) if row else None

    def mark_audio_render_stale(
        self, project_id: str, scene_ordinal: int, reasons: list[str]
    ) -> AudioRenderRecord | None:
        with self._session.begin() as session:
            row = self._audio_row(session, project_id, scene_ordinal)
            if row is None:
                return None
            row.stale = True
            # Reasons accumulate across edits until the next render clears them.
            row.stale_reasons = [*row.stale_reasons, *reasons]
            return self._to_audio(row)

    @staticmethod
    def _audio_row(
        session: Session, project_id: str, scene_ordinal: int
    ) -> models.StoredAudioRender | None:
        return session.execute(
            select(models.StoredAudioRender).where(
                models.StoredAudioRender.project_id == project_id,
                models.StoredAudioRender.scene_ordinal == scene_ordinal,
            )
        ).scalar_one_or_none()

    @staticmethod
    def _to_audio(row: models.StoredAudioRender) -> AudioRenderRecord:
        return AudioRenderRecord(
            id=row.id,
            project_id=row.project_id,
            scene_ordinal=row.scene_ordinal,
            wav_bytes=bytes(row.wav_bytes),
            duration_ms=row.duration_ms,
            clip_count=row.clip_count,
            ambience_tags=list(row.ambience_tags),
            timing=SceneTiming.model_validate(row.timing),
            stale=row.stale,
            stale_reasons=list(row.stale_reasons),
        )

    # -- per-scene render settings -----------------------------------------------
    def get_render_settings(
        self, project_id: str, scene_ordinal: int
    ) -> SceneRenderSettings:
        with self._session() as session:
            row = session.execute(
                select(models.StoredRenderSettings).where(
                    models.StoredRenderSettings.project_id == project_id,
                    models.StoredRenderSettings.scene_ordinal == scene_ordinal,
                )
            ).scalar_one_or_none()
            if row is None:
                return DEFAULT_RENDER_SETTINGS
            return SceneRenderSettings.model_validate(row.settings)

    def save_render_settings(
        self, project_id: str, scene_ordinal: int, settings: SceneRenderSettings
    ) -> SceneRenderSettings:
        with self._session.begin() as session:
            self._replace(
                session,
                models.StoredRenderSettings,
                project_id=project_id,
                scene_ordinal=scene_ordinal,
            )
            session.add(
                models.StoredRenderSettings(
                    id=str(uuid4()),
                    project_id=project_id,
                    scene_ordinal=scene_ordinal,
                    settings=settings.model_dump(mode="json"),
                )
            )
        return settings

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
        with self._session.begin() as session:
            self._replace(
                session,
                models.StoredVideoRender,
                project_id=project_id,
                scene_ordinal=scene_ordinal,
                shot_ordinal=shot_ordinal,
            )
            session.add(
                models.StoredVideoRender(
                    id=record.id,
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
            )
        return record

    def get_video_render(
        self, project_id: str, scene_ordinal: int, shot_ordinal: int
    ) -> VideoRenderRecord | None:
        with self._session() as session:
            row = session.execute(
                select(models.StoredVideoRender).where(
                    models.StoredVideoRender.project_id == project_id,
                    models.StoredVideoRender.scene_ordinal == scene_ordinal,
                    models.StoredVideoRender.shot_ordinal == shot_ordinal,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return VideoRenderRecord(
                id=row.id,
                project_id=row.project_id,
                scene_ordinal=row.scene_ordinal,
                shot_ordinal=row.shot_ordinal,
                video_bytes=bytes(row.video_bytes),
                output_urls=list(row.output_urls),
                duration_ms=row.duration_ms,
                cost_cents=row.cost_cents,
                provider=row.provider,
                model=row.model,
                source=row.source,
            )

    # -- shot frames (previz boards) -------------------------------------------
    def save_shot_frame(
        self, project_id: str, scene_ordinal: int, shot_ordinal: int, image_bytes: bytes
    ) -> None:
        with self._session.begin() as session:
            self._replace(
                session,
                models.StoredShotFrame,
                project_id=project_id,
                scene_ordinal=scene_ordinal,
                shot_ordinal=shot_ordinal,
            )
            session.add(
                models.StoredShotFrame(
                    id=str(uuid4()),
                    project_id=project_id,
                    scene_ordinal=scene_ordinal,
                    shot_ordinal=shot_ordinal,
                    image_bytes=image_bytes,
                )
            )

    def get_shot_frame(
        self, project_id: str, scene_ordinal: int, shot_ordinal: int
    ) -> bytes | None:
        with self._session() as session:
            row = session.execute(
                select(models.StoredShotFrame).where(
                    models.StoredShotFrame.project_id == project_id,
                    models.StoredShotFrame.scene_ordinal == scene_ordinal,
                    models.StoredShotFrame.shot_ordinal == shot_ordinal,
                )
            ).scalar_one_or_none()
            return bytes(row.image_bytes) if row else None

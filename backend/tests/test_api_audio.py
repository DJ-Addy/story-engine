"""API tests: scene audio render + serve ("crisp listen" path).

get_tts is overridden with FakeTTS in every test here so nothing touches the
network or spends real credits; the default EdgeTTSAdapter is only ever
constructed lazily inside app.api.deps.get_tts, never at import time.
"""

import pytest
from fastapi.testclient import TestClient

from app.adapters.fake import FakeTTS
from app.api.deps import get_repo, get_tts
from app.api.main import create_app
from app.api.repo import InMemoryRepository


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def client(repo):
    app = create_app()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_tts] = lambda: FakeTTS()
    with TestClient(app) as c:
        yield c


def auth_headers(client, email="engineer@example.com", password="salt-marsh-4"):
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_project(client, headers, rights_attested=True, title="Wager"):
    r = client.post(
        "/api/v1/projects",
        json={"title": title, "rights_attested": rights_attested},
        headers=headers,
    )
    return r.json()["id"]


def upload_script(client, headers, project_id, sample_fountain):
    r = client.post(
        f"/api/v1/projects/{project_id}/script",
        files={"file": ("sample.fountain", sample_fountain.encode("utf-8"))},
        headers=headers,
    )
    assert r.status_code == 201
    return r


def test_render_audio_then_fetch_wav(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)

    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers
    )
    assert r.status_code == 201
    body = r.json()
    assert body["scene_ordinal"] == 1
    assert body["duration_ms"] > 0
    assert body["clip_count"] > 0
    assert isinstance(body["ambience_tags"], list)

    audio = client.get(f"/api/v1/projects/{project_id}/scenes/1/audio", headers=headers)
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.content
    assert audio.content.startswith(b"RIFF")


def test_audio_404_before_render(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)

    r = client.get(f"/api/v1/projects/{project_id}/scenes/1/audio", headers=headers)
    assert r.status_code == 404


def test_render_audio_requires_rights_attestation(client, repo, sample_fountain):
    # POST /projects itself rejects rights_attested=False (422) before a
    # project can even exist, so exercise the render-time gate by flipping
    # the flag directly on the stored (unfrozen) ProjectRecord afterwards.
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    repo.get_project(project_id).rights_attested = False

    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers
    )
    assert r.status_code == 403


def test_render_audio_missing_scene_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)

    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/99/render/audio", headers=headers
    )
    assert r.status_code == 404


def test_render_audio_owner_isolation(client, sample_fountain):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    project_id = make_project(client, headers_a)
    upload_script(client, headers_a, project_id, sample_fountain)

    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers_b
    )
    assert r.status_code == 404


def test_render_audio_cost_cap_exceeded_402(client, repo, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)

    # ProjectRecord is a plain (unfrozen) pydantic model held by reference in
    # the in-memory repo, so mutating it here directly shrinks the cap the
    # running app sees -- no API surface exists yet to set cost_cap_cents at
    # project-creation time.
    project = repo.get_project(project_id)
    project.cost_cap_cents = 1

    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers
    )
    assert r.status_code == 402


class TestVoicesComeFromTheProvider:
    """Every voice the renderer names must be one the provider actually has.

    The pool used to be a literal list of Edge TTS names that outlived the
    adapter it belonged to. Against Google Cloud TTS every render failed with
    `Voice 'en-US-AriaNeural' does not exist. Is it misspelled?` — a 503 raised
    on the provider's own 400, after the scene had been parsed and priced.
    """

    @staticmethod
    def _scene(fountain: str):
        from app.ingest.fountain import parse_fountain
        from app.ingest.normalize import normalize

        return normalize(parse_fountain(fountain)).scenes[1]

    async def test_every_assigned_voice_exists_in_the_catalogue(
        self, sample_fountain: str
    ) -> None:
        from app.adapters.fake import FakeTTS
        from app.api.routers.scenes import _voice_map

        tts = FakeTTS()
        catalogue = {voice.id for voice in await tts.list_voices()}

        mapping, _tones = await _voice_map(self._scene(sample_fountain), tts)

        assert mapping, "expected at least a narrator"
        unknown = set(mapping.values()) - catalogue
        assert not unknown, f"voices absent from the provider catalogue: {unknown}"

    async def test_narrator_and_characters_differ_when_the_pool_allows(
        self, sample_fountain: str
    ) -> None:
        from app.adapters.fake import FakeTTS
        from app.api.routers.scenes import _voice_map

        tts = FakeTTS()
        mapping, _tones = await _voice_map(self._scene(sample_fountain), tts)

        narrator = mapping[None]
        characters = [v for k, v in mapping.items() if k is not None]
        if len(await tts.list_voices()) > 1:
            assert narrator not in characters, (
                "the narrator should not share a voice with a character "
                "while the catalogue has room"
            )

    async def test_assignment_is_deterministic(self, sample_fountain: str) -> None:
        """The same scene must render the same way twice."""
        from app.adapters.fake import FakeTTS
        from app.api.routers.scenes import _voice_map

        scene = self._scene(sample_fountain)
        first, _ = await _voice_map(scene, FakeTTS())
        second, _ = await _voice_map(scene, FakeTTS())

        assert first == second


class TestCastingReachesTheRender:
    """A saved casting decides the render — that is the point of saving one.

    The voice-fit judge and the renderer used to be two unrelated opinions
    about the same scene: the judge scored a casting the caller handed it and
    discarded it, while the renderer dealt voices round-robin from the provider
    catalogue and never saw the judge. These pin the join.
    """

    @staticmethod
    def _scene(fountain: str):
        from app.ingest.fountain import parse_fountain
        from app.ingest.normalize import normalize

        return normalize(parse_fountain(fountain)).scenes[1]

    async def _casting(self, tts, character: str, tone: str | None):
        from app.api.repo import CastEntry, CastingRecord

        voices = await tts.list_voices()
        chosen = voices[-1]
        return CastingRecord(
            id="c1",
            project_id="p1",
            entries=[
                CastEntry(
                    character=character,
                    voice_id=chosen.id,
                    voice_name=chosen.name,
                    tone=tone,
                    confidence=0.9,
                    rationale="test",
                )
            ],
            source="judge",
        ), chosen

    async def test_casting_voice_overrides_the_round_robin_deal(
        self, sample_fountain: str
    ) -> None:
        from app.adapters.fake import FakeTTS
        from app.api.routers.scenes import _voice_map

        tts = FakeTTS()
        scene = self._scene(sample_fountain)
        speaker = next(
            line.character_name
            for line in scene.lines
            if line.kind == "dialogue" and line.character_name
        )
        dealt, _ = await _voice_map(scene, tts)

        # Deliberately cast a voice the round-robin did NOT choose, so the
        # assertion below proves the casting decided rather than coinciding.
        catalogue = await tts.list_voices()
        other = next(v for v in catalogue if v.id != dealt[speaker])
        casting, _chosen = await self._casting(tts, speaker, "urgent")
        casting.entries[0].voice_id = other.id
        casting.entries[0].voice_name = other.name

        cast, tones = await _voice_map(scene, tts, casting)

        assert dealt[speaker] != other.id
        assert cast[speaker] == other.id
        assert tones[speaker] == "urgent"

    async def test_speakers_the_casting_omits_still_get_a_voice(
        self, sample_fountain: str
    ) -> None:
        """A character added after the last casting run must not be silent."""
        from app.adapters.fake import FakeTTS
        from app.api.routers.scenes import _voice_map

        tts = FakeTTS()
        scene = self._scene(sample_fountain)
        casting, _ = await self._casting(tts, "SOMEONE-NOT-IN-THIS-SCENE", "calm")

        mapping, _tones = await _voice_map(scene, tts, casting)

        catalogue = {voice.id for voice in await tts.list_voices()}
        speakers = {
            line.character_name
            for line in scene.lines
            if line.kind == "dialogue" and line.character_name
        }
        for speaker in speakers:
            assert mapping[speaker] in catalogue
        assert mapping[None] in catalogue

    async def test_a_voice_the_provider_no_longer_has_is_ignored(
        self, sample_fountain: str
    ) -> None:
        """A casting outlives the adapter it was decided against.

        Honouring a stale voice id would send the render a name the provider
        answers 400 to; falling back to the catalogue keeps it working.
        """
        from app.adapters.fake import FakeTTS
        from app.api.repo import CastEntry, CastingRecord
        from app.api.routers.scenes import _voice_map

        tts = FakeTTS()
        scene = self._scene(sample_fountain)
        speaker = next(
            line.character_name
            for line in scene.lines
            if line.kind == "dialogue" and line.character_name
        )
        stale = CastingRecord(
            id="c1",
            project_id="p1",
            entries=[
                CastEntry(
                    character=speaker,
                    voice_id="en-US-AriaNeural",  # the Edge name that broke production
                    voice_name="Aria",
                    tone="sad",
                )
            ],
            source="judge",
        )

        mapping, tones = await _voice_map(scene, tts, stale)

        catalogue = {voice.id for voice in await tts.list_voices()}
        assert mapping[speaker] in catalogue
        assert mapping[speaker] != "en-US-AriaNeural"
        # The tone is still honoured: only the voice id went stale.
        assert tones[speaker] == "sad"

"""Contrato HTTP: validação de requisição, autenticação, autorização, respostas e erros."""

import json
import uuid

import httpx
import pytest

from app.application.use_cases.authenticate_tenant import generate_api_key, hash_api_key
from app.config import Settings
from app.container import Container
from app.domain.entities import Tenant, TenantStatus
from app.infrastructure.security.template_cipher import generate_key
from app.main import create_app
from tests.fakes import JPEG, PNG

API_KEY = generate_api_key()
OTHER_KEY = generate_api_key()


def make_settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        s3_bucket="unused",
        s3_access_key="unused",
        s3_secret_key="unused",
        template_encryption_key=generate_key(),
        max_capture_bytes=1024,
        liveness_min_frames=3,
        liveness_max_frames=10,
    )


@pytest.fixture
def api(world):
    now = world.clock.now()
    world.tenant.api_key_hash = hash_api_key(API_KEY)
    world.store.tenants[world.tenant.id] = world.tenant
    other = Tenant(
        id=uuid.uuid4(),
        name="Tenant B",
        slug="tenant-b",
        status=TenantStatus.ACTIVE,
        api_key_hash=hash_api_key(OTHER_KEY),
        created_at=now,
        updated_at=now,
    )
    world.store.tenants[other.id] = other

    async def ok() -> None:
        return None

    async def broken() -> None:
        raise ConnectionError("down")

    container = Container(
        settings=make_settings(),
        uow_factory=world.uow,
        storage=world.storage,
        queue=world.queue,
        pipeline=world.pipeline(),
        cipher=world.cipher,
        policies=world.policies,
        readiness_checks={"database": ok, "redis": ok, "storage": ok},
    )
    container.clock = world.clock  # type: ignore[assignment]
    app = create_app(make_settings())
    app.state.container = container  # ASGITransport não executa o lifespan
    world.container = container
    world.broken = broken
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def auth(key: str = API_KEY) -> dict[str, str]:
    return {"X-API-Key": key}


def image(content: bytes = JPEG, content_type: str = "image/jpeg"):
    return {"image": ("capture", content, content_type)}


async def enroll(api, world, subject: str = "user-1") -> str:
    r = await api.post(f"/api/v1/subjects/{subject}/face", files=image(), headers=auth())
    assert r.status_code == 202, r.text
    await world.process_registration().execute(uuid.UUID(r.json()["registration_id"]))
    return r.json()["registration_id"]


class TestHealth:
    async def test_health(self, api):
        r = await api.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        assert "X-Request-ID" in r.headers

    async def test_ready_ok(self, api):
        r = await api.get("/ready")
        assert r.status_code == 200
        assert r.json()["checks"] == {"database": "ok", "redis": "ok", "storage": "ok"}

    async def test_ready_fails_when_dependency_down(self, api, world):
        world.container.readiness_checks["redis"] = world.broken
        r = await api.get("/ready")
        assert r.status_code == 503
        assert r.json()["checks"]["redis"] == "fail"

    async def test_openapi_lists_only_planned_routes(self, api):
        paths = set((await api.get("/openapi.json")).json()["paths"])
        assert paths == {
            "/health",
            "/ready",
            "/api/v1/subjects/{subject_id}/face",
            "/api/v1/subjects/{subject_id}/verifications",
            "/api/v1/verifications/{verification_id}",
            "/api/v1/face-registrations/{registration_id}",
            "/api/v1/liveness-sessions",
        }

    async def test_openapi_never_documents_score_or_measurements(self, api):
        schemas = (await api.get("/openapi.json")).json()["components"]["schemas"]
        fields = {f for schema in schemas.values() for f in schema.get("properties", {})}
        forbidden = {"similarity", "score", "threshold", "sharpness", "brightness", "template"}
        assert fields & forbidden == set()


class TestAuthentication:
    @pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": "fvs_unknown"}])
    async def test_rejects_missing_or_invalid_key(self, api, headers):
        r = await api.post("/api/v1/subjects/u1/face", files=image(), headers=headers)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"

    async def test_suspended_tenant(self, api, world):
        tenant = world.store.tenants[world.tenant.id]
        tenant.status = TenantStatus.SUSPENDED
        r = await api.post("/api/v1/subjects/u1/face", files=image(), headers=auth())
        assert r.status_code == 401


class TestRegisterFace:
    async def test_accepts_and_enqueues(self, api, world):
        r = await api.post("/api/v1/subjects/user-1/face", files=image(), headers=auth())
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "CREATED" and body["subject_id"] == "user-1"
        assert world.queue.registrations == [uuid.UUID(body["registration_id"])]

    async def test_already_enrolled(self, api, world):
        await enroll(api, world)
        r = await api.post("/api/v1/subjects/user-1/face", files=image(), headers=auth())
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "SUBJECT_ALREADY_ENROLLED"

    @pytest.mark.parametrize(
        "files",
        [image(JPEG, "image/gif"), image(PNG, "image/jpeg"), image(JPEG + b"\x00" * 4096)],
    )
    async def test_invalid_capture(self, api, files):
        r = await api.post("/api/v1/subjects/user-1/face", files=files, headers=auth())
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "INVALID_CAPTURE"

    async def test_missing_file(self, api):
        r = await api.post("/api/v1/subjects/user-1/face", headers=auth())
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_invalid_subject_id(self, api):
        r = await api.post("/api/v1/subjects/bad%20id/face", files=image(), headers=auth())
        assert r.status_code == 422

    async def test_validation_errors_do_not_echo_input(self, api):
        r = await api.post(
            "/api/v1/subjects/user-1/verifications",
            files=image(),
            headers={**auth(), "Idempotency-Key": "short"},
        )
        assert r.status_code == 422
        assert "short" not in r.text


class TestVerifications:
    async def test_create_and_get_approved(self, api, world):
        await enroll(api, world)
        r = await api.post(
            "/api/v1/subjects/user-1/verifications",
            files=image(PNG, "image/png"),
            headers={**auth(), "Idempotency-Key": "idem-0000-0001"},
        )
        assert r.status_code == 202, r.text
        verification_id = r.json()["verification_id"]
        assert r.json()["decision"] is None

        await world.process_verification().execute(uuid.UUID(verification_id))
        r = await api.get(f"/api/v1/verifications/{verification_id}", headers=auth())
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "APPROVED" and body["decision"] == "APPROVED"
        assert body["quality"] == {"passed": True, "issues": []}
        assert "similarity" not in body

    async def test_idempotency_key_required(self, api, world):
        await enroll(api, world)
        r = await api.post("/api/v1/subjects/user-1/verifications", files=image(), headers=auth())
        assert r.status_code == 422

    async def test_idempotent_replay_header(self, api, world):
        await enroll(api, world)
        headers = {**auth(), "Idempotency-Key": "idem-0000-0002"}
        first = await api.post(
            "/api/v1/subjects/user-1/verifications", files=image(), headers=headers
        )
        second = await api.post(
            "/api/v1/subjects/user-1/verifications", files=image(), headers=headers
        )
        assert first.json()["verification_id"] == second.json()["verification_id"]
        assert second.headers.get("Idempotent-Replayed") == "true"

    async def test_unknown_subject(self, api):
        r = await api.post(
            "/api/v1/subjects/ghost/verifications",
            files=image(),
            headers={**auth(), "Idempotency-Key": "idem-0000-0003"},
        )
        assert r.status_code == 404

    async def test_other_tenant_cannot_read(self, api, world):
        await enroll(api, world)
        r = await api.post(
            "/api/v1/subjects/user-1/verifications",
            files=image(),
            headers={**auth(), "Idempotency-Key": "idem-0000-0004"},
        )
        verification_id = r.json()["verification_id"]
        r = await api.get(f"/api/v1/verifications/{verification_id}", headers=auth(OTHER_KEY))
        assert r.status_code == 404

    async def test_other_tenant_does_not_see_subject(self, api, world):
        await enroll(api, world)
        r = await api.post(
            "/api/v1/subjects/user-1/verifications",
            files=image(),
            headers={**auth(OTHER_KEY), "Idempotency-Key": "idem-0000-0005"},
        )
        assert r.status_code == 404

    async def test_malformed_verification_id(self, api):
        r = await api.get("/api/v1/verifications/not-a-uuid", headers=auth())
        assert r.status_code == 422

    async def test_storage_unavailable_returns_503(self, api, world):
        await enroll(api, world)
        world.storage.fail = True
        r = await api.post(
            "/api/v1/subjects/user-1/verifications",
            files=image(),
            headers={**auth(), "Idempotency-Key": "idem-0000-0006"},
        )
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


class TestFaceRegistrationQueries:
    async def test_registration_result_by_id(self, api, world):
        registration_id = await enroll(api, world)
        r = await api.get(f"/api/v1/face-registrations/{registration_id}", headers=auth())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["registration_id"] == registration_id
        assert body["subject_id"] == "user-1"
        assert body["status"] == "APPROVED" and body["decision"] == "APPROVED"
        assert body["quality"] == {"passed": True, "issues": []}
        assert not {"template", "model_name", "similarity"} & set(body)

    async def test_pending_registration_has_no_decision_nor_quality(self, api):
        r = await api.post("/api/v1/subjects/user-9/face", files=image(), headers=auth())
        r = await api.get(
            f"/api/v1/face-registrations/{r.json()['registration_id']}", headers=auth()
        )
        assert r.json()["status"] == "CREATED"
        assert r.json()["decision"] is None and r.json()["quality"] is None

    async def test_quality_rejection_returns_issue_codes(self, api, world):
        world.script.quality_ok = False
        registration_id = await enroll(api, world)
        r = await api.get(f"/api/v1/face-registrations/{registration_id}", headers=auth())
        body = r.json()
        assert body["decision"] == "REJECTED" and body["reason"] == "LOW_QUALITY"
        assert body["quality"]["passed"] is False
        assert body["quality"]["issues"]  # códigos, nunca medidas
        assert all(isinstance(code, str) for code in body["quality"]["issues"])

    async def test_subject_face_state(self, api, world):
        world.script.quality_ok = False
        first = await enroll(api, world)
        r = await api.get("/api/v1/subjects/user-1/face", headers=auth())
        assert r.status_code == 200
        assert r.json()["enrolled"] is False and r.json()["active_registration_id"] is None
        assert r.json()["latest_registration"]["registration_id"] == first

        world.script.quality_ok = True
        world.clock.advance(minutes=1)
        second = await enroll(api, world)
        body = (await api.get("/api/v1/subjects/user-1/face", headers=auth())).json()
        assert body["enrolled"] is True
        assert body["active_registration_id"] == second
        assert body["latest_registration"]["registration_id"] == second

    async def test_unknown_subject_is_404(self, api):
        r = await api.get("/api/v1/subjects/ghost/face", headers=auth())
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "SUBJECT_NOT_FOUND"

    async def test_unknown_registration_is_404(self, api):
        r = await api.get(f"/api/v1/face-registrations/{uuid.uuid4()}", headers=auth())
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "FACE_REGISTRATION_NOT_FOUND"

    async def test_other_tenant_sees_nothing(self, api, world):
        registration_id = await enroll(api, world)
        by_id = await api.get(
            f"/api/v1/face-registrations/{registration_id}", headers=auth(OTHER_KEY)
        )
        by_subject = await api.get("/api/v1/subjects/user-1/face", headers=auth(OTHER_KEY))
        assert by_id.status_code == 404 and by_subject.status_code == 404

    async def test_requires_authentication(self, api, world):
        registration_id = await enroll(api, world)
        assert (await api.get(f"/api/v1/face-registrations/{registration_id}")).status_code == 401
        assert (await api.get("/api/v1/subjects/user-1/face")).status_code == 401


class TestReenrollment:
    async def replace(self, api, subject="user-1", key=API_KEY):
        return await api.put(f"/api/v1/subjects/{subject}/face", files=image(), headers=auth(key))

    async def test_put_replaces_after_approval(self, api, world):
        old = await enroll(api, world)
        world.clock.advance(minutes=1)
        r = await self.replace(api)
        assert r.status_code == 202, r.text
        new = r.json()["registration_id"]
        await world.process_registration().execute(uuid.UUID(new))

        subject = (await api.get("/api/v1/subjects/user-1/face", headers=auth())).json()
        assert subject["enrolled"] is True and subject["active_registration_id"] == new
        previous = (await api.get(f"/api/v1/face-registrations/{old}", headers=auth())).json()
        assert previous["status"] == "APPROVED"
        assert previous["superseded_by_id"] == new and previous["superseded_at"]
        current = (await api.get(f"/api/v1/face-registrations/{new}", headers=auth())).json()
        assert current["replaces_registration_id"] == old and current["superseded_at"] is None

    async def test_put_requires_enrolled_subject(self, api, world):
        assert (await self.replace(api, "ghost")).status_code == 404
        world.script.quality_ok = False
        await enroll(api, world, "user-2")
        r = await self.replace(api, "user-2")
        assert r.status_code == 409 and r.json()["error"]["code"] == "SUBJECT_NOT_ENROLLED"

    async def test_post_on_enrolled_subject_points_to_put(self, api, world):
        await enroll(api, world)
        r = await api.post("/api/v1/subjects/user-1/face", files=image(), headers=auth())
        assert r.status_code == 409 and "PUT" in r.json()["error"]["message"]

    async def test_other_tenant_cannot_replace(self, api, world):
        await enroll(api, world)
        assert (await self.replace(api, key=OTHER_KEY)).status_code == 404

    async def test_put_validates_capture_and_auth(self, api, world):
        await enroll(api, world)
        bad = await api.put(
            "/api/v1/subjects/user-1/face", files=image(b"x", "image/gif"), headers=auth()
        )
        assert bad.status_code == 422
        assert (await api.put("/api/v1/subjects/user-1/face", files=image())).status_code == 401


class TestWebhookMatchesGet:
    """Decisão do usuário (ADR-009): o corpo do webhook é o mesmo JSON do GET."""

    async def test_registration_and_verification_payloads_equal_get(self, api, world):
        await world.configure_webhook().execute(
            slug=world.tenant.slug,
            url="https://consumidor.example/hook",
            rotate_secret=False,
            allow_http=False,
        )
        registration_id = await enroll(api, world)
        r = await api.post(
            "/api/v1/subjects/user-1/verifications",
            files=image(),
            headers={**auth(), "Idempotency-Key": "idem-webhook-api-1"},
        )
        verification_id = r.json()["verification_id"]
        await world.process_verification().execute(uuid.UUID(verification_id))
        await world.deliver_webhooks().execute()

        bodies = {
            json.loads(s.body)["type"]: json.loads(s.body)["data"]
            for s in world.webhook_sender.sent
        }
        get_registration = await api.get(
            f"/api/v1/face-registrations/{registration_id}", headers=auth()
        )
        get_verification = await api.get(f"/api/v1/verifications/{verification_id}", headers=auth())
        assert bodies["face_registration.completed"] == get_registration.json()
        assert bodies["verification.completed"] == get_verification.json()


def frames(n: int = 4):
    return [("frames", (f"f{i}.jpg", JPEG, "image/jpeg")) for i in range(n)]


class TestLivenessSessions:
    async def open(self, api, subject="user-1", purpose="REGISTRATION", key=API_KEY):
        return await api.post(
            "/api/v1/liveness-sessions",
            json={"subject_id": subject, "purpose": purpose},
            headers=auth(key),
        )

    async def test_session_shape(self, api):
        r = await self.open(api)
        assert r.status_code == 201, r.text
        body = r.json()
        assert len(body["challenge"]) == 3 and len(body["instructions"]) == 3
        assert set(body["challenge"]) <= {"TURN_LEFT", "TURN_RIGHT", "MOVE_CLOSER"}
        assert body["frames"]["min"] == 3 and body["frames"]["max"] == 10
        assert body["expires_at"] and body["subject_id"] == "user-1"

    async def test_requires_auth_and_valid_purpose(self, api):
        no_key = await api.post(
            "/api/v1/liveness-sessions", json={"subject_id": "u", "purpose": "REGISTRATION"}
        )
        bad = await self.open(api, purpose="OTHER")
        assert no_key.status_code == 401 and bad.status_code == 422

    async def test_registration_with_session_and_frames(self, api, world):
        session = (await self.open(api)).json()["session_id"]
        r = await api.post(
            "/api/v1/subjects/user-1/face",
            data={"liveness_session_id": session},
            files=[("image", ("selfie.jpg", JPEG, "image/jpeg")), *frames(4)],
            headers=auth(),
        )
        assert r.status_code == 202, r.text
        registration = world.store.registrations[uuid.UUID(r.json()["registration_id"])]
        assert registration.liveness_challenge_id == uuid.UUID(session)

    async def test_session_is_single_use(self, api, world):
        session = (await self.open(api, purpose="VERIFICATION")).json()["session_id"]
        await enroll(api, world)
        files = [("image", ("selfie.jpg", JPEG, "image/jpeg")), *frames(4)]
        first = await api.post(
            "/api/v1/subjects/user-1/verifications",
            data={"liveness_session_id": session},
            files=files,
            headers={**auth(), "Idempotency-Key": "idem-live-0001"},
        )
        second = await api.post(
            "/api/v1/subjects/user-1/verifications",
            data={"liveness_session_id": session},
            files=files,
            headers={**auth(), "Idempotency-Key": "idem-live-0002"},
        )
        assert first.status_code == 202 and second.status_code == 409
        assert second.json()["error"]["code"] == "LIVENESS_SESSION_INVALID"

    async def test_session_of_other_tenant_is_404(self, api):
        session = (await self.open(api, key=OTHER_KEY)).json()["session_id"]
        r = await api.post(
            "/api/v1/subjects/user-1/face",
            data={"liveness_session_id": session},
            files=[("image", ("selfie.jpg", JPEG, "image/jpeg")), *frames(4)],
            headers=auth(),
        )
        assert r.status_code == 404 and r.json()["error"]["code"] == "LIVENESS_SESSION_NOT_FOUND"

    async def test_session_without_frames_is_422(self, api):
        session = (await self.open(api)).json()["session_id"]
        r = await api.post(
            "/api/v1/subjects/user-1/face",
            data={"liveness_session_id": session},
            files=image(),
            headers=auth(),
        )
        assert r.status_code == 422

    async def test_evidence_required_when_active_liveness(self, api, world):
        world.container.settings = world.container.settings.model_copy(
            update={"liveness_provider": "active", "liveness_requirement": "REQUIRED"}
        )
        r = await api.post("/api/v1/subjects/user-1/face", files=image(), headers=auth())
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "LIVENESS_EVIDENCE_REQUIRED"
        assert world.storage.objects == {}  # recusado antes de gravar qualquer coisa


class FakeFrameObserver:
    def __init__(self, answer: dict | None) -> None:
        self.answer = answer
        self.calls: list[str] = []

    async def observe(self, content: bytes, content_type: str) -> dict | None:
        self.calls.append(content_type)
        return self.answer


class TestDevLivenessPage:
    """Página e medição da captura guiada: só em APP_ENV=local (ADR-010)."""

    async def test_not_mounted_outside_local(self, api):
        assert (await api.get("/dev/liveness")).status_code == 404
        r = await api.post(
            "/dev/liveness/observe", files={"frame": ("f.jpg", JPEG, "image/jpeg")}, headers=auth()
        )
        assert r.status_code == 404

    @pytest.fixture
    def local(self, api, world):
        settings = make_settings().model_copy(update={"app_env": "local"})
        observer = FakeFrameObserver(
            {"face_count": 1, "yaw": -0.4, "eye_distance": 70.0, "image_width": 640}
        )
        world.container.settings = settings
        world.container.frame_observer = observer  # type: ignore[assignment]
        app = create_app(settings)
        app.state.container = world.container
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        return client, observer

    async def test_page_carries_configured_parameters(self, local):
        client, _ = local
        r = await client.get("/dev/liveness")
        assert r.status_code == 200
        assert "__PARAMS__" not in r.text and '"turn_min_yaw": 0.35' in r.text
        assert "/dev/liveness" not in json.dumps((await client.get("/openapi.json")).json())

    async def test_observe_returns_only_measurements(self, local):
        client, observer = local
        r = await client.post(
            "/dev/liveness/observe", files={"frame": ("f.jpg", JPEG, "image/jpeg")}, headers=auth()
        )
        assert r.status_code == 200
        assert r.json() == {
            "available": True,
            "face_count": 1,
            "yaw": -0.4,
            "eye_distance": 70.0,
            "image_width": 640,
        }
        assert observer.calls == ["image/jpeg"]

    async def test_observe_requires_api_key_and_valid_image(self, local):
        client, observer = local
        files = {"frame": ("f.jpg", JPEG, "image/jpeg")}
        assert (await client.post("/dev/liveness/observe", files=files)).status_code == 401
        bad = {"frame": ("f.jpg", b"not an image", "image/jpeg")}
        r = await client.post("/dev/liveness/observe", files=bad, headers=auth())
        assert r.status_code == 422 and observer.calls == []

    async def test_observe_without_worker_answer_is_503(self, local):
        client, observer = local
        observer.answer = None
        r = await client.post(
            "/dev/liveness/observe", files={"frame": ("f.jpg", JPEG, "image/jpeg")}, headers=auth()
        )
        assert r.status_code == 503 and r.json() == {"available": False}

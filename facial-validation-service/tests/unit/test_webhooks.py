"""Webhook de resultado (ADR-009): outbox, backoff, assinatura e configuração."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.application.dto import CreateVerificationCommand, RegisterFaceCommand
from app.application.use_cases.webhooks import (
    SECRET_PREFIX,
    InvalidWebhookUrl,
    validate_webhook_url,
    webhook_secret_aad,
)
from app.domain.entities import (
    MAX_ATTEMPTS,
    RETRY_DELAYS,
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEventType,
)
from app.domain.exceptions import InvalidStateTransition
from app.domain.value_objects import CaptureData, ProcessStatus
from app.infrastructure.webhooks import sign
from tests.fakes import JPEG

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def delivery() -> WebhookDelivery:
    return WebhookDelivery.create(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        event_type=WebhookEventType.VERIFICATION_COMPLETED,
        resource_id=uuid.uuid4(),
        now=NOW,
    )


class TestRetryPolicy:
    def test_schedule_covers_about_a_day_in_nine_attempts(self):
        assert MAX_ATTEMPTS == 9
        assert timedelta(hours=23) < sum(RETRY_DELAYS, timedelta()) < timedelta(hours=24)

    def test_failures_follow_backoff_then_give_up(self):
        d, now = delivery(), NOW
        for attempt in range(1, MAX_ATTEMPTS + 1):
            d.claim(now, timedelta(minutes=2))
            assert d.attempts == attempt
            d.fail(now, error="HTTP_500", status_code=500)
            if attempt < MAX_ATTEMPTS:
                assert d.status is WebhookDeliveryStatus.PENDING
                assert d.next_attempt_at == now + RETRY_DELAYS[attempt - 1]
                now = d.next_attempt_at
        assert d.status is WebhookDeliveryStatus.FAILED
        assert d.last_status_code == 500 and d.last_error == "HTTP_500"

    def test_claim_leases_the_delivery(self):
        d = delivery()
        d.claim(NOW, timedelta(minutes=2))
        assert d.next_attempt_at == NOW + timedelta(minutes=2)

    def test_success_and_abandon_are_terminal(self):
        ok, gone = delivery(), delivery()
        ok.claim(NOW, timedelta(minutes=2))
        ok.succeed(NOW, 204)
        gone.abandon(NOW, "WEBHOOK_DISABLED")
        assert ok.status is WebhookDeliveryStatus.DELIVERED and ok.delivered_at == NOW
        assert gone.status is WebhookDeliveryStatus.FAILED
        for terminal in (ok, gone):
            with pytest.raises(InvalidStateTransition):
                terminal.claim(NOW, timedelta(minutes=2))

    def test_error_code_is_truncated(self):
        d = delivery()
        d.claim(NOW, timedelta(minutes=2))
        d.fail(NOW, error="X" * 500)
        assert len(d.last_error) == 64


class TestUrlValidation:
    @pytest.mark.parametrize(
        "url",
        ["https://consumidor.example/webhooks", "https://10.0.0.5:8443/hook?tenant=a"],
    )
    def test_accepts_https(self, url):
        assert validate_webhook_url(url, allow_http=False) == url

    @pytest.mark.parametrize(
        "url",
        [
            "http://consumidor.example/hook",  # http fora de local/test
            "ftp://consumidor.example/hook",
            "https://user:pass@consumidor.example/hook",
            "https:///sem-host",
            "https://consumidor.example/hook#frag",
            "https://consumidor.example/" + "a" * 2100,
            "javascript:alert(1)",
        ],
    )
    def test_refuses(self, url):
        with pytest.raises(InvalidWebhookUrl):
            validate_webhook_url(url, allow_http=False)

    def test_http_only_when_allowed(self):
        assert validate_webhook_url("http://api:9000/hook", allow_http=True)


class TestSignature:
    def test_known_vector(self):
        signature = sign("whsec_teste", 1700000000, b'{"a":1}')
        expected = hmac.new(b"whsec_teste", b'1700000000.{"a":1}', hashlib.sha256).hexdigest()
        assert signature == f"t=1700000000,v1={expected}"

    def test_body_or_timestamp_change_breaks_it(self):
        base = sign("s", 1, b"x")
        assert sign("s", 2, b"x") != base and sign("s", 1, b"y") != base


async def enable_webhook(world, url="https://consumidor.example/hook") -> str:
    return await world.configure_webhook().execute(
        slug=world.tenant.slug, url=url, rotate_secret=False, allow_http=False
    )


async def register(world, external_id="user-1"):
    accepted = await world.register_face().execute(
        RegisterFaceCommand(
            tenant_id=world.tenant.id,
            external_subject_id=external_id,
            capture=CaptureData(content=JPEG, content_type="image/jpeg"),
        )
    )
    await world.process_registration().execute(accepted.registration_id)
    return accepted.registration_id


class TestConfiguration:
    async def test_secret_is_returned_once_and_stored_encrypted(self, world):
        secret = await enable_webhook(world)
        tenant = world.store.tenants[world.tenant.id]
        assert secret.startswith(SECRET_PREFIX)
        assert secret.encode() not in tenant.webhook_secret
        plain = world.cipher.decrypt(
            tenant.webhook_secret, associated_data=webhook_secret_aad(tenant.id)
        )
        assert plain.decode() == secret

    async def test_changing_url_keeps_secret_unless_rotated(self, world):
        first = await enable_webhook(world)
        assert await enable_webhook(world, "https://outro.example/hook") is None
        rotated = await world.configure_webhook().execute(
            slug=world.tenant.slug,
            url="https://outro.example/hook",
            rotate_secret=True,
            allow_http=False,
        )
        assert rotated and rotated != first

    async def test_unknown_tenant(self, world):
        with pytest.raises(LookupError):
            await world.configure_webhook().execute(
                slug="ghost", url="https://x.example", rotate_secret=False, allow_http=False
            )


class TestOutbox:
    async def test_no_webhook_configured_no_delivery(self, world):
        await register(world)
        assert world.store.webhooks == {} and world.queue.webhooks == []

    async def test_result_schedules_delivery_and_triggers_queue(self, world):
        await enable_webhook(world)
        registration_id = await register(world)
        (d,) = world.store.webhooks.values()
        assert d.event_type is WebhookEventType.FACE_REGISTRATION_COMPLETED
        assert d.resource_id == registration_id and d.status is WebhookDeliveryStatus.PENDING
        assert world.queue.webhooks == [d.id]

    async def test_queue_failure_keeps_result_and_pending_delivery(self, world):
        await enable_webhook(world)
        world.queue.fail_webhooks = True
        registration_id = await register(world)
        assert world.store.registrations[registration_id].status is ProcessStatus.APPROVED
        (d,) = world.store.webhooks.values()
        assert d.status is WebhookDeliveryStatus.PENDING  # a varredura entrega

    async def test_expired_verification_also_notifies(self, world):
        await register(world)
        await enable_webhook(world)
        accepted = await world.create_verification().execute(
            CreateVerificationCommand(
                tenant_id=world.tenant.id,
                external_subject_id="user-1",
                idempotency_key="idem-webhook-01",
                capture=CaptureData(content=JPEG, content_type="image/jpeg"),
            )
        )
        world.clock.advance(hours=1)
        await world.process_verification().execute(accepted.verification_id)
        (d,) = world.store.webhooks.values()
        assert d.event_type is WebhookEventType.VERIFICATION_COMPLETED


class TestDelivery:
    async def test_delivers_signed_result_with_envelope(self, world):
        secret = await enable_webhook(world)
        registration_id = await register(world)
        assert await world.deliver_webhooks().execute() == 1

        (sent,) = world.webhook_sender.sent
        body = json.loads(sent.body)
        assert sent.url == "https://consumidor.example/hook" and sent.secret == secret
        assert body["id"] == str(sent.event_id) and body["type"] == "face_registration.completed"
        assert body["data"]["registration_id"] == str(registration_id)
        assert body["data"]["decision"] == "APPROVED"
        assert not {"similarity", "template", "score"} & set(body["data"])
        (d,) = world.store.webhooks.values()
        assert d.status is WebhookDeliveryStatus.DELIVERED and d.last_status_code == 200

    @pytest.mark.parametrize("response", [500, 404, 302, "TIMEOUT", "CONNECTION_ERROR"])
    async def test_failure_reschedules_with_backoff(self, world, response):
        await enable_webhook(world)
        await register(world)
        world.webhook_sender.responses = [response]
        await world.deliver_webhooks().execute()
        (d,) = world.store.webhooks.values()
        assert d.status is WebhookDeliveryStatus.PENDING and d.attempts == 1
        assert d.next_attempt_at == world.clock.now() + RETRY_DELAYS[0]
        expected = f"HTTP_{response}" if isinstance(response, int) else response
        assert d.last_error == expected

    async def test_not_due_is_not_retried_early(self, world):
        await enable_webhook(world)
        await register(world)
        world.webhook_sender.responses = [500]
        await world.deliver_webhooks().execute()
        assert await world.deliver_webhooks().execute() == 0
        world.clock.advance(minutes=1)
        assert await world.deliver_webhooks().execute() == 1
        (d,) = world.store.webhooks.values()
        assert d.status is WebhookDeliveryStatus.DELIVERED and d.attempts == 2

    async def test_gives_up_after_all_attempts(self, world):
        await enable_webhook(world)
        await register(world)
        world.webhook_sender.responses = [503] * MAX_ATTEMPTS
        for _ in range(MAX_ATTEMPTS):
            await world.deliver_webhooks().execute()
            world.clock.advance(hours=9)
        (d,) = world.store.webhooks.values()
        assert d.status is WebhookDeliveryStatus.FAILED and d.attempts == MAX_ATTEMPTS
        assert len(world.webhook_sender.sent) == MAX_ATTEMPTS

    async def test_disabled_webhook_abandons_pending(self, world):
        await enable_webhook(world)
        await register(world)
        world.store.tenants[world.tenant.id].disable_webhook(world.clock.now())
        await world.deliver_webhooks().execute()
        (d,) = world.store.webhooks.values()
        assert d.status is WebhookDeliveryStatus.FAILED and d.last_error == "WEBHOOK_DISABLED"
        assert world.webhook_sender.sent == []

    async def test_each_delivery_has_its_own_event_id(self, world):
        await enable_webhook(world)
        await register(world, "user-1")
        await register(world, "user-2")
        await world.deliver_webhooks().execute()
        ids = {s.event_id for s in world.webhook_sender.sent}
        assert len(ids) == 2 and ids == set(world.store.webhooks)

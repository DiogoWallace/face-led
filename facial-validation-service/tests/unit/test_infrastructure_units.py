import json
import logging

import pytest
from cryptography.exceptions import InvalidTag

from app.application.use_cases.authenticate_tenant import generate_api_key, hash_api_key
from app.infrastructure.observability.logging import REDACTED, JsonFormatter, redact
from app.infrastructure.security.template_cipher import AesGcmTemplateCipher, generate_key


class TestTemplateCipher:
    def test_roundtrip_and_ciphertext_differs(self):
        cipher = AesGcmTemplateCipher(generate_key())
        blob = cipher.encrypt(b"embedding", associated_data=b"t:s:r")
        assert b"embedding" not in blob
        assert cipher.decrypt(blob, associated_data=b"t:s:r") == b"embedding"

    def test_wrong_associated_data_fails(self):
        cipher = AesGcmTemplateCipher(generate_key())
        blob = cipher.encrypt(b"embedding", associated_data=b"tenant-a")
        with pytest.raises(InvalidTag):
            cipher.decrypt(blob, associated_data=b"tenant-b")

    def test_rejects_short_key(self):
        with pytest.raises(ValueError):
            AesGcmTemplateCipher("c2hvcnQ=")


class TestApiKeys:
    def test_hash_is_deterministic_and_not_the_key(self):
        key = generate_api_key()
        assert key.startswith("fvs_")
        assert hash_api_key(key) == hash_api_key(key)
        assert key not in hash_api_key(key)


class TestLoggingRedaction:
    def test_redacts_sensitive_keys_recursively(self):
        data = {"api_key": "k", "nested": {"embedding": [1, 2], "ok": 1}, "raw": b"\x00\x01"}
        assert redact(data) == {
            "api_key": REDACTED,
            "nested": {"embedding": REDACTED, "ok": 1},
            "raw": "<2 bytes>",
        }

    def test_formatter_redacts_extras(self):
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "evento", (), None)
        record.authorization = "Bearer secret"
        record.image = b"\xff\xd8"
        record.verification_id = "abc"
        payload = json.loads(JsonFormatter().format(record))
        assert payload["authorization"] == REDACTED
        assert payload["image"] == REDACTED
        assert payload["verification_id"] == "abc"


class _FakeJob:
    def __init__(self, outcome) -> None:
        self.outcome = outcome

    async def result(self, timeout=None, poll_delay=0.5):  # noqa: ASYNC109 - assinatura do arq
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _FakePool:
    def __init__(self, job) -> None:
        self.job = job
        self.enqueued: list[tuple] = []

    async def enqueue_job(self, function, *args):
        self.enqueued.append((function, *args))
        return self.job


class TestArqFrameObserver:
    """Medida de quadro da página /dev/liveness: falha do worker vira None, nunca exceção."""

    async def test_returns_worker_answer(self):
        from app.infrastructure.queue.arq_queue import OBSERVE_LIVENESS_FRAME, ArqFrameObserver

        pool = _FakePool(_FakeJob({"face_count": 1}))
        assert await ArqFrameObserver(pool).observe(b"x", "image/jpeg") == {"face_count": 1}
        assert pool.enqueued == [(OBSERVE_LIVENESS_FRAME, b"x", "image/jpeg")]

    @pytest.mark.parametrize("outcome", [TimeoutError(), RuntimeError("worker")])
    async def test_timeout_or_failure_is_none(self, outcome):
        from app.infrastructure.queue.arq_queue import ArqFrameObserver

        assert await ArqFrameObserver(_FakePool(_FakeJob(outcome))).observe(b"x", "x") is None

    async def test_redis_down_is_queue_error(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        from app.application.ports import TaskQueueError
        from app.infrastructure.queue.arq_queue import ArqFrameObserver

        class Down:
            async def enqueue_job(self, *_):
                raise RedisConnectionError("down")

        with pytest.raises(TaskQueueError):
            await ArqFrameObserver(Down()).observe(b"x", "x")

import uuid

import pytest
from arq.jobs import Job, JobStatus

from app.application.ports import CaptureStorageError
from app.domain.value_objects import CaptureData
from app.infrastructure.queue.arq_queue import ArqTaskQueue, create_queue_pool
from tests.fakes import JPEG

pytestmark = pytest.mark.integration


async def test_queue_enqueues_and_deduplicates(redis_url):
    pool = await create_queue_pool(redis_url)
    try:
        await pool.flushdb()
        queue = ArqTaskQueue(pool)
        verification_id = uuid.uuid4()
        await queue.enqueue_verification(verification_id)
        await queue.enqueue_verification(verification_id)  # mesmo _job_id: ignorado
        job = Job(f"ver:{verification_id}", pool)
        assert await job.status() is JobStatus.queued
        assert await pool.zcard("arq:queue") == 1
    finally:
        await pool.flushdb()
        await pool.aclose()


async def test_storage_roundtrip(storage):
    key = f"tests/{uuid.uuid4()}"
    await storage.put(key, CaptureData(content=JPEG, content_type="image/jpeg"))
    stored = await storage.get(key)
    assert stored.content == JPEG and stored.content_type == "image/jpeg"
    url = await storage.temporary_url(key, 60)
    assert "X-Amz-Signature" in url and "X-Amz-Expires=60" in url
    await storage.delete(key)
    with pytest.raises(CaptureStorageError):
        await storage.get(key)


async def test_storage_check(storage):
    await storage.check()

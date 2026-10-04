"""Fluxo completo sobre PostgreSQL + S3 reais e AES-GCM real.

O motor biométrico continua sendo o dublê roteirizado: este teste valida
persistência, storage, criptografia e estados, não reconhecimento facial.
"""

import uuid
from datetime import timedelta

import pytest

from app.application.dto import CapturePolicy, CreateVerificationCommand, RegisterFaceCommand
from app.application.use_cases import (
    CreateVerification,
    GetVerification,
    ProcessFaceRegistration,
    ProcessVerification,
    RegisterFace,
)
from app.application.use_cases.capture_analysis import FaceAnalysisPipeline
from app.container import SystemClock
from app.domain.services import (
    FaceMatchPolicy,
    FaceMatchPolicyRegistry,
    LivenessRequirement,
    QualityGate,
    QualityRequirements,
)
from app.domain.value_objects import CaptureData, Decision, ProcessStatus
from app.infrastructure.security.template_cipher import AesGcmTemplateCipher, generate_key
from tests.fakes import JPEG, BiometricScript, RecordingQueue

pytestmark = pytest.mark.integration


async def test_register_then_verify(uow_factory, tenant_and_subject, storage):
    tenant, _ = tenant_and_subject
    queue, clock, script = RecordingQueue(), SystemClock(), BiometricScript()
    pipeline = FaceAnalysisPipeline(
        components=script.components(),
        quality_gate=QualityGate(QualityRequirements(min_face_px=40, min_sharpness=10)),
        liveness_requirement=LivenessRequirement.REQUIRED,
    )
    cipher = AesGcmTemplateCipher(generate_key())
    policies = FaceMatchPolicyRegistry()
    policies.register(
        FaceMatchPolicy(
            version="it-v1", model_name="scripted-model", model_version="1", min_similarity=0.5
        ),
        activate=True,
    )
    common = dict(uow_factory=uow_factory, storage=storage, clock=clock, new_id=uuid.uuid4)
    capture = CaptureData(content=JPEG, content_type="image/jpeg")
    capture_policy = CapturePolicy(max_bytes=10_000)

    accepted = await RegisterFace(queue=queue, capture_policy=capture_policy, **common).execute(
        RegisterFaceCommand(tenant_id=tenant.id, external_subject_id="user-2", capture=capture)
    )
    status = await ProcessFaceRegistration(
        pipeline=pipeline, cipher=cipher, queue=queue, **common
    ).execute(accepted.registration_id)
    assert status is ProcessStatus.APPROVED

    async with uow_factory() as uow:
        stored = await uow.face_registrations.get(accepted.registration_id)
    assert stored.template and script.embedding not in stored.template

    view = await CreateVerification(
        queue=queue, capture_policy=capture_policy, ttl=timedelta(minutes=5), **common
    ).execute(
        CreateVerificationCommand(
            tenant_id=tenant.id,
            external_subject_id="user-2",
            idempotency_key="it-idem-0001",
            capture=capture,
        )
    )
    status = await ProcessVerification(
        pipeline=pipeline, cipher=cipher, policies=policies, queue=queue, **common
    ).execute(view.verification_id)
    assert status is ProcessStatus.APPROVED

    result = await GetVerification(uow_factory=uow_factory).execute(tenant.id, view.verification_id)
    assert result.decision is Decision.APPROVED and result.policy_version == "it-v1"

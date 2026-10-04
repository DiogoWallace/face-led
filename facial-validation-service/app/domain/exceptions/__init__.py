"""Exceções de domínio. Nunca carregam imagens, embeddings ou segredos na mensagem."""


class DomainError(Exception):
    code = "DOMAIN_ERROR"


class InvalidStateTransition(DomainError):
    code = "INVALID_STATE_TRANSITION"


class InvalidCapture(DomainError):
    code = "INVALID_CAPTURE"


class AuthenticationFailed(DomainError):
    code = "UNAUTHENTICATED"


class SubjectNotFound(DomainError):
    code = "SUBJECT_NOT_FOUND"


class SubjectNotEnrolled(DomainError):
    code = "SUBJECT_NOT_ENROLLED"


class SubjectAlreadyEnrolled(DomainError):
    code = "SUBJECT_ALREADY_ENROLLED"


class VerificationNotFound(DomainError):
    code = "VERIFICATION_NOT_FOUND"


class FaceRegistrationNotFound(DomainError):
    code = "FACE_REGISTRATION_NOT_FOUND"


class LivenessSessionNotFound(DomainError):
    code = "LIVENESS_SESSION_NOT_FOUND"


class LivenessSessionInvalid(DomainError):
    """Usada, expirada, de outro subject ou de outra finalidade."""

    code = "LIVENESS_SESSION_INVALID"


class LivenessEvidenceRequired(DomainError):
    """O liveness ativo exige liveness_session_id e quadros na requisição."""

    code = "LIVENESS_EVIDENCE_REQUIRED"


class IdempotencyConflict(DomainError):
    code = "IDEMPOTENCY_CONFLICT"


class DuplicateIdempotencyKey(DomainError):
    """Sinaliza corrida na criação com a mesma Idempotency-Key (tratada no caso de uso)."""

    code = "DUPLICATE_IDEMPOTENCY_KEY"


class PolicyModelMismatch(DomainError):
    code = "POLICY_MODEL_MISMATCH"


class InvalidPolicy(DomainError):
    code = "INVALID_POLICY"

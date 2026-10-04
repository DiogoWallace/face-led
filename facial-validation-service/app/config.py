from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração via variáveis de ambiente (ver .env.example). Segredos são SecretStr."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, env_ignore_empty=True)

    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_name: str = "facial-validation-service"
    log_level: str = "INFO"

    database_url: SecretStr
    redis_url: SecretStr

    s3_endpoint: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str
    s3_access_key: SecretStr
    s3_secret_key: SecretStr
    s3_auto_create_bucket: bool = False

    # Componentes biométricos independentes (ADR-006). "none" = não configurado.
    biometric_detector: str = "none"
    biometric_quality_assessor: str = "none"
    biometric_embedder: str = "none"
    biometric_comparator: str = "none"
    liveness_provider: str = "none"
    # REQUIRED | DISABLED_FOR_EVALUATION (este último só em local/test).
    liveness_requirement: str = "REQUIRED"

    # Liveness ativo próprio (LIVENESS_PROVIDER=active, ADR-010). Valores INICIAIS,
    # PENDING CALIBRATION, sem medição com capturas reais (ADR-010). Fora de local/test, o serviço
    # exige LIVENESS_CALIBRATION_STATUS=CALIBRATED e o limiar de mesma pessoa.
    liveness_calibration_status: Literal["PENDING_CALIBRATION", "CALIBRATED"] = (
        "PENDING_CALIBRATION"
    )
    liveness_challenge_steps: int = Field(default=3, ge=2, le=4)
    liveness_session_ttl_seconds: int = Field(default=120, gt=0, le=600)
    liveness_min_frames: int = Field(default=8, ge=2)
    liveness_max_frames: int = Field(default=120, ge=2, le=120)
    liveness_frame_max_bytes: int = Field(default=300_000, gt=0)
    liveness_min_tracked_ratio: float = Field(default=0.8, gt=0, le=1)
    liveness_frontal_max_yaw: float = Field(default=0.15, gt=0)
    liveness_turn_min_yaw: float = Field(default=0.35, gt=0)
    liveness_turn_min_eye_ratio: float = Field(default=0.65, gt=0, le=1)
    liveness_closer_min_scale: float = Field(default=1.25, gt=1)
    liveness_max_yaw_jump: float = Field(default=0.25, gt=0)
    liveness_max_scale_jump: float = Field(default=0.25, gt=0)
    # Similaridade mínima entre a selfie e cada quadro-prova. Sem padrão: calibração.
    liveness_same_person_min_similarity: float | None = None

    # Runtime OpenCV (YuNet/SFace). Modelos fora do Git, conferidos por SHA-256;
    # carregados só pelo worker, uma instância por thread de inferência.
    biometric_models_dir: str = "models"
    biometric_inference_threads: int = Field(default=1, ge=1)
    biometric_opencv_threads: int | None = Field(default=None, ge=1)  # None = padrão do OpenCV
    # Score mínimo de detecção do YuNet. 0.6 é o valor do POC; revisar na calibração.
    biometric_detector_score_threshold: float = Field(default=0.6, gt=0, lt=1)

    # Modelo ao qual a FaceMatchPolicy se aplica.
    biometric_model: str | None = None
    biometric_model_version: str | None = None

    # FaceMatchPolicy. Sem valores padrão de propósito: dependem do modelo e de
    # calibração (ADR-003). Referência LFW para opencv-sface/2021dec: 0.3443,
    # status PENDING CALIBRATION; não é valor de produção.
    face_match_policy_version: str | None = None
    face_match_min_similarity: float | None = None
    face_match_calibration_status: str = "PENDING_CALIBRATION"
    face_match_target_far: float | None = None  # PENDING BUSINESS DECISION
    face_match_calibration_reference: str | None = None

    # QualityGate. Vazio = critério não aplicado (PENDING CALIBRATION).
    quality_min_face_px: float | None = None
    quality_min_face_ratio: float | None = None
    quality_min_sharpness: float | None = None
    quality_min_brightness: float | None = None
    quality_max_brightness: float | None = None

    # 32 bytes em base64 url-safe. Gestão via KMS/Vault é decisão pendente (ADR-004).
    template_encryption_key: SecretStr

    # Retenção da captura após o processamento. PENDING LEGAL/BUSINESS DECISION:
    # KEEP apenas preserva o comportamento original (ver domain/value_objects/retention.py).
    capture_retention: Literal["KEEP", "DELETE_AFTER_PROCESSING"] = "KEEP"

    # Webhook de resultado (ADR-009): tempo máximo de cada tentativa de entrega.
    webhook_timeout_seconds: float = Field(default=10, gt=0, le=60)

    max_capture_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    verification_ttl_seconds: int = Field(default=900, gt=0)

    otel_enabled: bool = False
    otel_service_name: str = "facial-validation-service"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

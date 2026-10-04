from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FACE_LED_", env_file=".env", extra="ignore")

    serial_port: str = "/dev/ttyUSB0"
    baud: int = 115200
    # Só 127.0.0.1: o worker do Docker Desktop chega por host.docker.internal.
    host: str = "127.0.0.1"
    port: int = 18200
    # Segredo whsec_... mostrado UMA vez pelo set-webhook do facial-validation-service.
    webhook_secret: SecretStr | None = None
    signature_tolerance_seconds: int = 300
    # Eventos que acionam o LED (separados por vírgula).
    event_types: str = "verification.completed"

    @property
    def event_type_set(self) -> frozenset[str]:
        return frozenset(t.strip() for t in self.event_types.split(",") if t.strip())

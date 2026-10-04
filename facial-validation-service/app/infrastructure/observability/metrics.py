"""Porta mínima de métricas. Implementação real (Prometheus/OTel metrics) é pendente."""

from typing import Protocol


class Metrics(Protocol):
    def increment(self, name: str, *, tags: dict[str, str] | None = None) -> None: ...
    def observe(self, name: str, value: float, *, tags: dict[str, str] | None = None) -> None: ...


class NoopMetrics:
    def increment(self, name: str, *, tags: dict[str, str] | None = None) -> None:
        return None

    def observe(self, name: str, value: float, *, tags: dict[str, str] | None = None) -> None:
        return None

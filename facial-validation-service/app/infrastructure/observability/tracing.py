"""Ponto de extensão para OpenTelemetry.

Desligado por padrão. Para ativar: instalar o extra `otel` (pip install .[otel]),
definir OTEL_ENABLED=true e as variáveis OTEL_EXPORTER_OTLP_* padrão do SDK.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def configure_tracing(app: Any, *, enabled: bool, service_name: str) -> None:
    if not enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("otel_not_installed")
        return

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    # Rotas com upload: não capturar corpo nem headers de autenticação.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready")

from __future__ import annotations

import os
from threading import Lock
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_lock = Lock()
_configured = False


def configure_telemetry(app: Any, service_name: str) -> Any:
    global _configured
    with _lock:
        if not _configured:
            provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
            endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
            if endpoint:
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            trace.set_tracer_provider(provider)
            _configured = True
    return OpenTelemetryMiddleware(app)

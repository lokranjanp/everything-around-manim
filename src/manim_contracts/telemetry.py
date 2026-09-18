from __future__ import annotations

import os
from threading import Lock

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_lock = Lock()
_configured = False


def configure_telemetry(app: FastAPI, service_name: str) -> None:
    global _configured
    with _lock:
        if not _configured:
            provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
            endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
            if endpoint:
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            trace.set_tracer_provider(provider)
            _configured = True
    FastAPIInstrumentor.instrument_app(app)

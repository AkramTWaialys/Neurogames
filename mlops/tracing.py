"""
OpenTelemetry Tracing — NeuroGames MLOps Backend
==================================================
Provides fail-soft, opt-in distributed tracing for the FastAPI server,
background pipeline tasks, and PostgreSQL operations.

Design principles:
- Fully optional: if OTel packages are missing or the collector is
  unreachable, everything degrades silently to no-ops.
- Idempotent: calling ``init_tracing()`` multiple times is safe.
- Worker-safe: background tasks (ProcessPoolExecutor) create independent
  root spans. Trace context is NOT automatically propagated across process
  boundaries. Each task includes a ``task_id`` attribute for manual
  correlation in Grafana/Tempo.
- Never raises: all public functions catch exceptions internally.

Usage::

    # In FastAPI startup (server.py):
    from mlops.tracing import init_tracing
    init_tracing()

    # In a pipeline task:
    from mlops.tracing import init_tracing, trace_span
    init_tracing(service_name="neurogames-worker")
    with trace_span("classify_pipeline", {"game": game}):
        ...

    # On a DB function:
    from mlops.tracing import trace_db_op
    @trace_db_op
    def insert_session(...): ...
"""

from __future__ import annotations

import functools
import logging
import os
import sys
from contextlib import contextmanager
from typing import Generator, Optional

log = logging.getLogger(__name__)

_tracer = None
_initialized: bool = False
_NOOP: bool = True  # True until a successful init


def init_tracing(
    service_name: str = "neurogames-api",
    endpoint: Optional[str] = None,
) -> None:
    """
    Initialize OTel with an OTLP gRPC exporter.

    Safe to call multiple times — subsequent calls are no-ops.
    Silently degrades if OTel packages are not installed or if the
    collector endpoint is unreachable at startup.

    Args:
        service_name: OTel resource ``service.name`` attribute.
        endpoint:     OTLP gRPC endpoint URL.  Falls back to the
                      ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var, then
                      ``http://localhost:4317``.
    """
    global _tracer, _initialized, _NOOP
    if _initialized:
        return
    _initialized = True

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        _endpoint = (
            endpoint
            or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        )

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=_endpoint, insecure=True)
            )
        )
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
        _NOOP = False
        log.info("OTel tracing initialised → %s (service=%s)", _endpoint, service_name)

    except Exception as exc:  # ImportError, socket errors, etc.
        log.info("OTel tracing unavailable (non-fatal): %s", exc)


def get_tracer():
    """Return the global tracer, initialising lazily if needed."""
    global _tracer
    if not _initialized:
        init_tracing()
    return _tracer


@contextmanager
def trace_span(
    name: str,
    attributes: Optional[dict] = None,
) -> Generator:
    """
    Context manager that creates a named OTel span.

    Completely transparent no-op when OTel is unavailable.

    Args:
        name:       Span name (e.g. "classify_pipeline").
        attributes: Optional dict of span attributes (values coerced to str).

    Yields:
        The active span, or ``None`` in no-op mode.

    Example::

        with trace_span("drift_retrain", {"game": "gonogo"}) as span:
            result = run_drift_retrain()
            if span:
                span.set_attribute("retrained", str(result.get("retrained")))
    """
    if _NOOP:
        yield None
        return

    try:
        tracer = get_tracer()
        if tracer is None:
            raise RuntimeError("tracer unavailable")
        span_cm = tracer.start_as_current_span(name)
        span = span_cm.__enter__()
    except Exception as exc:
        log.debug("OTel span unavailable (non-fatal): %s", exc)
        yield None
        return

    exc_info = (None, None, None)
    try:
        if attributes:
            for k, v in attributes.items():
                try:
                    span.set_attribute(k, str(v))
                except Exception:
                    pass
        try:
            yield span
        except Exception as exc:
            exc_info = sys.exc_info()
            try:
                span.set_attribute("error", True)
                span.set_attribute("error.message", str(exc))
                span.record_exception(exc)
            except Exception:
                pass
            raise
    finally:
        try:
            span_cm.__exit__(*exc_info)
        except Exception as exc:
            log.debug("OTel span close failed (non-fatal): %s", exc)


def trace_db_op(func):
    """
    Decorator that wraps a DB function with an OTel child span.

    No-op when OTel is unavailable — the original function is called
    directly with zero overhead.

    Span attributes:
        ``db.system``    = "postgresql"
        ``db.operation`` = function name

    Example::

        @trace_db_op
        def insert_session(game, session_dict):
            ...
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if _NOOP:
            return func(*args, **kwargs)
        with trace_span(
            f"db.{func.__name__}",
            {"db.system": "postgresql", "db.operation": func.__name__},
        ):
            return func(*args, **kwargs)

    return wrapper

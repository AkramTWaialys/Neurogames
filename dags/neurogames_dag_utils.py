"""Shared helpers for NeuroGames Airflow DAGs."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from airflow.exceptions import AirflowException, AirflowSkipException
from airflow.providers.http.hooks.http import HttpHook
from airflow.models import Variable


log = logging.getLogger(__name__)


def _as_utc(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def prevent_duplicate_dag_run(dedupe_window_seconds: int = 120, **context) -> bool:
    """
    Skip accidental duplicate DagRuns created by rapid manual triggers.

    Airflow's max_active_runs=1 prevents concurrent execution, but it still lets
    a second manual DagRun queue and execute later. This guard compares the
    DagRun logical timestamp, so a queued duplicate is still skipped even if it
    starts after the first run has already finished.
    """
    dag_run = context.get("dag_run")
    if dag_run is None:
        return True

    dag_id = dag_run.dag_id
    run_id = dag_run.run_id
    logical_date = _as_utc(
        getattr(dag_run, "logical_date", None)
        or getattr(dag_run, "execution_date", None)
    )
    variable_key = f"neurogames_last_accepted_run__{dag_id}"

    last_raw = Variable.get(variable_key, default_var=None)
    if last_raw:
        try:
            last = json.loads(last_raw)
        except json.JSONDecodeError:
            last = {}

        last_run_id = last.get("run_id")
        last_logical_date = _parse_utc(last.get("logical_date"))
        if last_run_id != run_id and last_logical_date is not None:
            delta_seconds = abs((logical_date - last_logical_date).total_seconds())
            if delta_seconds <= dedupe_window_seconds:
                raise AirflowSkipException(
                    f"Skipping duplicate DagRun {run_id}; previous accepted run "
                    f"{last_run_id} is {round(delta_seconds, 1)}s apart."
                )

    Variable.set(
        variable_key,
        json.dumps({
            "run_id": run_id,
            "logical_date": logical_date.isoformat(),
        }),
    )
    return True


def wait_for_ready(
    endpoint: str,
    label: str,
    http_conn_id: str = "neurogames_local",
    timeout_seconds: int = 120,
    poke_interval: int = 60,
    **context: Any,
) -> bool:
    """
    Poll a readiness endpoint and skip only when the service is reachable but
    reports that prerequisites are not ready.

    HttpSensor with soft_fail=True also skips on connection failures, which
    hides broken API/connection configuration as a normal data-readiness skip.
    """
    started = time.monotonic()
    hook = HttpHook(method="GET", http_conn_id=http_conn_id)
    last_payload: Any = None

    while True:
        try:
            response = hook.run(endpoint)
        except Exception as e:
            # Handle transient connection blips (e.g. Remote end closed connection)
            # as long as we haven't timed out yet.
            elapsed = time.monotonic() - started
            if elapsed >= timeout_seconds:
                raise AirflowException(f"{label} connection failed after {timeout_seconds}s: {e}") from e
            
            log.info("%s connection blip, retrying in 5s: %s", label, e)
            time.sleep(min(5, max(0, timeout_seconds - elapsed)))
            continue

        try:
            payload = response.json()
        except ValueError as exc:
            raise AirflowException(
                f"{label} readiness endpoint returned non-JSON response: "
                f"{response.text[:500]}"
            ) from exc

        last_payload = payload
        if payload.get("ready") is True:
            log.info("%s readiness passed: %s", label, payload)
            return True

        elapsed = time.monotonic() - started
        if elapsed >= timeout_seconds:
            raise AirflowSkipException(
                f"{label} is not ready after {timeout_seconds}s: {last_payload}"
            )

        log.info("%s is not ready yet: %s", label, payload)
        time.sleep(min(poke_interval, max(0, timeout_seconds - elapsed)))


def resolve_training_config(**context) -> dict:
    """
    Resolve training flow configuration from multiple sources in order:
    1. dag_run.conf (highest priority)
    2. Airflow Variables
    3. ENV/Fallback (lowest priority)
    """
    import os
    conf = context.get("dag_run").conf if context.get("dag_run") else {}

    # 1. Threshold
    # Fallback order: conf -> variable -> env -> 20
    threshold = conf.get("threshold")
    if threshold is None:
        threshold = Variable.get("neurogames_training_threshold", default_var=None)
    if threshold is None:
        threshold = os.environ.get("TRAINING_TRIGGER_THRESHOLD", 20)
    
    # 2. Force flag
    force = conf.get("force", False)

    # 3. Games selection
    games = conf.get("games") # Optional list

    # 4. Trigger reason
    # Default is volume, unless overridden by manual trigger or drift DagRun.
    trigger_reason = conf.get("trigger_reason", "volume")

    resolved = {
        "threshold": int(threshold),
        "force": bool(force),
        "games": games,
        "trigger_reason": trigger_reason,
        "drift_run_id": conf.get("drift_run_id"),
    }
    log.info("Resolved training config: %s", resolved)
    return resolved

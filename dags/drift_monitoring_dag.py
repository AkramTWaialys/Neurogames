"""
NeuroGames — Data Drift Monitoring DAG (Non-Blocking Edition)
==============================================================
Professional MLOps closed-loop:
  1. Gate: check that real data exists with enough rows
  2. Submit drift → retrain → champion/challenger as background task
  3. Poll until completion

Precondition: At least one game must have >= 30 rows of real data.
              (checked via /ready/drift endpoint)

Default schedule: daily at 06:00.
"""

import json
from datetime import datetime, timedelta
from airflow import DAG
from airflow.exceptions import AirflowException, AirflowSkipException
from airflow.providers.http.operators.http import HttpOperator
from airflow.providers.http.sensors.http import HttpSensor
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from neurogames_dag_utils import prevent_duplicate_dag_run, wait_for_ready

# ── Configuration ────────────────────────────────────────────────────────────
NEUROGAMES_HTTP_CONN_ID = "neurogames_host"

default_args = {
    "owner": "neurogames",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def extract_task_id(response_text: str, **kwargs):
    """Extract task_id from the submit response."""
    data = json.loads(response_text)
    task_id = data.get("task_id")
    if not task_id:
        raise ValueError(f"No task_id in response: {data}")
    return task_id


def task_succeeded(resp):
    """Return True only when the background task succeeded; fail fast on errors."""
    data = resp.json()
    status = data.get("status")
    if status == "error":
        raise AirflowException(data.get("error") or f"Pipeline task failed: {data}")
    return status == "ok"


def check_for_drift(**context):
    """Check if any games drifted and return their IDs."""
    ti = context["ti"]
    task_id = ti.xcom_pull(task_ids="extract_drift_task_id")
    
    from airflow.providers.http.hooks.http import HttpHook
    hook = HttpHook(method="GET", http_conn_id=NEUROGAMES_HTTP_CONN_ID)
    response = hook.run(f"/pipeline/status/{task_id}")
    status_resp = response.json()
    
    drifted_games = status_resp.get("drifted_games", [])
    drift_run_id = status_resp.get("drift_run_id")
    
    if not drifted_games:
        raise AirflowSkipException("No drift detected in any game. Skipping training trigger.")
        
    return {"games": drifted_games, "drift_run_id": drift_run_id}


with DAG(
    dag_id="neurogames_drift_monitoring",
    default_args=default_args,
    description="Detect drift → trigger training DAG for drifted games",
    schedule_interval="0 6 * * *",      # daily at 06:00
    start_date=datetime(2026, 4, 14),
    catchup=False,
    max_active_runs=1,
    tags=["neurogames", "mlops", "drift", "monitoring", "webhook"],
) as dag:

    prevent_duplicate_run = PythonOperator(
        task_id="prevent_duplicate_run",
        python_callable=prevent_duplicate_dag_run,
        op_kwargs={"dedupe_window_seconds": 120},
        retries=0,
    )

    # ── Gate: Only proceed if there is enough real data for drift ────────
    check_ready = PythonOperator(
        task_id="check_drift_ready",
        python_callable=wait_for_ready,
        op_kwargs={
            "endpoint": "/ready/drift",
            "label": "drift",
            "http_conn_id": NEUROGAMES_HTTP_CONN_ID,
            "timeout_seconds": 120,
            "poke_interval": 60,
        },
        retries=0,
    )

    # ── Submit drift check (returns immediately) ─────────────────────────
    submit_drift = HttpOperator(
        task_id="submit_drift_check",
        http_conn_id=NEUROGAMES_HTTP_CONN_ID,
        endpoint="/pipeline/drift-check/all",
        method="POST",
        response_check=lambda resp: resp.json().get("status") in ("accepted", "already_running"),
        response_filter=lambda resp: resp.text,
        log_response=True,
        execution_timeout=timedelta(minutes=2),
        retries=0,
    )

    # ── Extract task_id ──────────────────────────────────────────────────
    extract_id = PythonOperator(
        task_id="extract_drift_task_id",
        python_callable=extract_task_id,
        op_kwargs={
            "response_text": "{{ ti.xcom_pull(task_ids='submit_drift_check') }}",
        },
    )

    # ── Poll until drift check finishes ──────────────────────────────────
    poll_drift = HttpSensor(
        task_id="poll_drift_check",
        http_conn_id=NEUROGAMES_HTTP_CONN_ID,
        endpoint="/pipeline/status/{{ ti.xcom_pull(task_ids='extract_drift_task_id') }}",
        method="GET",
        response_check=task_succeeded,
        poke_interval=30,
        timeout=3600,                  # 1 hour max
        mode="reschedule",
    )

    # ── Analyze results and decide whether to trigger training ───────────
    analyze_drift = PythonOperator(
        task_id="analyze_drift_results",
        python_callable=check_for_drift,
        provide_context=True,
    )

    # Trigger the training pipeline DAG for drifted games.
    def _build_retraining_conf(**context):
        """Build conf dict with proper types (not Jinja strings)."""
        result = context["ti"].xcom_pull(task_ids="analyze_drift_results")
        return {
            "trigger_reason": "drift",
            "force": True,
            "games": result["games"],           # list[str], not a Jinja string
            "drift_run_id": result["drift_run_id"],
        }

    build_conf = PythonOperator(
        task_id="build_retraining_conf",
        python_callable=_build_retraining_conf,
        provide_context=True,
    )

    trigger_training = TriggerDagRunOperator(
        task_id="trigger_retraining",
        trigger_dag_id="neurogames_training_pipeline",
        conf="{{ ti.xcom_pull(task_ids='build_retraining_conf') }}",
        wait_for_completion=False,
    )

    prevent_duplicate_run >> check_ready >> submit_drift >> extract_id >> poll_drift >> analyze_drift >> build_conf >> trigger_training


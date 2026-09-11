"""
NeuroGames — Weekly Clinical Reporting DAG (Non-Blocking Edition)
==================================================================
Triggers LLM-based clinical report generation via background task.
Submits work to the API then polls for completion.

    - Snapshot reports  → every Sunday at 08:00
    - Progress reports  → every Sunday at 08:00  (comparative analysis)

Precondition: At least 1 participant must have data.
              (checked via /ready/reporting endpoint)

Reports are saved to mlops_outputs/reports/.
"""

import json
from datetime import datetime, timedelta
from airflow import DAG
from airflow.exceptions import AirflowException
from airflow.providers.http.operators.http import HttpOperator
from airflow.providers.http.sensors.http import HttpSensor
from airflow.operators.python import PythonOperator

from neurogames_dag_utils import prevent_duplicate_dag_run, wait_for_ready

# ── Configuration ────────────────────────────────────────────────────────────
NEUROGAMES_HTTP_CONN_ID = "neurogames_host"

default_args = {
    "owner": "neurogames",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
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


# with DAG(
#     dag_id="neurogames_weekly_reports",
#     default_args=default_args,
#     description="Generate weekly LLM clinical reports (only if participant data exists)",
#     schedule_interval=None,             # on-demand only (disabled weekly batch)
#     start_date=datetime(2026, 4, 14),
#     catchup=False,
#     max_active_runs=1,
#     tags=["neurogames", "reports", "llm", "webhook"],
# ) as dag:
# 
#     prevent_duplicate_run = PythonOperator(
#         task_id="prevent_duplicate_run",
#         python_callable=prevent_duplicate_dag_run,
#         op_kwargs={"dedupe_window_seconds": 120},
#         retries=0,
#     )
# 
#     # ── Gate: Only proceed if there are participants with data ───────────
#     check_ready = PythonOperator(
#         task_id="check_reporting_ready",
#         python_callable=wait_for_ready,
#         op_kwargs={
#             "endpoint": "/ready/reporting",
#             "label": "reporting",
#             "http_conn_id": NEUROGAMES_HTTP_CONN_ID,
#             "timeout_seconds": 120,
#             "poke_interval": 60,
#         },
#         retries=0,
#     )
# 
#     # ── Submit report generation (returns immediately) ───────────────────
#     submit_reports = HttpOperator(
#         task_id="submit_smart_reports",
#         http_conn_id=NEUROGAMES_HTTP_CONN_ID,
#         endpoint="/pipeline/report/smart",
#         method="POST",
#         response_check=lambda resp: resp.json().get("status") in ("accepted", "already_running"),
#         response_filter=lambda resp: resp.text,
#         log_response=True,
#         execution_timeout=timedelta(minutes=2),
#         retries=0,
#     )
# 
#     # ── Extract task_id ──────────────────────────────────────────────────
#     extract_id = PythonOperator(
#         task_id="extract_reports_task_id",
#         python_callable=extract_task_id,
#         op_kwargs={
#             "response_text": "{{ ti.xcom_pull(task_ids='submit_smart_reports') }}",
#         },
#     )
# 
#     # ── Poll until reports finish ────────────────────────────────────────
#     poll_reports = HttpSensor(
#         task_id="poll_smart_reports",
#         http_conn_id=NEUROGAMES_HTTP_CONN_ID,
#         endpoint="/pipeline/status/{{ ti.xcom_pull(task_ids='extract_reports_task_id') }}",
#         method="GET",
#         response_check=task_succeeded,
#         poke_interval=60,              # poll every minute (reports take ~30s each)
#         timeout=14400,                 # 4 hours max
#         mode="poke",
#     )
# 
#     prevent_duplicate_run >> check_ready >> submit_reports >> extract_id >> poll_reports

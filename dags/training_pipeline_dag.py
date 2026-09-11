"""
NeuroGames training DAG.

The DAG asks the FastAPI backend for the current training plan, then runs the
main stages required for a reliable training cycle: anomaly screening,
classification, model registration, and cross-game consensus. The list of
games comes from the backend registry, not from a hard-coded five-game constant.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from airflow import DAG
from airflow.exceptions import AirflowException, AirflowSkipException
from airflow.operators.python import PythonOperator
from airflow.providers.http.hooks.http import HttpHook
from airflow.providers.http.operators.http import HttpOperator
from airflow.providers.http.sensors.http import HttpSensor
from airflow.utils.trigger_rule import TriggerRule

from neurogames_dag_utils import prevent_duplicate_dag_run, resolve_training_config


NEUROGAMES_HTTP_CONN_ID = "neurogames_host"
HTTP_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 10

default_args = {
    "owner": "neurogames",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def _response_preview(resp, limit: int = 4000) -> str:
    try:
        return resp.text[:limit]
    except Exception:
        return "<response body unavailable>"


def extract_task_id(response_text: str, **kwargs):
    data = json.loads(response_text)
    task_id = data.get("task_id")
    if not task_id:
        raise AirflowException(f"No task_id in response: {data}")
    return task_id


def task_succeeded(resp):
    if resp.status_code == 404:
        raise AirflowException(
            f"Pipeline task status disappeared from API memory: {_response_preview(resp, 1000)}"
        )
    if resp.status_code >= 400:
        raise AirflowException(
            f"Pipeline status HTTP {resp.status_code}: {_response_preview(resp, 1000)}"
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise AirflowException(
            f"Pipeline status returned invalid JSON: {_response_preview(resp, 1000)}"
        ) from exc

    status = data.get("status")
    if status == "error":
        message = data.get("error") or "Pipeline task failed"
        error_traceback = data.get("error_traceback")
        if error_traceback:
            message = f"{message}\n{error_traceback[-3000:]}"
        raise AirflowException(message)
    if status == "ok":
        return True
    if status == "running":
        return False
    raise AirflowException(f"Unexpected pipeline task status: {data}")


def validate_training_plan(**context):
    plan = context["ti"].xcom_pull(task_ids="build_training_plan")
    eligible = plan.get("eligible_games", [])
    skipped = plan.get("skipped_games", [])
    print(f"Training plan - eligible: {eligible} | skipped: {[s.get('game') for s in skipped]}")
    if not eligible:
        raise AirflowSkipException(
            f"No game modules met the threshold ({plan.get('threshold')} new sessions)."
        )
    return True


def _submit_pipeline_task(
    hook: HttpHook,
    endpoint: str,
    payload: dict | None = None,
) -> dict:
    response = hook.run(
        endpoint=endpoint,
        data=json.dumps(payload or {}),
        headers={"Content-Type": "application/json"},
        extra_options={"check_response": False, "timeout": HTTP_TIMEOUT_SECONDS},
    )
    if response.status_code >= 400:
        raise AirflowException(f"{endpoint} failed: {_response_preview(response)}")

    data = response.json()
    if data.get("status") == "skipped":
        return {"status": "skipped", "response": data}
    if data.get("status") not in ("accepted", "already_running"):
        raise AirflowException(f"Unexpected submit response from {endpoint}: {data}")

    task_id = data.get("task_id")
    if not task_id:
        raise AirflowException(f"No task_id returned from {endpoint}: {data}")
    return {"status": data.get("status"), "task_id": task_id, "response": data}


def _poll_pipeline_tasks(
    task_refs: dict[str, dict],
    timeout_seconds: int = 3600,
) -> dict:
    status_hook = HttpHook(method="GET", http_conn_id=NEUROGAMES_HTTP_CONN_ID)
    import time

    results = {}
    pending = dict(task_refs)
    deadline = time.monotonic() + timeout_seconds
    while pending and time.monotonic() < deadline:
        for game, ref in list(pending.items()):
            task_id = ref["task_id"]
            poll = status_hook.run(
                endpoint=f"/pipeline/status/{task_id}",
                extra_options={"check_response": False, "timeout": HTTP_TIMEOUT_SECONDS},
            )
            try:
                if task_succeeded(poll):
                    results[game] = poll.json()
                    pending.pop(game, None)
            except AirflowException as exc:
                raise AirflowException(
                    f"Pipeline task for game '{game}' failed while polling {task_id}: {exc}"
                ) from exc
        if pending:
            time.sleep(POLL_INTERVAL_SECONDS)

    if pending:
        pending_desc = ", ".join(
            f"{game}:{ref['task_id']}" for game, ref in pending.items()
        )
        raise AirflowException(
            f"Timed out waiting for pipeline tasks after {timeout_seconds}s: {pending_desc}"
        )
    return results


def _classification_payload(context) -> dict:
    training_config = context["ti"].xcom_pull(task_ids="resolve_training_config")
    return {
        "trigger_reason": training_config["trigger_reason"],
        "threshold": training_config["threshold"],
        "drift_run_id": training_config["drift_run_id"],
    }


def _registration_payload_for_game(context, game: str) -> dict:
    classification_results = (
        context["ti"].xcom_pull(task_ids="classify_eligible_game_modules") or {}
    )
    game_status = classification_results.get(game) or {}
    result = game_status.get("result") or game_status
    run_id = result.get("run_id")
    if not run_id:
        raise AirflowException(
            f"Cannot register {game}: classification stage did not return an MLflow run_id. "
            f"Classification result: {game_status}"
        )
    return {"run_id": run_id}


def run_game_module_stage(stage: str, **context):
    plan = context["ti"].xcom_pull(task_ids="build_training_plan")
    eligible_games = plan.get("eligible_games", [])
    if not eligible_games:
        raise AirflowSkipException("No eligible game modules in training plan.")

    endpoints = {
        "classification": "/pipeline/classify/{game}",
        "anomaly": "/pipeline/anomaly/{game}",
        "registration": "/pipeline/register/{game}",
    }
    if stage not in endpoints:
        raise AirflowException(f"Unsupported game-module pipeline stage: {stage}")

    hook = HttpHook(method="POST", http_conn_id=NEUROGAMES_HTTP_CONN_ID)
    submitted = {}
    results = {}
    for game in eligible_games:
        endpoint = endpoints[stage].format(game=game)
        if stage == "classification":
            payload = _classification_payload(context)
        elif stage == "registration":
            payload = _registration_payload_for_game(context, game)
        else:
            payload = None
        print(f"Running {stage} for game module '{game}' via {endpoint}")
        ref = _submit_pipeline_task(hook, endpoint, payload)
        if ref["status"] == "skipped":
            results[game] = ref["response"]
        else:
            submitted[game] = ref
    if submitted:
        results.update(_poll_pipeline_tasks(submitted))
    return results


with DAG(
    dag_id="neurogames_training_pipeline",
    default_args=default_args,
    description="Screen, train, register, and aggregate eligible ADHD game modules.",
    schedule_interval="0 0 * * *",
    start_date=datetime(2026, 4, 14),
    catchup=False,
    max_active_runs=1,
    tags=["neurogames", "mlops", "training", "game-modules"],
) as dag:

    prevent_duplicate_run = PythonOperator(
        task_id="prevent_duplicate_run",
        python_callable=prevent_duplicate_dag_run,
        op_kwargs={"dedupe_window_seconds": 120},
        retries=0,
    )

    resolve_config = PythonOperator(
        task_id="resolve_training_config",
        python_callable=resolve_training_config,
        provide_context=True,
    )

    build_plan = HttpOperator(
        task_id="build_training_plan",
        http_conn_id=NEUROGAMES_HTTP_CONN_ID,
        endpoint="/pipeline/training-plan",
        method="POST",
        data="""{
            "threshold": {{ ti.xcom_pull(task_ids='resolve_training_config')['threshold'] | tojson }},
            "force": {{ ti.xcom_pull(task_ids='resolve_training_config')['force'] | tojson }},
            "games": {{ ti.xcom_pull(task_ids='resolve_training_config')['games'] | tojson }},
            "trigger_reason": {{ ti.xcom_pull(task_ids='resolve_training_config')['trigger_reason'] | tojson }},
            "drift_run_id": {{ ti.xcom_pull(task_ids='resolve_training_config')['drift_run_id'] | tojson }}
        }""",
        headers={"Content-Type": "application/json"},
        response_filter=lambda resp: resp.json(),
        log_response=True,
    )

    validate_plan = PythonOperator(
        task_id="validate_training_plan",
        python_callable=validate_training_plan,
        provide_context=True,
        retries=0,
    )

    classify_games = PythonOperator(
        task_id="classify_eligible_game_modules",
        python_callable=run_game_module_stage,
        op_kwargs={"stage": "classification"},
        provide_context=True,
        retries=0,
    )

    anomaly_games = PythonOperator(
        task_id="anomaly_eligible_game_modules",
        python_callable=run_game_module_stage,
        op_kwargs={"stage": "anomaly"},
        provide_context=True,
        retries=0,
    )

    register_games = PythonOperator(
        task_id="register_eligible_game_modules",
        python_callable=run_game_module_stage,
        op_kwargs={"stage": "registration"},
        provide_context=True,
        retries=0,
    )

    submit_cross = HttpOperator(
        task_id="submit_cross_game",
        http_conn_id=NEUROGAMES_HTTP_CONN_ID,
        endpoint="/pipeline/cross",
        method="POST",
        response_check=lambda resp: resp.json().get("status") in ("accepted", "already_running"),
        response_filter=lambda resp: resp.text,
        log_response=True,
        execution_timeout=timedelta(minutes=2),
        retries=0,
        trigger_rule=TriggerRule.ALL_SUCCESS,
    )

    extract_cross = PythonOperator(
        task_id="extract_cross_task_id",
        python_callable=extract_task_id,
        op_kwargs={"response_text": "{{ ti.xcom_pull(task_ids='submit_cross_game') }}"},
    )

    poll_cross = HttpSensor(
        task_id="poll_cross_game",
        http_conn_id=NEUROGAMES_HTTP_CONN_ID,
        endpoint="/pipeline/status/{{ ti.xcom_pull(task_ids='extract_cross_task_id') }}",
        method="GET",
        extra_options={"check_response": False},
        response_check=task_succeeded,
        poke_interval=10,
        timeout=3600,
        mode="reschedule",
    )

    prevent_duplicate_run >> resolve_config >> build_plan >> validate_plan
    validate_plan >> anomaly_games >> classify_games >> register_games
    register_games >> submit_cross >> extract_cross >> poll_cross

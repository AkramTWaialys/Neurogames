"""
Pipeline Webhook Routes — Airflow → FastAPI Integration
==========================================================
Endpoints called by the three Airflow DAGs.

All heavy endpoints (classify, register, cross, drift-retrain, report/smart)
run in background processes via the task worker. They return immediately
with a task_id that Airflow polls via GET /pipeline/status/{task_id}.

  Gate-checks (HttpSensor):
    GET  /ready/training    – ≥ 10 pending sessions in any game
    GET  /ready/drift       – ≥ 30 trained sessions in any game
    GET  /ready/reporting   – ≥ 1 participant with data

  Pipeline actions (submit + poll):
    POST /pipeline/classify/{game}      – train classification + MLflow
    POST /pipeline/register_all         – register & promote models
    POST /pipeline/cross                – cross-game analysis
    POST /pipeline/drift-retrain/all    – drift → retrain → champion
    POST /pipeline/report/smart         – smart reports (≥10 new sessions)

  Status polling:
    GET  /pipeline/status/{task_id}     – poll background task progress
    GET  /pipeline/tasks                – list recent tasks
"""

from __future__ import annotations

import os
import time
import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException

from .config import TRIGGER_THRESHOLD
from .db import (
    count_all_untrained,
    count_game_sessions,
    list_participant_ids,
    count_participant_sessions,
    load_drift_history,
    load_game_df,
    list_game_modules,
    list_ml_enabled_game_ids,
)
from .worker import task_manager
from .metrics import (
    drift_score,
    drift_features_count,
    drift_detected as drift_detected_gauge,
    drift_per_feature,
    drift_type_detected,
    pipeline_duration,
    retrain_total,
    promotion_total,
    reports_generated,
    pending_sessions,
    total_sessions_trained,
)

router = APIRouter(tags=["pipeline"])


# ── Gate-Check Endpoints ─────────────────────────────────────────────────────
# The Airflow HttpSensor checks these: response.json().get("ready") is True


@router.get("/ready/training")
async def ready_training(threshold: int = None):
    """
    Gate for the training DAG.
    Returns ready=True if AT LEAST ONE game has >= threshold new (pending) sessions.
    This fires the DAG as soon as any game is ready; per-game eligibility is then
    enforced inside build_training_plan.
    """
    from .config import TRIGGER_THRESHOLD
    ml_games = list_ml_enabled_game_ids()
    pending_all = count_all_untrained(ml_eligible_only=True)
    pending = {game: pending_all.get(game, 0) for game in ml_games}
    effective_threshold = threshold if threshold is not None else TRIGGER_THRESHOLD

    total_pending = sum(pending.values())
    # Ready if any single game has enough new sessions to justify a training run
    ready = any(v >= effective_threshold for v in pending.values())

    return {
        "ready": ready,
        "threshold": effective_threshold,
        "total_pending": total_pending,
        "pending_per_game": pending,
        "eligible_games": [g for g, v in pending.items() if v >= effective_threshold],
        "ml_enabled_games": ml_games,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


from pydantic import BaseModel
from typing import List, Optional

class TrainingPlanRequest(BaseModel):
    threshold: Optional[int] = None
    force: bool = False
    games: Optional[List[str]] = None
    trigger_reason: str = "volume"
    drift_run_id: Optional[str] = None


@router.post("/pipeline/training-plan")
async def build_training_plan(req: TrainingPlanRequest):
    """
    Returns a training plan: which games are eligible, which are skipped,
    and initial data-quality results.

    Eligibility is based on TOTAL session count (old + new), not just pending
    sessions.  The readiness gate (/ready/training) already ensures the pipeline
    only fires when there is new data somewhere; here we want every game that
    has ANY data to participate, because the classifier always trains on ALL
    sessions regardless of their training_runs status.
    """
    threshold = req.threshold
    force = req.force
    games = req.games
    trigger_reason = req.trigger_reason
    drift_run_id = req.drift_run_id
    from .config import TRIGGER_THRESHOLD
    ml_games = list_ml_enabled_game_ids()
    pending_all = count_all_untrained(ml_eligible_only=True)
    pending = {game: pending_all.get(game, 0) for game in ml_games}
    total_pending = sum(pending.values())
    effective_threshold = threshold if threshold is not None else TRIGGER_THRESHOLD

    # Determine games interested in training
    target_games = games if games else ml_games
    eligible_games = []
    skipped_games = []
    dq_results = {}

    # A game is eligible if it has >= threshold NEW (pending) sessions, or force is True.
    # When eligible, the classifier trains on ALL data (old + new) — the threshold
    # only decides whether a retraining run is warranted, not what data is used.
    for game in target_games:
        n_pending = pending.get(game, 0)
        n_total = count_game_sessions(game, ml_eligible_only=True)

        # Skip if not enough new data and not forced
        if n_pending < effective_threshold and not force:
            skipped_games.append({
                "game": game,
                "reason": "insufficient_new_data",
                "n_pending": n_pending,
                "n_total": n_total,
                "threshold": effective_threshold,
            })
            continue

        # Data Quality Check (Hard Gate)
        try:
            from multigame_pipeline.data_quality import run_data_quality_checks

            dq_report = run_data_quality_checks(load_game_df(game), game)
            dq_results[game] = {
                "passed": dq_report.passed,
                "warnings": dq_report.warnings,
                "failures": dq_report.failures,
                "stats": dq_report.stats,
                "n_pending": n_pending,
                "n_total": n_total,
            }
        except Exception as exc:
            dq_results[game] = {
                "passed": False,
                "warnings": [],
                "failures": [f"data_quality_error: {exc}"],
                "stats": {},
                "n_pending": n_pending,
                "n_total": n_total,
            }

        if not dq_results[game]["passed"]:
            skipped_games.append({
                "game": game,
                "reason": "data_quality_failed",
                "n_pending": n_pending,
                "n_total": n_total,
                "failures": dq_results[game]["failures"],
            })
            continue

        eligible_games.append(game)

    # Readiness check (for non-forced, non-drift runs)
    is_ready = force or (total_pending >= effective_threshold)

    return {
        "ready": is_ready and len(eligible_games) > 0,
        "total_pending": total_pending,
        "threshold": effective_threshold,
        "trigger_reason": trigger_reason,
        "eligible_games": eligible_games,
        "skipped_games": skipped_games,
        "dq_results": dq_results,
        "drift_run_id": drift_run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready/drift")
async def ready_drift():
    """
    Gate for the drift monitoring DAG.
    Returns ready=True if ANY game has ≥ 30 trained baseline sessions.
    """
    threshold = 30
    game_counts = {}
    ready_games = []
    for game in list_ml_enabled_game_ids():
        n = count_game_sessions(game, trained_only=True)
        game_counts[game] = n
        if n >= threshold:
            ready_games.append(game)

    return {
        "ready": len(ready_games) > 0,
        "threshold": threshold,
        "trained_per_game": game_counts,
        "ready_games": ready_games,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready/reporting")
async def ready_reporting():
    """
    Gate for the reporting DAG.
    Returns ready=True if at least 1 participant has data.
    """
    pids = list_participant_ids()
    return {
        "ready": len(pids) > 0,
        "n_participants": len(pids),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Pipeline Action Endpoints ────────────────────────────────────────────────


# ── Background Task Status Endpoints ─────────────────────────────────────────


@router.get("/pipeline/status/{task_id}")
async def pipeline_task_status(task_id: str):
    """Poll the status of a background pipeline task."""
    status = task_manager.get_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    # Map worker states to Airflow-compatible responses
    if status["state"] == "success":
        return {"status": "ok", **status}
    elif status["state"] == "failed":
        return {"status": "error", **status}
    else:
        return {"status": "running", **status}


@router.get("/pipeline/tasks")
async def pipeline_list_tasks():
    """List all recent background tasks."""
    return {
        "tasks": task_manager.list_tasks(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Pipeline Action Endpoints (non-blocking) ─────────────────────────────────


@router.post("/pipeline/classify/{game}")
async def pipeline_classify(game: str, context: dict = Body(None)):
    """
    Submit classification pipeline for a single game.
    Accepts optional context for trigger reason and threshold.
    """
    valid_games = list_ml_enabled_game_ids()
    if game not in valid_games:
        raise HTTPException(
            status_code=400,
            detail=f"Game module '{game}' is not ML-enabled. Valid: {valid_games}",
        )

    from .tasks import run_classify_task
    task_id, created = task_manager.get_or_submit(
        f"classify_{game}",
        run_classify_task,
        args=(game, context),
    )
    if not created:
        return {
            "status": "already_running",
            "task_id": task_id,
            "game": game,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "status": "accepted",
        "task_id": task_id,
        "game": game,
        "poll_url": f"/pipeline/status/{task_id}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/pipeline/register_all")
async def pipeline_register_all():
    """
    Submit model registration for ALL games as a background task.
    """
    from .tasks import run_register_task
    task_id, created = task_manager.get_or_submit("register_all", run_register_task)
    if not created:
        return {"status": "already_running", "task_id": task_id}

    return {
        "status": "accepted",
        "task_id": task_id,
        "poll_url": f"/pipeline/status/{task_id}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/pipeline/register/{game}")
async def pipeline_register_game(game: str, context: dict = Body(None)):
    """
    Submit model registration and promotion for a single game.
    Accepts optional {"run_id": "..."} to register the current DAG cycle only.
    """
    valid_games = list_ml_enabled_game_ids()
    if game not in valid_games:
        raise HTTPException(
            status_code=400,
            detail=f"Game module '{game}' is not ML-enabled. Valid: {valid_games}",
        )

    from .tasks import run_single_register_task
    context = context or {}
    run_id = context.get("run_id")
    task_name = f"register_{game}_{run_id}" if run_id else f"register_{game}"
    task_id, created = task_manager.get_or_submit(
        task_name,
        run_single_register_task,
        args=(game, run_id),
    )
    if not created:
        return {"status": "already_running", "task_id": task_id}

    return {
        "status": "accepted",
        "task_id": task_id,
        "game": game,
        "poll_url": f"/pipeline/status/{task_id}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/pipeline/cross")
async def pipeline_cross():
    """
    Submit cross-game analysis as a background task.
    """
    from .tasks import run_cross_task
    task_id, created = task_manager.get_or_submit("cross_game", run_cross_task)
    if not created:
        return {"status": "already_running", "task_id": task_id}

    return {
        "status": "accepted",
        "task_id": task_id,
        "poll_url": f"/pipeline/status/{task_id}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/pipeline/anomaly/{game}")
async def pipeline_anomaly(game: str):
    """
    Submit anomaly detection for a single game as a background task.
    Returns immediately with a task_id for polling.
    """
    valid_games = list_ml_enabled_game_ids()
    if game not in valid_games:
        raise HTTPException(
            status_code=400,
            detail=f"Game module '{game}' is not ML-enabled. Valid: {valid_games}",
        )

    from .tasks import run_anomaly_task
    task_id, created = task_manager.get_or_submit(
        f"anomaly_{game}",
        run_anomaly_task,
        args=(game,),
    )
    if not created:
        return {
            "status": "already_running",
            "task_id": task_id,
            "game": game,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "status": "accepted",
        "task_id": task_id,
        "game": game,
        "poll_url": f"/pipeline/status/{task_id}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/pipeline/drift-check/all")
async def pipeline_drift_check():
    """
    Submit drift-only monitoring as a background task.
    Returns drifted games to be handled by Airflow.
    """
    from .tasks import run_drift_check_task
    task_id, created = task_manager.get_or_submit("drift_check", run_drift_check_task)
    if not created:
        return {"status": "already_running", "task_id": task_id}

    return {
        "status": "accepted",
        "task_id": task_id,
        "poll_url": f"/pipeline/status/{task_id}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/pipeline/drift-retrain/all")
async def pipeline_drift_retrain_deprecated():
    """
    [DEPRECATED] Use /pipeline/drift-check/all and trigger training via Airflow.
    """
    raise HTTPException(
        status_code=410,
        detail="This endpoint is deprecated. Use /pipeline/drift-check/all instead."
    )


@router.post("/pipeline/report/smart")
async def pipeline_report_smart():
    """
    Submit smart report generation as a background task.
    """
    from .tasks import run_smart_reports_task
    task_id, created = task_manager.get_or_submit("smart_reports", run_smart_reports_task)
    if not created:
        return {"status": "already_running", "task_id": task_id}

    return {
        "status": "accepted",
        "task_id": task_id,
        "poll_url": f"/pipeline/status/{task_id}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Drift History Endpoint ────────────────────────────────────────────────────


@router.get("/drift/history/{game}")
async def get_drift_history(
    game: str,
    drift_type: str = None,
    feature: str = None,
    limit: int = 100,
):
    """
    Return per-feature drift history for a game.

    Query params:
      - drift_type: filter by type ('data', 'prediction', 'label_dist', 'concept')
      - feature:    filter by feature name
      - limit:      max rows to return (default 100)
    """
    valid_games = [g["game_id"] for g in list_game_modules(active_only=True)]
    if game not in valid_games:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown game module '{game}'. Valid: {valid_games}",
        )
    rows = load_drift_history(game, drift_type=drift_type, feature=feature, limit=limit)
    return {
        "game": game,
        "drift_type": drift_type,
        "n_rows": len(rows),
        "rows": rows,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _update_drift_gauges_from_result(result: dict) -> None:
    """
    Set Prometheus drift gauges from a completed drift task result.
    Called only from the API process — never from workers.
    """
    game = result.get("game")
    if not game:
        return

    for f in result.get("per_feature", []):
        fname = f.get("feature")
        pval = f.get("p_value")
        if fname is not None and pval is not None:
            drift_per_feature.labels(game=game, feature=fname).set(pval)

    for dtype in ("data", "prediction", "label_dist", "concept"):
        key = f"{dtype}_drift_detected"
        if key in result:
            drift_type_detected.labels(game=game, drift_type=dtype).set(
                1 if result[key] else 0
            )

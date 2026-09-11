"""
Pipeline Task Functions (standalone, picklable)
==================================================
These functions encapsulate the heavy ML work and are designed
to be called from the background worker (ProcessPoolExecutor).

They must be top-level module functions (not lambdas, not methods)
so they can be pickled and sent to worker processes.
"""

import os
import time
import logging
import joblib

log = logging.getLogger("mlops.tasks")


def run_classify_task(game: str, context: dict = None) -> dict:
    """
    Airflow-facing task to run classification for a single game.
    Supports optional context (trigger_reason, threshold, etc.) for MLflow tagging.
    """
    return _run_classify_game_internal(game, context)


def _run_classify_game_internal(game: str, context: dict = None) -> dict:
    """
    Internal shared logic for classification + MLflow logging.
    Used by both standard and drift-triggered training runs.
    """
    from multigame_pipeline.classification.classifier import run_classification
    from mlops.tracking import mlflow_run, log_classification_run
    from mlops.config import MLOPS_OUTPUTS_DIR
    from mlops.db import list_game_session_ids, list_untrained_session_ids, mark_trained
    from mlops.tracing import init_tracing, trace_span

    init_tracing(service_name="neurogames-worker")
    context = context or {}
    trigger_reason = context.get("trigger_reason", "volume")

    outputs_dir = os.path.join(MLOPS_OUTPUTS_DIR, game)
    os.makedirs(outputs_dir, exist_ok=True)

    untrained_session_ids = list_untrained_session_ids(game, ml_eligible_only=True)
    training_session_ids = list_game_session_ids(game, ml_eligible_only=True)
    t0 = time.time()

    # MLflow tags to include in the run
    extra_tags = {
        "trigger_reason": trigger_reason,
        "training_threshold": str(context.get("threshold", "N/A")),
        "pending_sessions_at_trigger": str(len(untrained_session_ids)),
    }
    if context.get("drift_run_id"):
        extra_tags["drift_run_id"] = str(context["drift_run_id"])

    with trace_span("classify_pipeline", {"game": game, "n_sessions": len(training_session_ids)}):
        with mlflow_run(game, "classification", extra_tags=extra_tags) as run_ctx:
            # Data-quality is handled internally in run_classification (passes/fails)
            result = run_classification(game, verbose=True)

            if result.get("status") == "data_quality_failed":
                # In the new plan, we check DQ before submitting, but we keep this gate for safety.
                raise RuntimeError(f"Data quality failed for {game}: {result.get('dq_report')}")

            if result.get("status") == "insufficient_class_members":
                log.warning("[classify][%s] Skipped — %s", game, result.get("detail", "too few samples"))
                if untrained_session_ids:
                    mark_trained(game, untrained_session_ids, run_id=run_ctx.run_id)
                elapsed = round(time.time() - t0, 1)
                return {
                    "status": "skipped",
                    "game": game,
                    "reason": result.get("detail", "insufficient_class_members"),
                    "class_counts": result.get("class_counts"),
                    "run_id": run_ctx.run_id,
                    "elapsed_seconds": elapsed,
                }

            log_classification_run(run_ctx, game, result, outputs_dir)

            # Log sklearn model to MLflow
            try:
                model_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "multigame_pipeline", "outputs", game,
                    "classifier_ensemble.pkl",
                )
                if os.path.exists(model_path):
                    import joblib
                    bundle = joblib.load(model_path)
                    if "model" in bundle:
                        run_ctx.log_model(bundle["model"])
            except Exception:
                pass

            if untrained_session_ids:
                mark_trained(game, untrained_session_ids, run_id=run_ctx.run_id)

    elapsed = round(time.time() - t0, 1)
    return {
        "status": "ok",
        "game": game,
        "trigger_reason": trigger_reason,
        "new_sessions_trained": len(untrained_session_ids),
        "sessions_in_training_run": len(training_session_ids),
        "raw_participants": result.get("raw_participants"),
        "labeled_participants": result.get("labeled_participants"),
        "participant_aggregate_examples": result.get("participant_aggregate_examples"),
        "evaluation_train_examples": result.get("evaluation_train_examples"),
        "evaluation_test_examples": result.get("evaluation_test_examples"),
        "selected_model_name": result.get("selected_model_name"),
        "selected_model_cv_acc": result.get("selected_model_cv_acc"),
        "selected_model_cv_std": result.get("selected_model_cv_std"),
        "selected_model_cv_f1_macro": result.get("selected_model_cv_f1_macro"),
        "selected_model_cv_f1_macro_std": result.get("selected_model_cv_f1_macro_std"),
        "selected_f1_macro": result.get("selected_f1_macro"),
        "ensemble_test_acc": result.get("ensemble_test_acc", 0),
        "cv_results": {
            k: round(v, 4) for k, v in result.get("cv_results", {}).items()
        },
        "candidate_metrics": result.get("candidate_metrics", {}),
        "baseline_metrics": result.get("baseline_metrics", {}),
        "separability_diagnostic": result.get("separability_diagnostic", {}),
        "classification_model_comparison_png": result.get("classification_model_comparison_png"),
        "classification_model_comparison_csv": result.get("classification_model_comparison_csv"),
        "classification_model_comparison_svg": result.get("classification_model_comparison_svg"),
        "classification_model_selection_flow_png": result.get("classification_model_selection_flow_png"),
        "classification_model_selection_flow_svg": result.get("classification_model_selection_flow_svg"),
        "presentation_model_evaluation_png": result.get("presentation_model_evaluation_png"),
        "classification_model_card_png": result.get("classification_model_card_png"),
        "classification_report_manifest_md": result.get("classification_report_manifest_md"),
        "run_id": run_ctx.run_id,
        "elapsed_seconds": elapsed,
    }


def run_register_task() -> dict:
    """Register best models for all games, with champion/challenger validation."""
    from mlops.db import list_ml_enabled_game_ids
    results = []
    for game in list_ml_enabled_game_ids():
        results.append(run_single_register_task(game))
    return {"status": "ok", "results": results}


def run_single_register_task(game: str, run_id: str | None = None) -> dict:
    """Register and promote best model for a single game."""
    from mlops.registry import register_best_model, promote_to_production, validate_challenger
    from mlops.tracing import init_tracing, trace_span

    init_tracing(service_name="neurogames-worker")

    with trace_span(f"register_model_{game}"):
        reg = register_best_model(game, run_id=run_id)
        promo = {}
        validation = {}
        if reg.get("status") == "registered":
            challenger_acc = reg.get("metric", 0)
            validation = validate_challenger(
                game,
                challenger_acc,
                challenger_run_id=reg.get("run_id"),
            )
            if validation.get("promoted", False):
                promo = promote_to_production(game, version=reg.get("version"))
                log.info("[%s] PROMOTED: %s", game, validation.get("reason", ""))
            else:
                log.info("[%s] BLOCKED: %s", game, validation.get("reason", ""))

    return {
        "status": "ok",
        "game": game,
        "registration": reg,
        "validation": validation,
        "promotion": promo,
    }


def run_cross_task() -> dict:
    """Run cross-game analysis."""
    from multigame_pipeline.cross_game.cross_game_analysis import run_cross_game_analysis
    from mlops.tracking import mlflow_run
    from mlops.tracing import init_tracing, trace_span

    init_tracing(service_name="neurogames-worker")

    with trace_span("cross_game_analysis"):
        with mlflow_run("all", "cross_game") as run_ctx:
            result = run_cross_game_analysis(verbose=True)
            run_ctx.log_metric("majority_vote_acc", result.get("majority_vote_acc", 0))

    return {
        "status": "ok",
        "majority_vote_acc": result.get("majority_vote_acc", 0),
        "run_id": run_ctx.run_id,
    }


def run_anomaly_task(game: str) -> dict:
    """Run anomaly detection for a single game and persist flags to the DB."""
    from multigame_pipeline.anomaly.anomaly_detector import run_anomaly_detection
    from mlops.tracking import mlflow_run, log_anomaly_run
    from mlops.config import MLOPS_OUTPUTS_DIR
    from mlops.tracing import init_tracing, trace_span
    from mlops.web_service import refresh_dashboard_cache

    init_tracing(service_name="neurogames-worker")

    outputs_dir = os.path.join(MLOPS_OUTPUTS_DIR, game)
    os.makedirs(outputs_dir, exist_ok=True)

    t0 = time.time()
    with trace_span("anomaly_pipeline", {"game": game}):
        with mlflow_run(game, "anomaly") as run_ctx:
            result = run_anomaly_detection(game, verbose=True)
            log_anomaly_run(run_ctx, game, result, outputs_dir)

    # Refresh the admin dashboard cache so anomaly alerts appear
    try:
        refresh_dashboard_cache()
    except Exception as e:
        log.warning("[anomaly][%s] Could not refresh dashboard cache: %s", game, e)

    elapsed = round(time.time() - t0, 1)
    return {
        "status": "ok",
        "game": game,
        "n_flagged": result.get("n_flagged", 0),
        "run_id": run_ctx.run_id,
        "elapsed_seconds": elapsed,
    }


def run_drift_check_task() -> dict:
    """
    Monitoring-only drift task.
    Identifies drifted games but does NOT train or promote.
    Called by neurogames_drift_monitoring DAG.
    """
    from mlops.drift_monitor import (
        check_all_games,
        _load_drift_windows,
        run_prediction_drift,
        run_label_distribution_drift,
        run_concept_drift,
    )
    from mlops.inference import predict
    from mlops.db import (
        insert_drift_check,
        insert_drift_history_batch,
        list_game_session_ids,
    )
    from mlops.tracing import init_tracing, trace_span
    import pandas as pd

    init_tracing(service_name="neurogames-worker")

    drift_results = []
    drifted_games = []

    with trace_span("drift_check_only"):
        raw_results = check_all_games()

    drift_run_id = f"drift_check_{int(time.time())}"

    for dr in raw_results:
        game = dr.get("game", "unknown")

        # 1. Record drift check
        try:
            checked_session_ids = list_game_session_ids(game)
            insert_drift_check(
                game=game,
                run_id=drift_run_id,
                session_ids=checked_session_ids,
                n_sessions=len(checked_session_ids),
                drift_detected=dr.get("drift_detected", False),
                drift_share=dr.get("drift_share", 0.0),
                status=dr.get("status", "completed"),
            )
        except Exception as e:
            log.warning("[drift][%s] Could not record drift check: %s", game, e)

        # 2. Persist data drift history
        per_feature = dr.get("per_feature", [])
        if per_feature:
            history_rows = [
                {
                    "game": game,
                    "feature": f.get("feature", "unknown"),
                    "drift_type": "data",
                    "drifted": int(f.get("drifted", False)),
                    "stat_name": f.get("statistic"),
                    "p_value": f.get("p_value"),
                    "effect_size": None,
                    "run_id": drift_run_id,
                }
                for f in per_feature
            ]
            insert_drift_history_batch(history_rows)

        # 3. Advanced drift checks
        try:
            baseline_df, current_df = _load_drift_windows(game)
            baseline_result = predict(game, baseline_df) if not baseline_df.empty else None
            current_result = predict(game, current_df) if not current_df.empty else None

            adv_history_rows = []

            if baseline_result and current_result:
                # Prediction drift
                pred_drift = run_prediction_drift(
                    game,
                    baseline_result["probabilities"],
                    current_result["probabilities"],
                )
                dr["prediction_drift_detected"] = pred_drift.get("drift_detected", False)
                for pc in pred_drift.get("per_class", []):
                    adv_history_rows.append({
                        "game": game,
                        "feature": f"class_{pc['class_idx']}_proba",
                        "drift_type": "prediction",
                        "drifted": int(pc.get("drifted", False)),
                        "stat_name": "ks_stat",
                        "p_value": pc.get("p_value"),
                        "run_id": drift_run_id,
                    })

                # Label distribution drift
                label_drift = run_label_distribution_drift(
                    game,
                    pd.Series(baseline_result["labels"]),
                    pd.Series(current_result["labels"]),
                )
                dr["label_dist_drift_detected"] = label_drift.get("drift_detected", False)
                adv_history_rows.append({
                    "game": game,
                    "feature": "label_distribution",
                    "drift_type": "label_dist",
                    "drifted": int(label_drift.get("drift_detected", False)),
                    "stat_name": "chi2_stat",
                    "p_value": label_drift.get("p_value"),
                    "run_id": drift_run_id,
                })

                # Concept drift
                b_agg = baseline_result["agg_df"]
                c_agg = current_result["agg_df"]
                class_names = baseline_result["class_names"]
                name_to_idx = {n: i for i, n in enumerate(class_names)}

                if "cluster" in b_agg.columns and "cluster" in c_agg.columns:
                    baseline_true = b_agg["cluster"].map(name_to_idx).values
                    current_true = c_agg["cluster"].map(name_to_idx).values
                    concept_drift = run_concept_drift(
                        game,
                        baseline_result["predictions"], baseline_true,
                        current_result["predictions"], current_true,
                    )
                    dr["concept_drift_detected"] = concept_drift.get("drift_detected", False)
                    adv_history_rows.append({
                        "game": game,
                        "feature": "model_accuracy",
                        "drift_type": "concept",
                        "drifted": int(concept_drift.get("drift_detected", False)),
                        "stat_name": "acc_drop",
                        "p_value": concept_drift.get("p_value"),
                        "run_id": drift_run_id,
                    })

                if adv_history_rows:
                    insert_drift_history_batch(adv_history_rows)

        except Exception as e:
            log.warning("[drift][%s] Advanced drift checks failed: %s", game, e)

        # Check if any type of drift requires retraining
        is_drifted = (
            dr.get("drift_detected", False)
            or dr.get("prediction_drift_detected", False)
            or dr.get("concept_drift_detected", False)
        )
        if is_drifted:
            drifted_games.append(game)

        drift_results.append({
            "game": game,
            "drift_detected": is_drifted,
            "metrics": {
                "feature_drift_share": dr.get("drift_share", 0.0),
                "prediction_drift": dr.get("prediction_drift_detected", False),
                "concept_drift": dr.get("concept_drift_detected", False),
            }
        })

    return {
        "status": "ok",
        "drift_run_id": drift_run_id,
        "drifted_games": drifted_games,
        "results": drift_results,
    }


def run_single_report_task(pid: str, locale: str = "en") -> dict:
    """Generate a report for a single participant (on-demand)."""
    from mlops.reporter import generate_report, normalize_report_locale
    from mlops.db import insert_report_run
    from mlops.tracing import init_tracing, trace_span

    init_tracing(service_name="neurogames-worker")

    with trace_span("generate_single_report", {"participant_id": pid}):
        locale = normalize_report_locale(locale)
        result = generate_report(pid, compare=True, locale=locale)

    # Record this report generation in the tracking table
    if result.get("counts_as_new_report"):
        try:
            insert_report_run(
                participant_id=pid,
                report_path=result.get("report_path"),
                report_group_id=result.get("report_group_id"),
                locale=result.get("locale"),
                snapshot_until=result.get("snapshot_until"),
                session_count_at_snapshot=result.get("session_count_at_snapshot"),
                is_canonical=True,
            )
        except Exception as e:
            log.warning("[report][%s] Could not record report run: %s", pid, e)

    return {
        "status": "ok",
        "pid": pid,
        "locale": result.get("locale"),
        "report_group_id": result.get("report_group_id"),
        "snapshot_until": result.get("snapshot_until"),
        "method": result.get("method"),
        "report_path": result.get("report_path"),
        "validation": result.get("validation", {"valid": True, "issues": []}),
    }


def run_smart_reports_task() -> dict:
    """Generate reports for participants with enough new sessions."""
    from mlops.reporter import generate_report, find_latest_report
    from mlops.db import (
        list_participant_ids,
        count_participant_sessions,
        count_sessions_since,
        get_latest_report_run,
        insert_report_run,
    )
    from mlops.tracing import init_tracing, trace_span

    init_tracing(service_name="neurogames-worker")

    with trace_span("smart_reports_batch"):
        threshold = 20
        pids = list_participant_ids()
        generated = []
        skipped = []

        for pid in pids:
            n_total = count_participant_sessions(pid)
            if n_total < threshold:
                skipped.append({"pid": pid, "reason": "not_enough_sessions", "n": n_total})
                continue

            n_since = n_total
            try:
                latest_run = get_latest_report_run(pid)
                if latest_run and latest_run.get("generated_at"):
                    n_since = count_sessions_since(pid, latest_run["generated_at"])
                else:
                    cached = find_latest_report(pid)
                    if cached and cached.get("generated_at"):
                        n_since = count_sessions_since(pid, cached["generated_at"])
            except Exception:
                pass

            if n_since < threshold:
                skipped.append({"pid": pid, "reason": "not_enough_new", "n_since": n_since})
                continue

            try:
                result = generate_report(pid)
                # Record this report generation in the tracking table
                if result.get("counts_as_new_report"):
                    try:
                        insert_report_run(
                            participant_id=pid,
                            report_path=result.get("report_path"),
                            report_group_id=result.get("report_group_id"),
                            locale=result.get("locale"),
                            snapshot_until=result.get("snapshot_until"),
                            session_count_at_snapshot=result.get("session_count_at_snapshot"),
                            is_canonical=True,
                        )
                    except Exception as rec_err:
                        log.warning("[report][%s] Could not record report run: %s", pid, rec_err)
                generated.append({
                    "pid": pid,
                    "locale": result.get("locale"),
                    "method": result.get("method", "unknown"),
                    "report_path": result.get("report_path"),
                })
            except Exception as e:
                skipped.append({"pid": pid, "reason": f"error: {str(e)}"})

    return {
        "status": "ok",
        "generated": len(generated),
        "skipped": len(skipped),
        "details": generated,
        "skipped_details": skipped[:20],
    }

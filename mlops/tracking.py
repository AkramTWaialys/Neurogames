"""
MLOps Experiment Tracking (MLflow)
====================================
Provides helpers to wrap pipeline modules with MLflow logging.

Usage:
    from mlops.tracking import setup_mlflow, mlflow_run

    setup_mlflow("NeuroGames-Classification")
    with mlflow_run("gonogo", "classification") as run:
        result = run_classification("gonogo")
        log_classification_metrics(result)
"""

import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import mlflow
    import mlflow.sklearn
    HAS_MLFLOW = True
except ImportError:
    HAS_MLFLOW = False

from mlops.config import MLFLOW_TRACKING_URI, MLFLOW_EXPERIMENT_PREFIX


def _safe_mlflow_key(value: str) -> str:
    """Return a compact metric/param key fragment accepted by MLflow."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip().lower())
    return safe.strip("_") or "unknown"


def setup_mlflow(experiment_name: str = None):
    """
    Initialize MLflow tracking.
    Sets the tracking URI and creates/selects the experiment.
    """
    if not HAS_MLFLOW:
        print("  [WARN] MLflow not installed. Tracking disabled.")
        return None

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

    if experiment_name is None:
        experiment_name = MLFLOW_EXPERIMENT_PREFIX

    mlflow.set_experiment(experiment_name)
    return experiment_name


class mlflow_run:
    """
    Context manager that wraps a pipeline module call in an MLflow run.

    Usage:
        with mlflow_run("gonogo", "classification") as run:
            # run your module code
            pass
    """

    def __init__(self, game_name: str, module_name: str, extra_tags: dict = None):
        self.game_name = game_name
        self.module_name = module_name
        self.extra_tags = extra_tags or {}
        self.run = None

    def __enter__(self):
        if not HAS_MLFLOW:
            return self

        experiment_name = f"{MLFLOW_EXPERIMENT_PREFIX}/{self.game_name}/{self.module_name}"
        setup_mlflow(experiment_name)

        tags = {
            "game": self.game_name,
            "game_id": self.game_name,
            "module": self.module_name,
        }
        tags.update(self.extra_tags)

        self.run = mlflow.start_run(
            run_name=f"{self.game_name}_{self.module_name}",
            tags=tags,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if HAS_MLFLOW and self.run:
            if exc_type is not None:
                mlflow.set_tag("status", "FAILED")
                mlflow.set_tag("error", str(exc_val)[:200])
            else:
                mlflow.set_tag("status", "SUCCESS")
            mlflow.end_run()
        return False

    def log_param(self, key, value):
        if HAS_MLFLOW:
            mlflow.log_param(key, value)

    def log_params(self, params: dict):
        if HAS_MLFLOW:
            mlflow.log_params(params)

    def log_metric(self, key, value, step=None):
        if HAS_MLFLOW:
            mlflow.log_metric(key, value, step=step)

    def log_metrics(self, metrics: dict, step=None):
        if HAS_MLFLOW:
            mlflow.log_metrics(metrics, step=step)

    def set_tag(self, key, value):
        if HAS_MLFLOW:
            mlflow.set_tag(key, value)

    def set_tags(self, tags: dict):
        if HAS_MLFLOW:
            for key, value in tags.items():
                mlflow.set_tag(key, value)

    def set_run_name(self, run_name: str):
        if HAS_MLFLOW:
            mlflow.set_tag("mlflow.runName", run_name)

    def log_artifact(self, local_path):
        if HAS_MLFLOW and os.path.exists(local_path):
            mlflow.log_artifact(local_path)

    def log_model(self, model, artifact_path="model"):
        if HAS_MLFLOW:
            mlflow.sklearn.log_model(model, artifact_path)

    @property
    def run_id(self):
        if self.run:
            return self.run.info.run_id
        return None


# ── Per-module logging helpers ────────────────────────────────────────────────

def log_data_quality(run_ctx: mlflow_run, dq_report: dict):
    """Log data quality check results as MLflow tags and metrics."""
    if not dq_report:
        return
    run_ctx.log_param("dq_passed", dq_report.get("passed", False))
    run_ctx.log_metric("dq_n_warnings", len(dq_report.get("warnings", [])))
    run_ctx.log_metric("dq_n_failures", len(dq_report.get("failures", [])))
    if HAS_MLFLOW:
        import mlflow
        mlflow.set_tag("dq_status", "PASS" if dq_report.get("passed") else "FAIL")


def log_classification_run(run_ctx: mlflow_run, game_name: str, result: dict, outputs_dir: str):
    """Log a classification module run to MLflow."""
    selected_model = result.get("selected_model_name", "unknown")
    selected_acc = result.get("ensemble_test_acc") or 0
    run_display_name = (
        f"{game_name} | classification | {selected_model} | "
        f"acc={selected_acc:.4f}"
    )
    registered_model_name = f"neurogames-{game_name}-classification"

    run_ctx.set_run_name(run_display_name)
    run_ctx.set_tags({
        "selected_model_name": selected_model,
        "model_family": selected_model,
        "registered_model_name": registered_model_name,
        "model_display_name": f"{game_name}/{selected_model}",
    })

    # Params
    run_ctx.log_params({
        "game": game_name,
        "module": "classification",
        "n_classes": len(result.get("class_names", [])),
        "selected_model": selected_model,
        "selected_model_name": selected_model,
        "registered_model_name": registered_model_name,
    })

    # Core metrics
    run_ctx.log_metric("ensemble_test_acc", result.get("ensemble_test_acc", 0))
    if result.get("selected_f1_macro") is not None:
        run_ctx.log_metric("selected_f1_macro", result["selected_f1_macro"])
    if result.get("selected_model_cv_acc") is not None:
        run_ctx.log_metric("selected_model_cv_acc", result["selected_model_cv_acc"])
    if result.get("selected_model_cv_std") is not None:
        run_ctx.log_metric("selected_model_cv_std", result["selected_model_cv_std"])
    if result.get("selected_model_cv_f1_macro") is not None:
        run_ctx.log_metric("selected_model_cv_f1_macro", result["selected_model_cv_f1_macro"])
    if result.get("selected_model_cv_f1_macro_std") is not None:
        run_ctx.log_metric("selected_model_cv_f1_macro_std", result["selected_model_cv_f1_macro_std"])

    candidate_metrics = result.get("candidate_metrics") or {}
    if not candidate_metrics:
        candidate_metrics = {
            model_name: {"cv_accuracy_mean": acc}
            for model_name, acc in result.get("cv_results", {}).items()
        }
    for model_name, metrics in candidate_metrics.items():
        safe_name = _safe_mlflow_key(model_name)
        if metrics.get("cv_accuracy_mean") is not None:
            run_ctx.log_metric(f"candidate_{safe_name}_cv_accuracy", metrics["cv_accuracy_mean"])
        if metrics.get("cv_accuracy_std") is not None:
            run_ctx.log_metric(f"candidate_{safe_name}_cv_accuracy_std", metrics["cv_accuracy_std"])
        if metrics.get("cv_f1_macro_mean") is not None:
            run_ctx.log_metric(f"candidate_{safe_name}_cv_f1_macro", metrics["cv_f1_macro_mean"])
        if metrics.get("cv_f1_macro_std") is not None:
            run_ctx.log_metric(f"candidate_{safe_name}_cv_f1_macro_std", metrics["cv_f1_macro_std"])

    baseline_metrics = result.get("baseline_metrics") or {}
    for baseline_name, metrics in baseline_metrics.items():
        safe_name = _safe_mlflow_key(baseline_name.replace("Baseline: ", "baseline_"))
        if metrics.get("holdout_accuracy") is not None:
            run_ctx.log_metric(f"{safe_name}_holdout_accuracy", metrics["holdout_accuracy"])
        if metrics.get("holdout_f1_macro") is not None:
            run_ctx.log_metric(f"{safe_name}_holdout_f1_macro", metrics["holdout_f1_macro"])

    # Baseline lift (Phase 2)
    if result.get("baseline_lift_f1") is not None:
        run_ctx.log_metric("baseline_lift_f1", result["baseline_lift_f1"])
    separability = result.get("separability_diagnostic") or {}
    run_ctx.log_metric("separability_suspicious", 1.0 if separability.get("suspicious") else 0.0)
    if HAS_MLFLOW:
        import mlflow
        mlflow.set_tag("separability_status", "SUSPICIOUS" if separability.get("suspicious") else "OK")
        if separability.get("message"):
            mlflow.set_tag("separability_message", separability["message"][:250])

    # Calibration (Phase 3)
    if result.get("brier_score") is not None:
        run_ctx.log_metric("brier_score", result["brier_score"])
    if result.get("ece") is not None:
        run_ctx.log_metric("ece", result["ece"])

    # Data quality (Phase 1)
    log_data_quality(run_ctx, result.get("dq_report"))

    # Artifacts
    artifact_dirs = [outputs_dir]
    try:
        from multigame_pipeline.preprocessor import get_game_outputs
        artifact_dirs.append(get_game_outputs(game_name))
    except Exception:
        pass

    artifact_names = [
        "classification_confusion_matrix.png",
        "classification_feature_importance.png",
        "calibration_curve.png",
        "classification_model_comparison.png",
        "classification_model_comparison.csv",
        "classification_model_comparison.svg",
        "classification_model_selection_flow.png",
        "classification_model_selection_flow.svg",
        "classification_model_card.png",
        "classification_report_manifest.md",
    ]
    logged_paths = set()
    for artifact_dir in artifact_dirs:
        for fname in artifact_names:
            fpath = os.path.join(artifact_dir, fname)
            if os.path.exists(fpath) and fpath not in logged_paths:
                run_ctx.log_artifact(fpath)
                logged_paths.add(fpath)


def log_sequence_run(run_ctx: mlflow_run, game_name: str, result: dict, outputs_dir: str):
    """Log a sequence model module run to MLflow."""
    run_ctx.log_params({
        "game": game_name,
        "module": "sequence",
    })

    run_ctx.log_metric("test_acc", result.get("test_acc", 0))

    cv = result.get("cv_results", {})
    for model_name, acc in cv.items():
        safe_name = model_name.lower().replace(" ", "_")
        run_ctx.log_metric(f"{safe_name}_acc", acc)

    for fname in ["sequence_confusion_matrix.png", "sequence_loss_curve.png", "sequence_lstm_curve.png"]:
        fpath = os.path.join(outputs_dir, fname)
        if os.path.exists(fpath):
            run_ctx.log_artifact(fpath)


def log_assessment_run(run_ctx: mlflow_run, game_name: str, result: dict, outputs_dir: str):
    """Log an assessment module run to MLflow."""
    run_ctx.log_params({
        "game": game_name,
        "module": "assessment",
    })

    run_ctx.log_metric("session_test_acc", result.get("session_test_acc", 0))

    for fname in ["assessment_convergence.png", "assessment_early_accuracy.png"]:
        run_ctx.log_artifact(os.path.join(outputs_dir, fname))


def log_anomaly_run(run_ctx: mlflow_run, game_name: str, result: dict, outputs_dir: str,
                    params: dict = None):
    """Log an anomaly detection module run to MLflow."""
    default_params = {
        "game": game_name,
        "module": "anomaly",
    }
    if params:
        default_params.update(params)
    run_ctx.log_params(default_params)

    n_flagged = result.get("n_flagged", 0)
    run_ctx.log_metric("n_flagged", n_flagged)

    flags_df = result.get("flags_df")
    if flags_df is not None and len(flags_df) > 0:
        run_ctx.log_metric("flag_rate", n_flagged / len(flags_df))

    for fname in ["anomaly_score_distribution.png", "anomaly_by_cluster.png"]:
        run_ctx.log_artifact(os.path.join(outputs_dir, fname))


def get_experiment_runs(experiment_name: str = None) -> list:
    """
    Query MLflow for all runs in an experiment.
    Returns a list of dicts with run info for display in the dashboard.
    """
    if not HAS_MLFLOW:
        return []

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

    if experiment_name is None:
        # Get all experiments
        experiments = mlflow.search_experiments()
        all_runs = []
        for exp in experiments:
            if exp.name.startswith(MLFLOW_EXPERIMENT_PREFIX):
                runs = mlflow.search_runs(
                    experiment_ids=[exp.experiment_id],
                    order_by=["start_time DESC"],
                    max_results=50,
                )
                if len(runs) > 0:
                    all_runs.append(runs)

        if not all_runs:
            return []

        import pandas as pd
        combined = pd.concat(all_runs, ignore_index=True)
        if "start_time" in combined.columns:
            combined = combined.sort_values("start_time", ascending=False)
        return combined.to_dict("records")
    else:
        setup_mlflow(experiment_name)
        exp = mlflow.get_experiment_by_name(experiment_name)
        if exp is None:
            return []
        runs = mlflow.search_runs(
            experiment_ids=[exp.experiment_id],
            order_by=["start_time DESC"],
            max_results=50,
        )
        return runs.to_dict("records") if len(runs) > 0 else []

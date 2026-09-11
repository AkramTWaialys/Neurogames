"""
MLOps Model Registry
======================
Helpers to register, promote, and load models from the MLflow Model Registry.

Usage:
    from mlops.registry import register_best_model, load_production_model

    register_best_model("gonogo")
    model = load_production_model("gonogo")
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import mlflow
    import mlflow.sklearn
    from mlflow.tracking import MlflowClient
    HAS_MLFLOW = True
except ImportError:
    HAS_MLFLOW = False

from mlops.config import MLFLOW_TRACKING_URI, MLFLOW_EXPERIMENT_PREFIX, MLOPS_OUTPUTS_DIR


def _get_client():
    """Return an MLflow tracking client."""
    if not HAS_MLFLOW:
        raise RuntimeError("MLflow is not installed. Run: pip install mlflow")
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    return MlflowClient(MLFLOW_TRACKING_URI)


def _classification_experiment_names(game_name: str, module: str) -> list[str]:
    """Return current and legacy experiment names for a game/module."""
    return [
        f"{MLFLOW_EXPERIMENT_PREFIX}/{game_name}/{module}",
        f"{MLFLOW_EXPERIMENT_PREFIX}-{module}",
    ]


def _safe_tag_value(value) -> str:
    """MLflow tags must be strings and should stay reasonably small."""
    if value is None:
        return ""
    return str(value)[:250]


def _get_metric(metrics: dict, name: str):
    value = metrics.get(name)
    return float(value) if value is not None else None


def _run_summary(client, run_id: str | None, metric: str = "ensemble_test_acc") -> dict:
    """Return compact model identity and metrics for one MLflow run."""
    if not run_id:
        return {}
    run = client.get_run(run_id)
    params = run.data.params
    tags = run.data.tags
    metrics = run.data.metrics
    selected_model = (
        params.get("selected_model_name")
        or params.get("selected_model")
        or tags.get("selected_model_name")
        or "unknown"
    )
    return {
        "run_id": run_id,
        "run_name": tags.get("mlflow.runName", ""),
        "selected_model_name": selected_model,
        "metric_value": _get_metric(metrics, metric) or 0.0,
        "ensemble_test_acc": _get_metric(metrics, "ensemble_test_acc"),
        "selected_f1_macro": _get_metric(metrics, "selected_f1_macro"),
        "selected_model_cv_acc": _get_metric(metrics, "selected_model_cv_acc"),
        "selected_model_cv_f1_macro": _get_metric(metrics, "selected_model_cv_f1_macro"),
        "baseline_lift_f1": _get_metric(metrics, "baseline_lift_f1"),
        "brier_score": _get_metric(metrics, "brier_score"),
        "ece": _get_metric(metrics, "ece"),
    }


def _registration_summary_for_run(
    client,
    run_id: str,
    game_name: str,
    module: str,
    metric: str = "ensemble_test_acc",
) -> dict:
    """Validate and summarize the exact MLflow run requested for registration."""
    run = client.get_run(run_id)
    tags = run.data.tags
    metrics = run.data.metrics

    if run.info.status != "FINISHED":
        return {"error": f"Run {run_id} is not finished (status={run.info.status})"}
    if tags.get("status") != "SUCCESS":
        return {"error": f"Run {run_id} is not tagged as a successful pipeline run"}
    tagged_game = tags.get("game") or tags.get("game_id")
    if tagged_game and tagged_game != game_name:
        return {"error": f"Run {run_id} does not belong to game '{game_name}'"}
    tagged_module = tags.get("module")
    if tagged_module and tagged_module != module:
        return {"error": f"Run {run_id} does not belong to module '{module}'"}
    if metrics.get(metric) is None:
        return {"error": f"Run {run_id} is missing metric '{metric}'"}

    try:
        artifacts = client.list_artifacts(run_id)
        if not any(artifact.path == "model" for artifact in artifacts):
            return {"error": f"Run {run_id} has no logged MLflow model artifact"}
    except Exception as exc:
        return {"error": f"Could not inspect artifacts for run {run_id}: {exc}"}

    summary = _run_summary(client, run_id, metric=metric)
    summary.update({
        "metric_value": _get_metric(metrics, metric) or 0.0,
        "experiment_id": run.info.experiment_id,
        "experiment_name": "",
        "explicit_run_id": True,
    })
    return summary


def _write_champion_challenger_comparison(
    game_name: str,
    module: str,
    champion: dict | None,
    challenger: dict,
) -> dict:
    """Create PNG/CSV comparison artifacts from MLflow metrics."""
    if not challenger:
        return {}

    rows = []
    if champion:
        rows.append({
            "role": "champion",
            "model": champion.get("selected_model_name", "unknown"),
            "version": champion.get("version"),
            "run_id": champion.get("run_id"),
            "test_accuracy": champion.get("ensemble_test_acc"),
            "macro_f1": champion.get("selected_f1_macro"),
            "cv_accuracy": champion.get("selected_model_cv_acc"),
            "cv_macro_f1": champion.get("selected_model_cv_f1_macro"),
            "baseline_lift_f1": champion.get("baseline_lift_f1"),
            "brier_score": champion.get("brier_score"),
            "ece": champion.get("ece"),
        })
    rows.append({
        "role": "challenger",
        "model": challenger.get("selected_model_name", "unknown"),
        "version": challenger.get("version"),
        "run_id": challenger.get("run_id"),
        "test_accuracy": challenger.get("ensemble_test_acc"),
        "macro_f1": challenger.get("selected_f1_macro"),
        "cv_accuracy": challenger.get("selected_model_cv_acc"),
        "cv_macro_f1": challenger.get("selected_model_cv_f1_macro"),
        "baseline_lift_f1": challenger.get("baseline_lift_f1"),
        "brier_score": challenger.get("brier_score"),
        "ece": challenger.get("ece"),
    })

    out_dir = os.path.join(MLOPS_OUTPUTS_DIR, game_name)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "champion_challenger_comparison.csv")
    png_path = os.path.join(out_dir, "champion_challenger_comparison.png")

    try:
        import pandas as pd
        df = pd.DataFrame(rows)
        df.to_csv(csv_path, index=False)

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        higher_is_better = [
            ("test_accuracy", "Test accuracy"),
            ("macro_f1", "Macro F1"),
            ("cv_accuracy", "CV accuracy"),
            ("cv_macro_f1", "CV macro F1"),
            ("baseline_lift_f1", "F1 lift over baseline"),
        ]
        lower_is_better = [
            ("brier_score", "Brier score"),
            ("ece", "ECE"),
        ]

        fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
        roles = df["role"].tolist()
        colors = ["#2563eb" if role == "champion" else "#f59e0b" for role in roles]

        for ax, metrics, title in [
            (axes[0], higher_is_better, "Higher is better"),
            (axes[1], lower_is_better, "Lower is better"),
        ]:
            metric_labels = [label for key, label in metrics if key in df and df[key].notna().any()]
            metric_keys = [key for key, label in metrics if label in metric_labels]
            if not metric_labels:
                ax.text(0.5, 0.5, "No metrics available", ha="center", va="center")
                ax.set_title(title)
                continue
            x = np.arange(len(metric_labels))
            width = 0.35
            for idx, (_, row) in enumerate(df.iterrows()):
                values = [
                    0.0 if row.get(key) is None or pd.isna(row.get(key)) else row.get(key)
                    for key in metric_keys
                ]
                offset = (idx - (len(df) - 1) / 2) * width
                ax.bar(x + offset, values, width=width, label=row["role"], color=colors[idx], alpha=0.9)
            ax.set_xticks(x)
            ax.set_xticklabels(metric_labels, rotation=25, ha="right")
            ax.set_ylim(0, 1.05)
            ax.set_title(title)
            ax.grid(axis="y", alpha=0.2)
            ax.legend()

        champion_label = "none" if not champion else champion.get("selected_model_name", "unknown")
        fig.suptitle(
            f"{game_name.upper()} {module} champion vs challenger\n"
            f"Champion: {champion_label} | Challenger: {challenger.get('selected_model_name', 'unknown')}",
            fontsize=13,
            weight="bold",
        )
        fig.tight_layout(rect=[0, 0, 1, 0.88])
        fig.savefig(png_path, dpi=180)
        plt.close(fig)
        return {"csv_path": csv_path, "png_path": png_path}
    except Exception as exc:
        return {"error": str(exc), "csv_path": csv_path}


def get_best_run(game_name: str, module: str = "classification",
                 metric: str = "ensemble_test_acc"):
    """
    Find the best MLflow run for a given game and module.

    Returns:
        dict with run_id, metric_value, params, or None if no runs found
    """
    if not HAS_MLFLOW:
        return None

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    last_error = None
    for experiment_name in _classification_experiment_names(game_name, module):
        experiment = mlflow.get_experiment_by_name(experiment_name)
        if experiment is None:
            continue

        try:
            runs = mlflow.search_runs(
                experiment_ids=[experiment.experiment_id],
                filter_string=f"tags.game = '{game_name}' and tags.status = 'SUCCESS'",
                order_by=[f"metrics.{metric} DESC"],
                max_results=1,
            )
        except Exception as exc:
            # Some MLflow/Postgres versions fail instead of returning an empty
            # result for legacy experiments. Continue to the next compatible
            # experiment name so new game-module runs can still be registered.
            last_error = exc
            continue

        if len(runs) == 0:
            continue

        best = runs.iloc[0]
        selected_model = (
            best.get("params.selected_model_name")
            or best.get("params.selected_model")
            or best.get("tags.selected_model_name")
            or "unknown"
        )
        return {
            "run_id": best["run_id"],
            "metric_value": best.get(f"metrics.{metric}", 0),
            "selected_model_name": selected_model,
            "run_name": best.get("tags.mlflow.runName", ""),
            "selected_f1_macro": best.get("metrics.selected_f1_macro"),
            "selected_model_cv_acc": best.get("metrics.selected_model_cv_acc"),
            "selected_model_cv_f1_macro": best.get("metrics.selected_model_cv_f1_macro"),
            "baseline_lift_f1": best.get("metrics.baseline_lift_f1"),
            "start_time": best.get("start_time"),
            "experiment_id": experiment.experiment_id,
            "experiment_name": experiment_name,
        }

    if last_error is not None:
        return {"error": str(last_error)}
    return None


def register_best_model(
    game_name: str,
    module: str = "classification",
    metric: str = "ensemble_test_acc",
    run_id: str | None = None,
) -> dict:
    """
    Find the best run for a game/module and register it in the MLflow Model Registry.
    When run_id is provided, register that exact run and never search history.

    Returns:
        dict with model_name, version, run_id, status
    """
    if not HAS_MLFLOW:
        return {"error": "MLflow not installed"}

    client = _get_client()
    if run_id:
        best = _registration_summary_for_run(client, run_id, game_name, module, metric)
    else:
        best = get_best_run(game_name, module, metric)
    if isinstance(best, dict) and best.get("error"):
        return {"error": best["error"]}
    if best is None:
        return {"error": f"No successful runs found for {game_name}/{module}"}

    model_name = f"neurogames-{game_name}-{module}"
    model_uri = f"runs:/{best['run_id']}/model"

    try:
        result = mlflow.register_model(model_uri, model_name)
        selected_model = best.get("selected_model_name", "unknown")
        version_tags = {
            "game": game_name,
            "module": module,
            "selected_model_name": selected_model,
            "model_display_name": f"{game_name}/{selected_model}",
            "metric_name": metric,
            "metric_value": best.get("metric_value", 0),
            "registration_source_run_id": best["run_id"],
            "registration_mode": "explicit_run_id" if run_id else "best_historical_run",
            "source_run_name": best.get("run_name", ""),
        }
        for key, value in version_tags.items():
            client.set_model_version_tag(model_name, result.version, key, _safe_tag_value(value))
        client.set_registered_model_tag(model_name, "game", game_name)
        client.set_registered_model_tag(model_name, "module", module)
        client.set_registered_model_tag(model_name, "latest_selected_model_name", _safe_tag_value(selected_model))
        try:
            client.update_model_version(
                name=model_name,
                version=result.version,
                description=(
                    f"{game_name} {module} model version {result.version}: "
                    f"{selected_model}, {metric}={best.get('metric_value', 0):.4f}"
                ),
            )
        except Exception:
            pass
        return {
            "model_name": model_name,
            "model_display_name": f"{game_name}/{selected_model}",
            "selected_model_name": selected_model,
            "version": result.version,
            "run_id": best["run_id"],
            "run_name": best.get("run_name", ""),
            "metric": best["metric_value"],
            "metric_name": metric,
            "status": "registered",
        }
    except Exception as e:
        return {"error": str(e), "run_id": best["run_id"]}


def promote_to_production(game_name: str, module: str = "classification",
                          version: int = None) -> dict:
    """
    Promote a model version to champion (production) by setting a 'champion' alias.
    If version is None, promotes the latest version.
    Uses MLflow 2.x alias API (replaces deprecated transition_model_version_stage).
    """
    if not HAS_MLFLOW:
        return {"error": "MLflow not installed"}

    client = _get_client()
    model_name = f"neurogames-{game_name}-{module}"

    try:
        if version is None:
            # Get latest version without deprecated stage filter
            versions = client.search_model_versions(f"name='{model_name}'")
            if not versions:
                return {"error": f"No versions found for {model_name}"}
            version = max(int(v.version) for v in versions)

        # Use alias instead of deprecated stage. Passing an int avoids a
        # Postgres type collision in older MLflow/SQLAlchemy combinations.
        client.set_registered_model_alias(
            name=model_name,
            alias="champion",
            version=int(version),
        )
        try:
            model_version = client.get_model_version(model_name, str(version))
            run_summary = _run_summary(client, model_version.run_id)
            selected_model = run_summary.get("selected_model_name", "unknown")
            client.set_model_version_tag(model_name, str(version), "alias", "champion")
            client.set_model_version_tag(model_name, str(version), "champion", "true")
            client.set_registered_model_tag(model_name, "champion_version", str(version))
            client.set_registered_model_tag(model_name, "champion_run_id", _safe_tag_value(model_version.run_id))
            client.set_registered_model_tag(model_name, "champion_selected_model_name", _safe_tag_value(selected_model))
            client.set_registered_model_tag(
                model_name,
                "champion_metric_value",
                _safe_tag_value(run_summary.get("ensemble_test_acc")),
            )
        except Exception:
            pass

        return {
            "model_name": model_name,
            "version": version,
            "alias": "champion",
            "status": "promoted",
        }
    except Exception as e:
        return {"error": str(e)}


def get_champion_run(game_name: str, module: str = "classification",
                     metric: str = "ensemble_test_acc") -> dict | None:
    """
    Return the currently promoted champion run for one game/module.

    Registered versions can exist before an alias is assigned, so champion
    validation must read the alias directly instead of treating the best
    historical run as the current champion.
    """
    if not HAS_MLFLOW:
        return None

    client = _get_client()
    model_name = f"neurogames-{game_name}-{module}"

    try:
        version = client.get_model_version_by_alias(model_name, "champion")
        summary = _run_summary(client, version.run_id, metric=metric)
        summary.update({
            "model_name": model_name,
            "version": version.version,
            "run_id": version.run_id,
            "metric_value": summary.get("metric_value", 0),
        })
        return summary
    except Exception:
        return None


def load_production_model(game_name: str, module: str = "classification"):
    """
    Load the champion model from the MLflow Model Registry (via alias).
    Falls back to loading from the .pkl file if registry is empty.
    Uses MLflow 2.x alias URI: models:/name@champion
    """
    if not HAS_MLFLOW:
        return _fallback_load(game_name)

    model_name = f"neurogames-{game_name}-{module}"

    try:
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        # Try alias-based URI first (MLflow 2.x)
        model_uri = f"models:/{model_name}@champion"
        model = mlflow.sklearn.load_model(model_uri)
        return model
    except Exception:
        return _fallback_load(game_name)


def _fallback_load(game_name: str):
    """Fallback: load model from the original pkl file."""
    import joblib
    from multigame_pipeline.preprocessor import get_game_outputs

    pkl_path = os.path.join(get_game_outputs(game_name), "classifier_ensemble.pkl")
    if os.path.exists(pkl_path):
        return joblib.load(pkl_path)
    return None


def get_registry_summary() -> list:
    """
    Get a summary of all registered models for the dashboard.
    Returns list of dicts with model info.
    Uses MLflow 2.x search_model_versions (avoids deprecated get_latest_versions).
    """
    if not HAS_MLFLOW:
        return []

    try:
        client = _get_client()
        models = client.search_registered_models()
        summary = []
        for m in models:
            # Get all versions without deprecated stage filter
            versions = client.search_model_versions(f"name='{m.name}'")
            # Collect champion alias info
            try:
                model_info = client.get_registered_model(m.name)
                raw_aliases = model_info.aliases or {}
                if isinstance(raw_aliases, dict):
                    aliases = {
                        str(alias): str(version)
                        for alias, version in raw_aliases.items()
                    }
                else:
                    aliases = {
                        str(alias.alias): str(alias.version)
                        for alias in raw_aliases
                    }
            except Exception:
                aliases = {}
            for v in versions:
                alias_label = next((k for k, val in aliases.items() if val == str(v.version)), "-")
                try:
                    run_summary = _run_summary(client, v.run_id)
                except Exception:
                    run_summary = {}
                selected_model = (
                    run_summary.get("selected_model_name")
                    or (getattr(v, "tags", {}) or {}).get("selected_model_name")
                    or "unknown"
                )
                summary.append({
                    "model_name": m.name,
                    "model_display_name": f"{m.name} / {selected_model}",
                    "selected_model_name": selected_model,
                    "version": v.version,
                    "alias": alias_label,
                    "is_champion": alias_label == "champion",
                    "run_id": v.run_id,
                    "run_name": run_summary.get("run_name", ""),
                    "test_accuracy": run_summary.get("ensemble_test_acc"),
                    "macro_f1": run_summary.get("selected_f1_macro"),
                    "cv_accuracy": run_summary.get("selected_model_cv_acc"),
                    "cv_macro_f1": run_summary.get("selected_model_cv_f1_macro"),
                    "baseline_lift_f1": run_summary.get("baseline_lift_f1"),
                    "brier_score": run_summary.get("brier_score"),
                    "ece": run_summary.get("ece"),
                    "status": v.status,
                })
        return summary
    except Exception:
        return []



# ── Phase 5: Champion/Challenger Validation ──────────────────────────────────

def reset_classification_champions(
    games: list[str] | None = None,
    module: str = "classification",
    dry_run: bool = True,
) -> dict:
    """
    Remove champion aliases and stale champion tags without deleting runs or versions.

    Returns a per-model action report. Defaults to dry-run so presentation setup
    can be inspected before changing registry state.
    """
    if not HAS_MLFLOW:
        return {"error": "MLflow not installed"}

    if games is None:
        try:
            from mlops.db import list_ml_enabled_game_ids
            games = list_ml_enabled_game_ids()
        except Exception:
            from mlops.config import GAME_NAMES
            games = list(GAME_NAMES)

    client = _get_client()
    champion_model_tags = [
        "champion_version",
        "champion_run_id",
        "champion_selected_model_name",
        "champion_metric_value",
    ]
    champion_version_tags = ["alias", "champion"]
    results = []

    for game_name in games:
        model_name = f"neurogames-{game_name}-{module}"
        entry = {
            "game": game_name,
            "model_name": model_name,
            "dry_run": dry_run,
            "actions": [],
            "errors": [],
        }

        try:
            client.get_registered_model(model_name)
        except Exception as exc:
            entry["status"] = "missing"
            entry["errors"].append(str(exc))
            results.append(entry)
            continue

        champion_version = None
        try:
            champion = client.get_model_version_by_alias(model_name, "champion")
            champion_version = str(champion.version)
            entry["actions"].append(f"delete alias champion -> version {champion_version}")
            if not dry_run:
                client.delete_registered_model_alias(model_name, "champion")
        except Exception:
            entry["actions"].append("no champion alias present")

        for tag in champion_model_tags:
            entry["actions"].append(f"delete registered model tag {tag}")
            if not dry_run:
                try:
                    client.delete_registered_model_tag(model_name, tag)
                except Exception as exc:
                    entry["errors"].append(f"{tag}: {exc}")

        if champion_version:
            for tag in champion_version_tags:
                entry["actions"].append(f"delete version {champion_version} tag {tag}")
                if not dry_run:
                    try:
                        client.delete_model_version_tag(model_name, champion_version, tag)
                    except Exception as exc:
                        entry["errors"].append(f"version {champion_version} {tag}: {exc}")

        entry["status"] = "dry_run" if dry_run else "reset"
        results.append(entry)

    return {
        "status": "dry_run" if dry_run else "reset",
        "module": module,
        "games": games,
        "results": results,
    }


def validate_challenger(
    game_name: str,
    challenger_acc: float,
    module: str = "classification",
    min_improvement: float = 0.01,
    challenger_run_id: str | None = None,
) -> dict:
    """
    Compare a new (challenger) model's accuracy against the current champion.

    Args:
        game_name:       Game identifier (e.g. ``'gonogo'``).
        challenger_acc:  Test accuracy of the new model.
        module:          Pipeline module name.
        min_improvement: Minimum required delta to allow promotion (default 1%).
        challenger_run_id: MLflow run ID for the newly registered challenger.

    Returns:
        dict with keys: promoted, champion_acc, challenger_acc, delta, reason.
    """
    result = {
        "promoted": False,
        "champion_acc": None,
        "challenger_acc": round(challenger_acc, 4),
        "delta": None,
        "reason": "",
        "champion_model_name": None,
        "champion_version": None,
        "champion_run_id": None,
        "champion_selected_model_name": None,
        "challenger_run_id": challenger_run_id,
        "challenger_selected_model_name": None,
        "comparison_artifacts": {},
    }

    client = _get_client() if HAS_MLFLOW else None
    challenger = {
        "run_id": challenger_run_id,
        "metric_value": challenger_acc,
        "ensemble_test_acc": challenger_acc,
        "selected_model_name": "unknown",
    }
    if client and challenger_run_id:
        try:
            challenger = _run_summary(client, challenger_run_id)
            challenger["metric_value"] = challenger_acc
        except Exception:
            pass
    result["challenger_selected_model_name"] = challenger.get("selected_model_name")

    champion = get_champion_run(game_name, module, metric="ensemble_test_acc")
    if champion is not None:
        result.update({
            "champion_model_name": champion.get("model_name"),
            "champion_version": champion.get("version"),
            "champion_run_id": champion.get("run_id"),
            "champion_selected_model_name": champion.get("selected_model_name"),
        })

    artifacts = _write_champion_challenger_comparison(game_name, module, champion, challenger)
    result["comparison_artifacts"] = artifacts
    if client and challenger_run_id and artifacts:
        for key in ("png_path", "csv_path"):
            path = artifacts.get(key)
            if path and os.path.exists(path):
                try:
                    client.log_artifact(challenger_run_id, path)
                except Exception:
                    pass

    if champion is None:
        # No champion exists — auto-promote
        result["promoted"] = True
        result["reason"] = "No existing champion; auto-promoting first model"
        return result

    champion_acc = champion.get("metric_value", 0)
    delta = round(challenger_acc - champion_acc, 4)
    result["champion_acc"] = round(champion_acc, 4)
    result["delta"] = delta

    if delta >= min_improvement:
        result["promoted"] = True
        result["reason"] = (
            f"Challenger ({challenger_acc:.4f}) beats champion "
            f"({champion_acc:.4f}) by {delta:+.4f} ≥ {min_improvement}"
        )
    elif delta >= 0:
        result["promoted"] = False
        result["reason"] = (
            f"Challenger ({challenger_acc:.4f}) marginally better than champion "
            f"({champion_acc:.4f}) by {delta:+.4f} — below min improvement {min_improvement}"
        )
    else:
        result["promoted"] = False
        result["reason"] = (
            f"Challenger ({challenger_acc:.4f}) is worse than champion "
            f"({champion_acc:.4f}) by {delta:+.4f}"
        )

    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MLOps Model Registry")
    parser.add_argument("action", choices=["register", "promote", "summary", "reset-champions"])
    parser.add_argument("--game", default="gonogo")
    parser.add_argument("--module", default="classification")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.action == "register":
        result = register_best_model(args.game, args.module, run_id=args.run_id)
        print(result)
    elif args.action == "promote":
        result = promote_to_production(args.game, args.module)
        print(result)
    elif args.action == "summary":
        for entry in get_registry_summary():
            print(f"  {entry['model_name']} v{entry['version']} [{entry['alias']}]")
    elif args.action == "reset-champions":
        games = None if args.game == "all" else [args.game]
        print(reset_classification_champions(games=games, module=args.module, dry_run=not args.apply))

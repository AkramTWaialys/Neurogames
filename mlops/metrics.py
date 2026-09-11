"""
Prometheus Metrics — NeuroGames MLOps
======================================
Centralised metric definitions used by the FastAPI server,
drift monitor, and pipeline webhook endpoints.

All custom metrics follow the naming convention:
    neurogames_<domain>_<metric>_<unit>
"""

from prometheus_client import Counter, Gauge, Histogram

# ── Data Ingestion ────────────────────────────────────────────────────────────
sessions_ingested = Counter(
    "neurogames_sessions_ingested_total",
    "Total game sessions successfully ingested",
    ["game"],
)

session_validation_failed = Counter(
    "neurogames_session_validation_failed_total",
    "Total session payloads rejected by validation or storage",
    ["game", "reason"],
)

pending_sessions = Gauge(
    "neurogames_pending_sessions",
    "Number of pending (non-aggregated) sessions per game",
    ["game"],
)

ml_eligible_sessions = Gauge(
    "neurogames_ml_eligible_sessions",
    "Number of sessions eligible for ML per ADHD game module",
    ["game"],
)

total_sessions_trained = Gauge(
    "neurogames_total_sessions_trained",
    "Total sessions per game consumed by training runs",
    ["game"],
)

# ── Data Drift ────────────────────────────────────────────────────────────────
drift_score = Gauge(
    "neurogames_drift_score",
    "Latest drift share (fraction of features drifted) per game",
    ["game"],
)

drift_features_count = Gauge(
    "neurogames_drift_features_count",
    "Number of features with detected drift per game",
    ["game"],
)

drift_detected = Gauge(
    "neurogames_drift_detected",
    "Whether drift is detected (1) or not (0) per game",
    ["game"],
)

# ── Advanced Drift (set by API process after worker completes) ────────────
drift_per_feature = Gauge(
    "neurogames_drift_per_feature_pvalue",
    "Per-feature drift p-value from latest check",
    ["game", "feature"],
)

drift_type_detected = Gauge(
    "neurogames_drift_type_detected",
    "Whether drift detected by type (1=yes, 0=no)",
    ["game", "drift_type"],
)

# ── ML Pipeline ───────────────────────────────────────────────────────────────
pipeline_duration = Histogram(
    "neurogames_pipeline_duration_seconds",
    "Duration of pipeline step execution in seconds",
    ["step", "game"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 900],
)

retrain_total = Counter(
    "neurogames_retrain_total",
    "Total number of model retrain events triggered by drift",
    ["game"],
)

promotion_total = Counter(
    "neurogames_promotion_total",
    "Total number of champion model promotions after challenger comparison",
    ["game"],
)

# ── Reports ───────────────────────────────────────────────────────────────────
reports_generated = Counter(
    "neurogames_reports_generated_total",
    "Total clinical reports generated",
    ["method"],  # "llm" or "template"
)


def initialize_metric_series(game_names: list[str]) -> None:
    """
    Create zero-valued labelled series before the first event happens.

    Prometheus only exports a labelled Counter/Gauge/Histogram after that
    labelset has been touched at least once. Initializing the expected labels
    makes Grafana panels render immediately after the first scrape instead of
    showing "No data" until traffic, drift, or pipeline actions occur.
    """
    for game in game_names:
        sessions_ingested.labels(game=game)
        session_validation_failed.labels(game=game, reason="validation")
        session_validation_failed.labels(game=game, reason="storage")
        pending_sessions.labels(game=game).set(0)
        ml_eligible_sessions.labels(game=game).set(0)
        total_sessions_trained.labels(game=game).set(0)
        drift_score.labels(game=game).set(0)
        drift_features_count.labels(game=game).set(0)
        drift_detected.labels(game=game).set(0)
        retrain_total.labels(game=game)
        promotion_total.labels(game=game)

        for step in ("classify", "drift_retrain"):
            pipeline_duration.labels(step=step, game=game)

    pipeline_duration.labels(step="cross", game="all")

    for method in ("llm", "template", "cached", "unknown"):
        reports_generated.labels(method=method)

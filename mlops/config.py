"""
MLOps Configuration
====================
Central configuration for all MLOps components:
server, aggregator, trigger, tracking, registry, drift monitor.
"""

import os

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MLOPS_DATA_DIR = os.path.join(PROJECT_ROOT, "mlops_data")
MLOPS_OUTPUTS_DIR = os.path.join(PROJECT_ROOT, "mlops_outputs")
REPORTS_DIR = os.path.join(MLOPS_OUTPUTS_DIR, "reports")
MLRUNS_DIR = os.path.join(PROJECT_ROOT, "mlruns")

# ── PostgreSQL Database ───────────────────────────────────────────────────
# Primary database connection for all app tables and session data.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:aaaa@localhost:5433/neurogames",
)

# ── MLflow Backend Store ──────────────────────────────────────────────────
# Store experiment metadata in PostgreSQL.
MLFLOW_BACKEND_STORE_URI = os.environ.get(
    "MLFLOW_BACKEND_STORE_URI",
    DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1),
)



TRIGGER_LOG_PATH = os.path.join(MLOPS_DATA_DIR, "trigger_log.json")

# ── Game Configuration ────────────────────────────────────────────────────────
# Game metadata is defined once in game_registry.py and re-exported here
# so that all existing importers of mlops.config continue to work unchanged.
from .game_registry import GAME_NAMES, GAME_DISPLAY_NAMES, GAME_DOMAINS  # noqa: F401

CLUSTER_LABELS = [
    "Combined ADHD",
    "Hyperactive-Impulsive ADHD",
    "Inattentive ADHD",
    "Optimal / Neurotypical",
]

# ── Server ────────────────────────────────────────────────────────────────────
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000

# ── Trigger ───────────────────────────────────────────────────────────────────
TRIGGER_THRESHOLD = 20       # new sessions per game before auto-trigger
TRIGGER_MODE = "count"       # "count" or "schedule"
SCHEDULE_TIME = "02:00"      # HH:MM — used when TRIGGER_MODE == "schedule"

# ── MLflow ────────────────────────────────────────────────────────────────────
# Use the MLflow tracking server (start with start_mlflow.bat).
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
MLFLOW_EXPERIMENT_PREFIX = "NeuroGames"

# Disable incompatible logged model creation feature (matches churn_predection.ipynb approach)
os.environ["MLFLOW_ENABLE_LOGGED_MODEL_CREATION"] = "false"

# ── Drift ─────────────────────────────────────────────────────────────────────
DRIFT_CONFIDENCE = 0.95      # statistical confidence for drift detection


def get_drift_output_dir(game_name: str) -> str:
    """Return (and create) the drift report output directory for a game."""
    d = os.path.join(MLOPS_OUTPUTS_DIR, game_name)
    os.makedirs(d, exist_ok=True)
    return d


def ensure_dirs():
    """Create all required MLOps directories."""
    for d in [MLOPS_DATA_DIR, MLOPS_OUTPUTS_DIR, REPORTS_DIR]:
        os.makedirs(d, exist_ok=True)
    game_ids = list(GAME_NAMES)
    try:
        from .db import list_game_modules
        registered = [g["game_id"] for g in list_game_modules(active_only=True)]
        game_ids = registered or game_ids
    except Exception:
        pass
    for game in game_ids:
        os.makedirs(os.path.join(MLOPS_OUTPUTS_DIR, game), exist_ok=True)

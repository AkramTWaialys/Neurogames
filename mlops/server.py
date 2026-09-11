"""
MLOps FastAPI Ingestion Server (PostgreSQL Edition)
===================================================
Receives game session data from testers via REST API,
validates it, and stores it in the centralised PostgreSQL database.

Start with:
    uvicorn mlops.server:app --host 0.0.0.0 --port 8000
"""

from datetime import datetime, timezone
import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager

from .config import SERVER_HOST, SERVER_PORT, ensure_dirs
from .game_adapters import GameAdapterError, validate_game_payload
from .aggregator import aggregate_game, aggregate_all_games, count_all_incoming
from .db import (
    count_game_sessions,
    get_user_profile,
    init_db,
    insert_session,
    insert_sessions_bulk,
    is_registered_game,
    list_game_modules,
    PUBLISHED_GAME_DIR,
    resolve_participant_user_id,
    verify_game_ingestion_key,
)
from .metrics import (
    initialize_metric_series,
    sessions_ingested,
    pending_sessions,
    total_sessions_trained,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure DB is ready and migrations run once
    logging.getLogger("mlops.server").info("Initializing database...")
    init_db()
    # Initialize metrics and dashboard cache in background
    refresh_prometheus_state()
    _schedule_cache_refresh()
    yield
    # Shutdown: Clean up resources if needed
    pass


# ── Create directories on import ──────────────────────────────────────────────
ensure_dirs()

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="NeuroGames MLOps Ingestion Server",
    description=(
        "Receives game session data from testers, validates it, "
        "and stores it in the centralised PostgreSQL database."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# Allow game clients from any origin to POST data
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Path(PUBLISHED_GAME_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/game-packages", StaticFiles(directory=str(PUBLISHED_GAME_DIR)), name="game-packages")


def refresh_prometheus_state() -> None:
    """Expose baseline metric values even before ingestion/pipeline traffic."""
    game_ids = [g["game_id"] for g in list_game_modules(active_only=False)]
    initialize_metric_series(game_ids)
    for game in game_ids:
        total = count_game_sessions(game, trained_only=False)
        trained = count_game_sessions(game, trained_only=True)
        pending_sessions.labels(game=game).set(max(total - trained, 0))
        total_sessions_trained.labels(game=game).set(trained)


# refresh_prometheus_state()  # Moved to lifespan


def _cluster_for_auth_session(auth_session: dict) -> str:
    """Return the server-owned training label for the authenticated user."""
    profile = get_user_profile(auth_session["user_id"])
    return (profile or {}).get("cluster") or "Unknown"


# ── Dashboard cache background refresh ────────────────────────────────────────
_log = logging.getLogger("mlops.server")


def _schedule_cache_refresh() -> None:
    """Schedule a dashboard cache refresh in a background thread.

    Uses run_in_executor so it never blocks the API response.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _do_cache_refresh)
    except RuntimeError:
        pass  # No event loop (e.g. during tests)


def _do_cache_refresh() -> None:
    """Actual cache refresh — runs in a worker thread."""
    try:
        from .web_service import refresh_dashboard_cache
        refresh_dashboard_cache()
    except Exception as e:
        _log.warning("Dashboard cache refresh failed: %s", e)


# Seed the cache on startup
# _do_cache_refresh() # Moved to lifespan


# ── Prometheus Metrics ────────────────────────────────────────────────────────
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
except ImportError:
    # Fallback: manual /metrics endpoint using prometheus_client
    try:
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        from fastapi.responses import Response

        @app.get("/metrics")
        async def metrics():
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
    except ImportError:
        pass


# ── Web-facing routes (participants, games, reports, lookup) ──────────────────
from .web_routes import router as web_router
app.include_router(web_router)

from .pipeline_routes import router as pipeline_router
app.include_router(pipeline_router)

from .game_module_routes import router as game_module_router
app.include_router(game_module_router)

from .fhir_routes import router as fhir_router
app.include_router(fhir_router)

# ── Routes ────────────────────────────────────────────────────────────────────

from .auth_routes import get_current_auth_session, router as auth_router
app.include_router(auth_router)


def _auth_session_from_request(request: Request) -> dict:
    return get_current_auth_session(request)


def _apply_upload_identity(
    game_name: str,
    row: dict,
    request: Request,
    game_key: str | None,
) -> dict:
    """
    Attach the authoritative participant identity and label source.

    Normal NeuroGames clients use bearer auth. A manually integrated external
    site can use a backend-only game-ingestion key, but its Participant_ID must
    map to an existing NeuroGames user before the row is ML-eligible.
    """
    if game_key:
        if not verify_game_ingestion_key(game_name, game_key):
            raise HTTPException(status_code=401, detail="Invalid or missing game ingestion key")
        participant_id = str(row.get("Participant_ID") or "").strip()
        user_id = resolve_participant_user_id(participant_id)
        row["_user_id"] = user_id
        if user_id is None:
            row["cluster"] = "Unknown"
            row["_ml_eligible"] = False
        else:
            profile = get_user_profile(user_id)
            row["cluster"] = (profile or {}).get("cluster") or "Unknown"
        row["ingestion_mode"] = "game_key"
        return row

    auth_session = _auth_session_from_request(request)
    row["Participant_ID"] = auth_session["username"]
    row["_user_id"] = auth_session["user_id"]
    row["cluster"] = _cluster_for_auth_session(auth_session)
    row["ingestion_mode"] = "neurogames_auth"
    return row


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/status")
async def status():
    """
    Returns the number of untrained sessions per game,
    plus the total sessions per game from PostgreSQL.
    """
    pending = count_all_incoming()
    aggregated = {}
    game_ids = [g["game_id"] for g in list_game_modules(active_only=True)]
    for game in game_ids:
        aggregated[game] = count_game_sessions(game)

    return {
        "pending_sessions": pending,
        "aggregated_sessions": aggregated,
        "games": game_ids,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/upload/{game_name}")
async def upload_session_endpoint(
    game_name: str,
    session_data: dict,
    request: Request,
    x_neurogames_game_key: str | None = Header(default=None, alias="X-NeuroGames-Game-Key"),
):
    """
    Receive a single game session as JSON.

    1. Validates game_name is a registered ADHD game module
    2. Validates session_data through the game's adapter
    3. Inserts directly into the shared sessions table
    """
    if not is_registered_game(game_name):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown game module '{game_name}'. Register it in /api/game-modules first."
        )

    try:
        row = validate_game_payload(game_name, session_data)
    except GameAdapterError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Validation failed: {str(e)}"
        )

    row = _apply_upload_identity(game_name, row, request, x_neurogames_game_key)
    row["ingested_at"] = datetime.now(timezone.utc).isoformat()
    rowid = insert_session(game_name, row)

    # ── Prometheus metrics ────────────────────────────────────────────
    sessions_ingested.labels(game=game_name).inc()
    pending_sessions.labels(game=game_name).set(
        count_game_sessions(game_name, trained_only=False)
        - count_game_sessions(game_name, trained_only=True)
    )

    # Refresh dashboard cache in background after ingestion
    _schedule_cache_refresh()

    return {
        "status": "ok",
        "game": game_name,
        "session_id": row["Game_Session_ID"],
        "participant_id": row["Participant_ID"],
        "ml_eligible": bool(row.get("_ml_eligible", True)),
        "db_row_id": rowid,
        "timestamp": datetime.now().isoformat(),
    }



@app.post("/upload_batch/{game_name}")
async def upload_batch(
    game_name: str,
    sessions: list[dict],
    request: Request,
    x_neurogames_game_key: str | None = Header(default=None, alias="X-NeuroGames-Game-Key"),
):
    """
    Receive multiple game sessions at once.
    Each item in the list is validated individually.
    Returns the number of successful uploads and any errors.
    """
    if not is_registered_game(game_name):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown game module '{game_name}'. Register it in /api/game-modules first."
        )

    results = {"uploaded": 0, "failed": 0, "errors": []}

    validated_rows = []
    for i, session_data in enumerate(sessions):
        try:
            row = validate_game_payload(game_name, session_data)
            row = _apply_upload_identity(game_name, row, request, x_neurogames_game_key)
            row["ingested_at"] = datetime.now(timezone.utc).isoformat()
            validated_rows.append(row)
            results["uploaded"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({"index": i, "error": str(e)})

    if validated_rows:
        insert_sessions_bulk(game_name, validated_rows)
        # ── Prometheus metrics ────────────────────────────────────────
        sessions_ingested.labels(game=game_name).inc(len(validated_rows))
        pending_sessions.labels(game=game_name).set(
            count_game_sessions(game_name, trained_only=False)
            - count_game_sessions(game_name, trained_only=True)
        )

    # Refresh dashboard cache in background after batch ingestion
    _schedule_cache_refresh()

    return results



@app.post("/aggregate/{game_name}")
async def trigger_aggregate(game_name: str):
    """
    Legacy compatibility endpoint. Returns untrained counts without marking
    data as trained; successful training tasks now update training_runs.
    """
    if not is_registered_game(game_name):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown game module '{game_name}'. Register it in /api/game-modules first."
        )

    result = aggregate_game(game_name)

    # ── Prometheus metrics ────────────────────────────────────────────
    trained = count_game_sessions(game_name, trained_only=True)
    pending_sessions.labels(game=game_name).set(max(result["n_total"] - trained, 0))
    total_sessions_trained.labels(game=game_name).set(trained)

    return {
        "status": "ok",
        "game": game_name,
        "untrained_sessions": result["n_new"],
        "total_sessions": result["n_total"],
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/aggregate_all")
async def trigger_aggregate_all():
    """Legacy endpoint returning untrained counts without changing state."""
    results = aggregate_all_games()

    # ── Prometheus metrics ────────────────────────────────────────────
    for r in results:
        trained = count_game_sessions(r["game"], trained_only=True)
        pending_sessions.labels(game=r["game"]).set(max(r["n_total"] - trained, 0))
        total_sessions_trained.labels(game=r["game"]).set(trained)

    return {
        "status": "ok",
        "results": [
            {"game": r["game"], "new": r["n_new"], "total": r["n_total"]}
            for r in results
        ],
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/schema/{game_name}")
async def get_game_schema(game_name: str):
    """
    Returns the expected JSON schema for a game's session data.
    Useful for game client developers to know what fields to send.
    """
    if not is_registered_game(game_name):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown game module '{game_name}'. Register it in /api/game-modules first."
        )

    from .schemas import DynamicGameSession, GAME_SCHEMAS
    schema_cls = GAME_SCHEMAS.get(game_name) or DynamicGameSession
    return schema_cls.model_json_schema()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)

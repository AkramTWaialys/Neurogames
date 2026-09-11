"""
Web-facing API routes for the NeuroGames frontend.

Provides participant profiles, session history, game metadata,
player lookup, and clinical report endpoints — all served from
the single MLOps server on port 8000.

These routes were previously on a separate backend at port 8001 and
have been consolidated here as part of the single-server architecture.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from .web_schemas import (
    GameInfo,
    GameListResponse,
    ParticipantSummary,
    SessionHistoryResponse,
)
from .web_service import (
    get_all_games,
    get_game,
    get_participant_summary,
    get_participant_sessions,
)

router = APIRouter()


# ── Game Metadata ─────────────────────────────────────────────────────────────

@router.get("/api/games", response_model=GameListResponse, tags=["games"])
def list_games():
    """List all available cognitive games."""
    games = get_all_games()
    return GameListResponse(games=games, count=len(games))


@router.get("/api/games/{game_id}", response_model=GameInfo, tags=["games"])
def game_detail(game_id: str):
    """Get detailed metadata for a specific game."""
    game = get_game(game_id)
    if game is None:
        raise HTTPException(
            status_code=404,
            detail=f"Game '{game_id}' not found. "
                   f"Available games: {[g.id for g in get_all_games()]}",
        )
    return game


# ── Participant Endpoints ─────────────────────────────────────────────────────

@router.get(
    "/api/participants/{pid}/summary",
    response_model=ParticipantSummary,
    tags=["participants"],
)
def participant_summary(pid: str, request: Request):
    """
    Get aggregated stats and progression for a participant.
    Returns per-game breakdowns, overall accuracy, XP, and level.
    """
    access = _require_participant_reader(request, pid)
    school_id = access.get("school_id")
    
    summary = get_participant_summary(pid, school_id=school_id)
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"No sessions for '{pid}' or access denied.",
        )
    return summary


@router.get(
    "/api/participants/{pid}/sessions",
    response_model=SessionHistoryResponse,
    tags=["participants"],
)
def participant_sessions(pid: str, request: Request):
    """
    Get every individual session for a participant.
    Returns full session details with game metadata, accuracy,
    reaction time, and performance level for history display.
    """
    access = _require_participant_reader(request, pid)
    school_id = access.get("school_id")
    
    result = get_participant_sessions(pid, school_id=school_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No sessions for '{pid}' or access denied.",
        )
    return result


# ── Player Lookup ─────────────────────────────────────────────────────────────

@router.get("/lookup/{pid}", tags=["participants"])
def legacy_lookup(pid: str):
    """Look up player info. Returns defaults for unknown players."""
    summary = get_participant_summary(pid)
    if summary is None:
        return {
            "age": 9,
            "age_group": "9-11",
            "cognitive_level": "Medium",
            "participant_id": pid,
        }
    return {
        "age": 9,
        "age_group": summary.games[0].game_id if summary.games else "9-11",
        "cognitive_level": "Medium",
        "participant_id": pid,
    }


# ── Report eligibility constants ──────────────────────────────────────────────

REPORT_SESSION_THRESHOLD = 20  # sessions required for a new report


def _request_locale(request: Request) -> str:
    from .reporter import normalize_report_locale

    return normalize_report_locale(request.query_params.get("locale"))


def _compute_report_status(pid: str) -> dict:
    """Compute full report eligibility for a participant (reused by multiple endpoints)."""
    from .db import count_participant_sessions, count_sessions_since, get_latest_report_run
    from .reporter import find_latest_report

    total = count_participant_sessions(pid)
    has_report = False
    report_date = None
    sessions_since = total  # default: all sessions count if no prior report

    try:
        latest_run = get_latest_report_run(pid)
        if latest_run:
            has_report = True
            report_date = latest_run.get("snapshot_until") or latest_run.get("generated_at")
            if report_date:
                sessions_since = count_sessions_since(pid, report_date)
        else:
            latest = find_latest_report(pid)
            if latest:
                has_report = True
                report_date = latest.get("snapshot_until") or latest.get("generated_at")
                if report_date:
                    sessions_since = count_sessions_since(pid, report_date)
    except Exception:
        pass

    # Eligibility: 20-session rule
    if not has_report:
        eligible = total >= REPORT_SESSION_THRESHOLD
        reason = (
            None if eligible
            else f"Need {REPORT_SESSION_THRESHOLD - total} more sessions for first report"
        )
    else:
        eligible = sessions_since >= REPORT_SESSION_THRESHOLD
        reason = (
            None if eligible
            else f"Need {REPORT_SESSION_THRESHOLD - sessions_since} more sessions since last report"
        )

    return {
        "total_sessions": total,
        "has_report": has_report,
        "report_date": report_date,
        "sessions_since_report": sessions_since,
        "eligible": eligible,
        "reason": reason,
    }


# ── Cognitive Performance Reports ─────────────────────────────────────────────

@router.post("/report/{pid}", tags=["reports"])
def generate_report(pid: str, request: Request):
    """Generate or fetch a cognitive performance report via mlops.reporter."""
    _require_admin(request) # Keep as generic admin for now, but compute_report_status should check school
    try:
        from .reporter import generate_report as _generate_report
        from .db import insert_report_run

        compare = request.query_params.get("compare", "false").lower() == "true"
        locale = _request_locale(request)
        result = _generate_report(pid, compare=compare, locale=locale)
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
            except Exception:
                pass
        return {
            "status": "ok",
            "report_text": result["report_text"],
            "method": result["method"],
            "structured": result.get("structured"),
            "locale": result.get("locale"),
            "report_group_id": result.get("report_group_id"),
            "snapshot_until": result.get("snapshot_until"),
            "session_count_at_snapshot": result.get("session_count_at_snapshot"),
            "validation": result.get("validation", {"valid": True, "issues": []}),
        }
    except Exception as e:
        return {
            "status": "partial",
            "report_text": (
                f"# Cognitive Performance Report for {pid}\n\n"
                "AI Generator unavailable.\n\n"
                "Summary stats would go here in a production build."
            ),
            "method": "fallback",
            "error": str(e),
        }


@router.get("/report/{pid}/latest", tags=["reports"])
def get_latest_report(pid: str, request: Request):
    """Fetch the latest report, creating a missing locale variant from its snapshot."""
    _require_admin(request)
    try:
        from .reporter import get_or_create_latest_report_variant

        locale = _request_locale(request)
        report = get_or_create_latest_report_variant(pid, locale=locale)
        if report is None:
            raise HTTPException(status_code=404, detail="No report found")
        return {
            "report_text": report.get("report_text", ""),
            "method": report.get("method", "pre_generated"),
            "structured": report.get("structured"),
            "report_date": report.get("snapshot_until") or report.get("generated_at", ""),
            "locale": report.get("locale"),
            "report_group_id": report.get("report_group_id"),
            "snapshot_until": report.get("snapshot_until"),
            "session_count_at_snapshot": report.get("session_count_at_snapshot"),
        }
    except ImportError:
        raise HTTPException(status_code=404, detail="Reporter module unavailable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/report/{pid}/status", tags=["reports"])
def report_status(pid: str, request: Request):
    """
    Report eligibility status.
    """
    _require_admin(request)
    return _compute_report_status(pid)


@router.post("/report/{pid}/request", tags=["reports"])
def request_report(pid: str, request: Request):
    """
    Queue on-demand report generation.

    Validates the 20-session eligibility rule, then submits the report
    task to the background worker. Returns immediately with a task_id
    for polling via GET /pipeline/status/{task_id}.
    """
    _require_admin(request)
    status = _compute_report_status(pid)
    if not status["eligible"]:
        raise HTTPException(status_code=409, detail=status["reason"])

    from .worker import task_manager
    from .tasks import run_single_report_task

    locale = _request_locale(request)
    task_name = f"report_{pid}"
    task_id, created = task_manager.get_or_submit(
        task_name,
        run_single_report_task,
        args=(pid, locale),
    )
    if not created:
        return {
            "status": "already_running",
            "task_id": task_id,
            "poll_url": f"/pipeline/status/{task_id}",
        }

    return {
        "status": "accepted",
        "task_id": task_id,
        "poll_url": f"/pipeline/status/{task_id}",
    }


@router.get("/report/{pid}/queue-status", tags=["reports"])
def report_queue_status(pid: str, request: Request):
    """
    Check whether a report is currently being generated for this player.
    """
    _require_admin(request)
    from .worker import task_manager

    task_name = f"report_{pid}"
    running_id = task_manager.is_running(task_name)

    if running_id:
        status = task_manager.get_status(running_id)
        # Estimate queue position: count running report tasks ahead of this one
        all_tasks = task_manager.list_tasks()
        report_tasks = [
            t for t in all_tasks
            if t and t["name"].startswith("report_") and t["state"] == "running"
        ]
        position = next(
            (i + 1 for i, t in enumerate(report_tasks) if t["task_id"] == running_id),
            1,
        )
        return {
            "generating": True,
            "task_id": running_id,
            "position": position,
            "state": status["state"] if status else "running",
            "elapsed_seconds": status["elapsed_seconds"] if status else None,
            "poll_url": f"/pipeline/status/{running_id}",
        }

    return {"generating": False, "task_id": None, "position": 0}


# ── API Status ────────────────────────────────────────────────────────────────

@router.get("/api/status", tags=["health"])
def api_status():
    """Status endpoint with game count and list."""
    games = get_all_games()
    return {
        "game_count": len(games),
        "games": [g.id for g in games],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Behavioral Events (Phase 4) ───────────────────────────────────────────────

@router.post("/session/{game}/events", tags=["events"])
async def ingest_events(game: str, request: Request):
    """
    Ingest a batch of raw behavioral events for a game session.
    Accepts a JSON body matching the EventBatch schema.
    """
    from .event_schemas import EventBatch
    from .db import insert_behavioral_events, is_registered_game

    if not is_registered_game(game):
        raise HTTPException(status_code=400, detail=f"Unknown game: {game}")

    body = await request.json()
    body["game"] = game

    try:
        batch = EventBatch(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Validation error: {e}")

    events_dicts = [
        {
            "participant_id": batch.participant_id,
            "session_id": batch.session_id,
            "event_type": evt.event_type,
            "event_timestamp": evt.event_timestamp,
            "event_data": evt.event_data,
        }
        for evt in batch.events
    ]

    n_inserted = insert_behavioral_events(game, events_dicts)
    return {"inserted": n_inserted, "game": game, "session_id": batch.session_id}


# ── Admin Dashboard Endpoints ─────────────────────────────────────────────────

import os
from .web_service import (
    get_admin_stats,
    refresh_dashboard_cache,
    get_population_data,
    get_all_participants_paginated,
    get_participant_timeline,
    get_game_analytics,
    get_anomaly_data,
    get_classification_data,
    get_concordance_data,
)

def _require_admin(request: Request) -> dict:
    """Validate token and require therapist or super_admin role."""
    from .auth_routes import get_current_auth_session
    session = get_current_auth_session(request)
    if session.get("role") not in ("therapist", "admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Therapist privileges required")
    return session

def _require_school_admin(request: Request) -> dict:
    """Require a school-scoped therapist role. Blocks super_admin from sensitive player data."""
    session = _require_admin(request)
    if session.get("role") not in ("therapist", "admin"):
        raise HTTPException(status_code=403, detail="Therapist privileges required (Super Admin access restricted)")
    return session


def _require_participant_reader(request: Request, pid: str) -> dict:
    """Allow a child to read only their own history, or a therapist to read school-scoped history."""
    from .auth_routes import get_current_auth_session

    session = get_current_auth_session(request)
    role = session.get("role")
    if role == "child":
        if str(session.get("username", "")).lower() != str(pid).lower():
            raise HTTPException(status_code=403, detail="Children can only read their own sessions")
        return {**session, "school_id": None}
    if role in ("therapist", "admin"):
        return {**session, "school_id": _get_school_filter(session)}
    raise HTTPException(status_code=403, detail="Participant history access denied")


def _get_school_filter(session: dict) -> int | None:
    """Return primary school_id for filtering. Supports multi-school via pivot table."""
    admin_id = session.get("user_id")
    if not admin_id: return None
    from .db import get_admin_school_ids
    school_ids = get_admin_school_ids(admin_id)
    if school_ids:
        # Return the first assigned school as the primary filter for legacy callers.
        # web_service.py functions that accept school_id will filter on this school.
        # For a true multi-school query, see the /players endpoint in auth_routes.py.
        return school_ids[0] if len(school_ids) == 1 else None
    # Fallback: legacy session token school_id
    return session.get("school_id")


@router.get("/api/admin/stats", tags=["admin"])
def admin_stats(request: Request):
    """KPIs for the admin overview dashboard (reads from cache)."""
    session = _require_admin(request)
    return get_admin_stats(school_id=_get_school_filter(session))


@router.post("/api/admin/refresh-cache", tags=["admin"])
async def admin_refresh_cache(request: Request):
    """Force-refresh the dashboard cache. Call after pipeline runs."""
    session = _require_admin(request)
    school_id = _get_school_filter(session)
    import asyncio
    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, lambda: refresh_dashboard_cache(school_id=school_id))
    return {"status": "ok", "total_sessions": stats.get("total_sessions", 0)}



@router.get("/api/admin/population", tags=["admin"])
def admin_population(request: Request):
    """Profile distribution and sessions-over-time for charts."""
    session = _require_admin(request)
    return get_population_data(school_id=_get_school_filter(session))


@router.get("/api/dashboard/participants", tags=["dashboard"])
@router.get("/api/admin/participants", tags=["admin"])
def admin_participants(request: Request, page: int = 1, page_size: int = 20, search: str = ""):
    """Paginated participant list with session counts."""
    session = _require_admin(request)
    return get_all_participants_paginated(
        page=page, page_size=page_size, search=search, school_id=_get_school_filter(session)
    )


@router.get("/api/dashboard/participants/{pid}/timeline", tags=["dashboard"])
@router.get("/api/admin/participants/{pid}/timeline", tags=["admin"])
def admin_participant_timeline(pid: str, request: Request):
    """Session-by-session metrics for a participant's timeline chart."""
    session = _require_admin(request)
    timeline = get_participant_timeline(pid, school_id=_get_school_filter(session))
    if timeline is None:
        raise HTTPException(status_code=404, detail=f"No sessions for '{pid}' or access denied.")
    return {"participant_id": pid, "timeline": timeline}


@router.get("/api/admin/analytics/game/{game_id}", tags=["admin"])
def admin_game_analytics(game_id: str, request: Request):
    """Distribution data for a specific game's analytics."""
    session = _require_admin(request)
    data = get_game_analytics(game_id, school_id=_get_school_filter(session))
    if data is None:
        raise HTTPException(status_code=404, detail=f"No data for game '{game_id}'")
    return data


@router.get("/api/admin/anomalies", tags=["admin"])
def admin_anomalies(request: Request, game: str = "gonogo"):
    """Anomaly detection flags and distributions for a game."""
    session = _require_admin(request)
    data = get_anomaly_data(game, school_id=_get_school_filter(session))
    return data


@router.get("/api/admin/classification", tags=["admin"])
def admin_classification(request: Request, game: str = "gonogo"):
    """Classification results: accuracy, confusion matrix, per-class metrics."""
    session = _require_admin(request)
    data = get_classification_data(game, school_id=_get_school_filter(session))
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No classification data for '{game}'. Run the ML pipeline first.",
        )
    return data


@router.get("/api/dashboard/participants/{pid}/concordance", tags=["dashboard"])
@router.get("/api/admin/participants/{pid}/concordance", tags=["admin"])
def admin_participant_concordance(pid: str, request: Request):
    """Conners CGI-10 concordance with ML classification for a participant."""
    _require_admin(request)
    from .db import get_conners_concordance
    record = get_conners_concordance(pid)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No concordance data for '{pid}'. "
                   "Ensure Conners assessment and ML pipeline have both run.",
        )
    return record


@router.get("/api/admin/concordance", tags=["admin"])
def admin_concordance(request: Request):
    """Population-level Conners–ML concordance stats for the dashboard."""
    session = _require_admin(request)
    school_id = _get_school_filter(session)
    return get_concordance_data(school_id=school_id)


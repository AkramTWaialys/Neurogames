"""
Web service — business logic for the NeuroGames web frontend.

Provides participant summaries, session history, and game metadata
by querying the centralised MLOps PostgreSQL database.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .db import get_db, get_game_module, list_game_modules
from .game_registry import GAMES, GameInfo  # single source of truth for game metadata
from .web_schemas import (
    GameStat,
    ParticipantSummary,
    SessionDetail,
    SessionHistoryResponse,
)


def _json_object(value) -> dict:
    """Return a JSON object from a JSONB dict or a serialized JSON string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}

# ── Canonical game registry ───────────────────────────────────────────────────


def _iso_timestamp(value) -> str | None:
    """Return an ISO timestamp string for DB values returned as datetime or text."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


DEFAULT_MODULE_COLOR = "#64748b"
DEFAULT_MODULE_ICON = "🎮"


def _module_to_game_info(module: dict) -> GameInfo:
    """Build public game metadata for built-in and registered modules."""
    game_id = module.get("game_id") or module.get("slug")
    builtin = GAMES.get(game_id)
    config = module.get("config_json") if isinstance(module.get("config_json"), dict) else {}
    return GameInfo(
        id=game_id,
        name=module.get("display_name") or (builtin.name if builtin else game_id),
        icon=builtin.icon if builtin else DEFAULT_MODULE_ICON,
        cognitive_domain=module.get("cognitive_domain") or (builtin.cognitive_domain if builtin else "ADHD game module"),
        description=module.get("description") or (builtin.description if builtin else "Registered compatible ADHD game module."),
        difficulty_system=builtin.difficulty_system if builtin else "Declared by game module",
        color=builtin.color if builtin else DEFAULT_MODULE_COLOR,
        runtime_type=config.get("runtime_type"),
        entry_url=config.get("entry_url"),
    )


def _game_info_for_id(game_id: str) -> GameInfo | None:
    """Return metadata for a game module, falling back to static built-ins."""
    try:
        module = get_game_module(game_id)
        if module:
            return _module_to_game_info(module)
    except Exception:
        pass
    return GAMES.get(game_id)


def get_all_games() -> list[GameInfo]:
    """Return metadata for all registered games."""
    try:
        modules = list_game_modules(active_only=True)
        if modules:
            return [_module_to_game_info(module) for module in modules]
    except Exception:
        pass
    return list(GAMES.values())


def get_game(game_id: str) -> Optional[GameInfo]:
    """Return metadata for a single game, or None if not found."""
    return _game_info_for_id(game_id)


# ── Session queries (from the MLOps sessions table) ───────────────────────────

def _get_participant_rows(pid: str) -> list[dict]:
    """
    Fetch all sessions for a participant from the MLOps database.

    The MLOps DB stores sessions with a data_json blob. We extract the
    relevant fields from each blob for the web frontend.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, game, participant_id, session_id, data_json, ingested_at
            FROM sessions
            WHERE LOWER(participant_id) = LOWER(%s)
            ORDER BY ingested_at ASC
            """,
            (pid,),
        ).fetchall()

    results = []
    for row in rows:
        data = _json_object(row["data_json"])

        correct = int(data.get("Correct_Responses", 0))
        total = int(data.get("Total_Actions", 0))
        accuracy = (correct / total) if total > 0 else 0.0
        stars = "⭐⭐⭐" if accuracy >= 0.75 else "⭐⭐" if accuracy >= 0.45 else "⭐"

        results.append({
            "id": row["id"],
            "game_id": row["game"],
            "participant_id": row["participant_id"],
            "session_id": row["session_id"],
            "correct": correct,
            "total_actions": total,
            "accuracy": accuracy,
            "level": int(data.get("level", data.get("max_level_reached", 1)) or 1),
            "duration_s": float(data.get("Time_Spent", 0)),
            "stars": stars,
            "stored_at": _iso_timestamp(row["ingested_at"]),
            "reaction_time": data.get("Reaction_Time"),
            "performance_level": data.get("Performance_Level"),
        })
    return results


def _verify_participant_school(pid: str, school_id: int | None) -> bool:
    """Return True if the participant belongs to the given school_id (or if school_id is None)."""
    if school_id is None:
        return True
    with get_db() as conn:
        row = conn.execute(
            "SELECT school_id FROM users WHERE LOWER(username) = LOWER(%s)",
            (pid,)
        ).fetchone()
        return row is not None and row["school_id"] == school_id


def get_participant_sessions(pid: str, school_id: int | None = None) -> Optional[SessionHistoryResponse]:
    """
    Return every individual session for a participant, enriched with
    game metadata (name, icon) and extra fields.
    """
    if not _verify_participant_school(pid, school_id):
        return None

    rows = _get_participant_rows(pid)
    if not rows:
        return None

    details: list[SessionDetail] = []
    for row in rows:
        gid = row["game_id"]
        game_info = _game_info_for_id(gid)
        details.append(SessionDetail(
            id=row["id"],
            game_id=gid,
            game_name=game_info.name if game_info else gid,
            icon=game_info.icon if game_info else "🎮",
            correct=row["correct"],
            total_actions=row["total_actions"],
            accuracy=round(row["accuracy"] * 100, 1),
            level=row["level"],
            duration_s=row["duration_s"],
            stars=row["stars"],
            stored_at=row["stored_at"],
            reaction_time=row.get("reaction_time"),
            performance_level=row.get("performance_level"),
        ))

    return SessionHistoryResponse(
        participant_id=pid,
        sessions=details,
        count=len(details),
    )


def get_participant_summary(pid: str, school_id: int | None = None) -> Optional[ParticipantSummary]:
    """
    Build an aggregated summary for a participant from the MLOps database.
    Case-insensitive participant ID lookup.
    """
    if not _verify_participant_school(pid, school_id):
        return None

    rows = _get_participant_rows(pid)
    if not rows:
        return None

    # ── Per-game aggregation ──────────────────────────────────────────────
    game_stats: dict[str, list[dict]] = {}
    for s in rows:
        gid = s["game_id"]
        game_stats.setdefault(gid, []).append(s)

    games: list[GameStat] = []
    total_correct = 0
    total_actions_sum = 0

    for gid, game_sessions in game_stats.items():
        game_info = _game_info_for_id(gid)
        correct = sum(s["correct"] for s in game_sessions)
        actions = sum(s["total_actions"] for s in game_sessions)
        total_correct += correct
        total_actions_sum += actions
        avg_acc = float((correct / actions * 100) if actions > 0 else 0.0)

        best = max((s["accuracy"] for s in game_sessions), default=0.0)
        max_level = max((s["level"] for s in game_sessions), default=1)
        timestamps = [s["stored_at"] for s in game_sessions if s.get("stored_at")]
        last = max(timestamps, default=None)

        games.append(GameStat(
            game_id=gid,
            game_name=game_info.name if game_info else gid,
            sessions_played=len(game_sessions),
            avg_accuracy=round(avg_acc, 1),
            best_score=round(best * 100, 1) if best else None,
            max_level=max_level,
            last_played=last,
        ))

    overall_acc = float(
        (total_correct / total_actions_sum * 100) if total_actions_sum > 0 else 0.0
    )
    n = len(rows)
    xp = n * 50 + total_correct * 2

    if xp >= 2000:
        level = "Expert"
    elif xp >= 1000:
        level = "Avancé"
    elif xp >= 300:
        level = "Intermédiaire"
    else:
        level = "Débutant"

    return ParticipantSummary(
        participant_id=pid,
        total_sessions=n,
        avg_accuracy=round(float(overall_acc), 1),
        games=games,
        xp=xp,
        level=level,
    )


# ── Admin Dashboard Service Functions ─────────────────────────────────────────


def _dashboard_session_scope(school_id: int | None) -> tuple[str, str, tuple]:
    """Return the indexed join/filter needed for school-scoped queries.

    Global dashboard queries do not need the users table. School-scoped
    queries use sessions.user_id, the authoritative authenticated identity,
    instead of a case-insensitive OR join that compares sessions to all users.
    """
    if school_id is None:
        return "", "", ()
    return "JOIN users u ON u.id = s.user_id", "WHERE u.school_id = %s", (school_id,)


def get_admin_stats(school_id: int | None = None) -> dict:
    """Return cached admin dashboard stats. If school_id is provided, compute dynamically."""
    from .db import read_dashboard_cache

    if school_id is None:
        cached = read_dashboard_cache()
        if cached is not None:
            return cached
        # First boot — seed the cache synchronously
        return refresh_dashboard_cache()
    
    # Per-school query is dynamic (not cached)
    return refresh_dashboard_cache(school_id=school_id)


def refresh_dashboard_cache(school_id: int | None = None) -> dict:
    """Compute all admin dashboard stats.
    If school_id is None, caches globally. Otherwise returns dict dynamically.
    """
    from .db import get_db, write_dashboard_cache

    with get_db() as conn:

        join_sql, where_sql, params = _dashboard_session_scope(school_id)

        # ── KPI totals (SQL-level aggregation, no Python JSON parsing) ────
        total_sessions = conn.execute(f"SELECT COUNT(*) as count FROM sessions s {join_sql} {where_sql}", params).fetchone()["count"]
        total_participants = conn.execute(f"SELECT COUNT(DISTINCT s.participant_id) as count FROM sessions s {join_sql} {where_sql}", params).fetchone()["count"]

        game_rows = conn.execute(f"""
            SELECT s.game,
                   SUM(CAST((s.data_json ->> 'Correct_Responses') AS INTEGER)) AS correct,
                   SUM(CAST((s.data_json ->> 'Total_Actions') AS INTEGER))     AS actions,
                   AVG(CAST((s.data_json ->> 'Reaction_Time') AS DOUBLE PRECISION))        AS avg_rt,
                   COUNT(*) AS cnt
            FROM sessions s
            {join_sql}
            {where_sql}
            GROUP BY s.game
        """, params).fetchall()

        avg_correct_total = sum(r["correct"] or 0 for r in game_rows)
        avg_actions_total = sum(r["actions"] or 0 for r in game_rows)
        avg_accuracy = round(avg_correct_total / max(1, avg_actions_total) * 100, 1)

        rt_values = [r["avg_rt"] for r in game_rows if r["avg_rt"] is not None]
        avg_rt = round(sum(rt_values) / max(1, len(rt_values)), 2) if rt_values else 0.0

        game_accuracies = []
        for r in game_rows:
            gid = r["game"]
            info = _game_info_for_id(gid)
            acc = round((r["correct"] or 0) / max(1, r["actions"] or 0) * 100, 1)
            game_accuracies.append({
                "game_id": gid,
                "game_name": info.name if info else gid,
                "color": info.color if info else "#888",
                "accuracy": acc,
                "sessions": r["cnt"],
            })

        # ── Data freshness ────────────────────────────────────────────────
        newest = conn.execute(
            f"SELECT MAX(s.ingested_at) as latest FROM sessions s {join_sql} {where_sql}", params
        ).fetchone()
        last_updated = newest["latest"]
        if isinstance(last_updated, datetime):
            last_updated = last_updated.isoformat()

        # ── Declining accuracy (batch: 1 query for ALL participants) ──────
        ranked_rows = conn.execute(f"""
            WITH ranked AS (
                SELECT s.participant_id, s.game, s.data_json, s.ingested_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.participant_id
                           ORDER BY s.ingested_at DESC
                       ) AS rn,
                       COUNT(*) OVER (PARTITION BY s.participant_id) AS total_sessions
                FROM sessions s
                {join_sql}
                {where_sql}
            )
            SELECT participant_id, game, data_json, ingested_at, rn, total_sessions
            FROM ranked
            WHERE total_sessions >= 5 AND rn <= 10
            ORDER BY participant_id, rn
        """, params).fetchall()

        # Group by participant in a single pass
        participant_sessions: dict[str, list[dict]] = {}
        for r in ranked_rows:
            pid = r["participant_id"]
            participant_sessions.setdefault(pid, []).append({
                "game": r["game"],
                "data_json": r["data_json"],
                "ingested_at": r["ingested_at"],
                "rn": r["rn"],
            })

        alerts: list[dict] = []
        at_risk: list[dict] = []
        declining = []

        for pid, sessions in participant_sessions.items():
            accs = []
            games_set = set()
            for s in sessions:
                d = _json_object(s["data_json"])
                try:
                    c = int(d.get("Correct_Responses", 0))
                    a = int(d.get("Total_Actions", 0))
                except (TypeError, ValueError):
                    continue
                if a > 0:
                    accs.append(c / a * 100)
                    games_set.add(s["game"])

            if len(accs) < 5:
                continue

            recent_avg = sum(accs[:5]) / 5
            baseline = sum(accs) / len(accs)
            delta = baseline - recent_avg

            if delta > 15:
                declining.append({
                    "participant_id": pid,
                    "delta": round(delta, 1),
                    "recent_avg": round(recent_avg, 1),
                    "games": list(games_set),
                    "last_session": sessions[0]["ingested_at"].isoformat() if isinstance(sessions[0]["ingested_at"], datetime) else sessions[0]["ingested_at"],
                })

        if declining:
            declining.sort(key=lambda x: x["delta"], reverse=True)
            top3 = declining[:3]
            games_affected: dict[str, int] = {}
            for d in declining:
                for g in d["games"]:
                    games_affected[g] = games_affected.get(g, 0) + 1

            top3_names = ", ".join(
                "{} (−{}%)".format(d["participant_id"], d["delta"]) for d in top3
            )
            games_str = ", ".join(
                "{} ({})".format(g, n)
                for g, n in sorted(games_affected.items(), key=lambda x: -x[1])
            )
            alerts.append({
                "severity": "critical",
                "title": f"{len(declining)} participants montrent une précision en baisse (>15% sur les 5 dernières sessions)",
                "detail": f"Risque le plus élevé : {top3_names}. Jeux affectés : {games_str}.",
            })
            for d in declining:
                at_risk.append({
                    "participant_id": d["participant_id"],
                    "anomaly_rate": None,
                    "trend": "−{}%".format(d["delta"]),
                    "last_session": d["last_session"],
                    "reason": "Précision en baisse de {}% sur les dernières sessions".format(d["delta"]),
                })

        # ── High anomaly rate alerts (>25% flags in ≥10 sessions) ─────────
        try:
            anom_sql = f"""
                SELECT af.user_id, COALESCE(u.username, CAST(af.user_id AS TEXT)) as username, af.game, 
                COUNT(*) as total, SUM(af.anomaly_flag::int) as flagged
                FROM anomaly_flags af 
                JOIN users u ON u.id = af.user_id 
                {where_sql}
                GROUP BY af.user_id, u.username, af.game
                HAVING COUNT(*) >= 10 AND CAST(SUM(af.anomaly_flag::int) AS DOUBLE PRECISION)/COUNT(*) > 0.25
                ORDER BY CAST(SUM(af.anomaly_flag::int) AS DOUBLE PRECISION)/COUNT(*) DESC
            """
            anomaly_pids = conn.execute(anom_sql, params).fetchall()

            if anomaly_pids:
                high_anom = []
                games_anom: dict[str, int] = {}
                for r in anomaly_pids:
                    rate = round(r["flagged"] / max(1, r["total"]) * 100, 1)
                    pid_display = r["username"] or str(r["user_id"])
                    high_anom.append({
                        "participant_id": pid_display,
                        "rate": rate,
                        "game": r["game"],
                    })
                    games_anom[r["game"]] = games_anom.get(r["game"], 0) + 1

                top3_anom = high_anom[:3]
                top3_anom_names = ", ".join(
                    "{} ({}%)".format(a["participant_id"], a["rate"]) for a in top3_anom
                )
                games_anom_str = ", ".join(
                    "{} ({})".format(g, n)
                    for g, n in sorted(games_anom.items(), key=lambda x: -x[1])
                )
                alerts.append({
                    "severity": "critical",
                    "title": f"{len(high_anom)} participants avec un taux d'anomalies élevé (>25% sur ≥10 sessions)",
                    "detail": f"Risque : {top3_anom_names}. Jeux : {games_anom_str}.",
                })
                for a in high_anom:
                    existing = [r for r in at_risk if r["participant_id"] == a["participant_id"]]
                    if existing:
                        existing[0]["anomaly_rate"] = "{}%".format(a["rate"])
                    else:
                        at_risk.append({
                            "participant_id": a["participant_id"],
                            "anomaly_rate": "{}%".format(a["rate"]),
                            "trend": None,
                            "last_session": None,
                            "reason": "Taux d'anomalies de {}% ({})".format(a["rate"], a["game"]),
                        })
        except Exception:
            pass  # anomaly_flags table may not exist yet


        # ── Stale data alert (>7 days no new sessions for a game) ─────────
        stale_games = []
        try:
            game_freshness = conn.execute(f"""
                SELECT s.game, MAX(s.ingested_at) as latest 
                FROM sessions s 
                {join_sql} 
                {where_sql} 
                GROUP BY s.game
            """, params).fetchall()
            now = datetime.now(timezone.utc)
            for gf in game_freshness:
                if gf["latest"]:
                    try:
                        ts = gf["latest"] if isinstance(gf["latest"], datetime) else datetime.fromisoformat(str(gf["latest"]).replace("Z", "+00:00"))
                        if (now - ts).days > 7:
                            stale_games.append(gf["game"])
                    except Exception:
                        pass
        except Exception:
            pass

        if stale_games:
            alerts.append({
                "severity": "warning",
                "title": f"{len(stale_games)} jeux sans nouvelles sessions depuis >7 jours",
                "detail": f"Jeux concernés : {', '.join(stale_games)}. Vérifiez le pipeline d'ingestion.",
            })

    # ── Assemble the full stats payload ───────────────────────────────────
    stats = {
        "total_sessions": total_sessions,
        "total_participants": total_participants,
        "avg_accuracy": avg_accuracy,
        "avg_reaction_time": avg_rt,
        "per_game": sorted(game_accuracies, key=lambda x: x["game_id"]),
        "last_updated": last_updated,
        "alerts": alerts,
        "at_risk_participants": at_risk[:10],
    }

    # Write to cache ONLY if global query
    if school_id is None:
        write_dashboard_cache(stats)
    return stats


def get_population_data(school_id: int | None = None) -> dict:
    """Return ADHD profile distribution and sessions-over-time for charts."""
    with get_db() as conn:
        join_sql, where_sql, params = _dashboard_session_scope(school_id)
        
        rows = conn.execute(f"SELECT s.game, s.data_json, s.ingested_at FROM sessions s {join_sql} {where_sql}", params).fetchall()

    # Profile distribution
    profile_counts: dict[str, int] = {}
    daily_counts: dict[str, int] = {}

    for row in rows:
        data = _json_object(row["data_json"])

        cluster = data.get("cluster", "Unknown")
        profile_counts[cluster] = profile_counts.get(cluster, 0) + 1

        # Daily session counts
        ts = row["ingested_at"] or ""
        day = ts.date().isoformat() if isinstance(ts, datetime) else str(ts)[:10]  # YYYY-MM-DD
        if day:
            daily_counts[day] = daily_counts.get(day, 0) + 1

    profiles = [{"profile": k, "count": v} for k, v in sorted(profile_counts.items())]

    # Sort daily counts and take last 30 days
    daily = [{"date": k, "sessions": v} for k, v in sorted(daily_counts.items())]
    daily = daily[-30:] if len(daily) > 30 else daily

    return {"profiles": profiles, "daily_sessions": daily}


def get_all_participants_paginated(
    page: int = 1, page_size: int = 20, search: str = "", school_id: int | None = None
) -> dict:
    """Return a paginated list of participants with session counts."""
    with get_db() as conn:
        join_sql, scope_where, scope_params = _dashboard_session_scope(school_id)
        where_sql = scope_where or "WHERE 1=1"
        params = list(scope_params)

        if search:
            count_row = conn.execute(
                f"SELECT COUNT(DISTINCT s.participant_id) as count FROM sessions s {join_sql} {where_sql} AND LOWER(s.participant_id) LIKE LOWER(%s)",
                params + [f"%{search}%"],
            ).fetchone()
            total = count_row["count"] if count_row else 0

            rows = conn.execute(
                f"""
                SELECT s.participant_id,
                       COUNT(*) as session_count,
                       MAX(s.ingested_at) as last_active
                FROM sessions s
                {join_sql}
                {where_sql} AND LOWER(s.participant_id) LIKE LOWER(%s)
                GROUP BY s.participant_id
                ORDER BY session_count DESC
                LIMIT %s OFFSET %s
                """,
                params + [f"%{search}%", page_size, (page - 1) * page_size],
            ).fetchall()
        else:
            count_row = conn.execute(
                f"SELECT COUNT(DISTINCT s.participant_id) as count FROM sessions s {join_sql} {where_sql}", params
            ).fetchone()
            total = count_row["count"] if count_row else 0

            rows = conn.execute(
                f"""
                SELECT s.participant_id,
                       COUNT(*) as session_count,
                       MAX(s.ingested_at) as last_active
                FROM sessions s
                {join_sql}
                {where_sql}
                GROUP BY s.participant_id
                ORDER BY session_count DESC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, (page - 1) * page_size],
            ).fetchall()

    # Calculate accuracy for each participant
    participants = []
    for row in rows:
        pid = row["participant_id"]
        sess_rows = conn.execute(
            "SELECT data_json FROM sessions WHERE participant_id = %s", (pid,)
        ).fetchall() if False else []  # Skip for performance — we'll calculate inline

        participants.append({
            "participant_id": pid,
            "session_count": row["session_count"],
            "last_active": row["last_active"],
        })

    return {
        "participants": participants,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


def get_participant_timeline(pid: str, school_id: int | None = None) -> list[dict] | None:
    """Return session-by-session metrics for timeline charts."""
    if school_id is not None:
        with get_db() as conn:
            user = conn.execute(
                "SELECT school_id FROM users WHERE LOWER(username) = LOWER(%s) OR id = %s",
                (pid, pid if str(pid).isdigit() else None)
            ).fetchone()
            if not user or user["school_id"] != school_id:
                return None

    rows = _get_participant_rows(pid)
    if not rows:
        return None

    timeline = []
    for i, row in enumerate(rows, 1):
        game_info = _game_info_for_id(row["game_id"])
        timeline.append({
            "session_number": i,
            "game_id": row["game_id"],
            "game_name": game_info.name if game_info else row["game_id"],
            "accuracy": round(row["accuracy"] * 100, 1),
            "reaction_time": row.get("reaction_time"),
            "level": row["level"],
            "duration_s": row["duration_s"],
            "stored_at": row["stored_at"],
        })
    return timeline


def get_game_analytics(game_id: str, school_id: int | None = None) -> dict | None:
    """Return distribution data for a specific game's analytics tab."""
    game_info = _game_info_for_id(game_id)
    if game_info is None:
        return None

    with get_db() as conn:
        join_sql = "LEFT JOIN users u ON LOWER(u.username) = LOWER(s.participant_id) OR u.id = s.user_id"
        where_sql = "WHERE (u.school_id = %s) AND s.game = %s" if school_id is not None else "WHERE s.game = %s"
        params: list = [school_id, game_id] if school_id is not None else [game_id]

        rows = conn.execute(
            f"SELECT s.data_json FROM sessions s {join_sql} {where_sql}", params
        ).fetchall()

    if not rows:
        return None

    accuracies = []
    reaction_times = []
    profiles: dict[str, list[float]] = {}

    for row in rows:
        data = _json_object(row["data_json"])

        c = int(data.get("Correct_Responses", 0))
        a = int(data.get("Total_Actions", 0))
        acc = c / max(1, a) * 100
        accuracies.append(acc)

        rt = data.get("Reaction_Time")
        if rt is not None:
            reaction_times.append(float(rt))

        cluster = data.get("cluster", "Unknown")
        profiles.setdefault(cluster, []).append(acc)

    return {
        "game_id": game_id,
        "game_name": game_info.name,
        "total_sessions": len(rows),
        "accuracies": accuracies,
        "reaction_times": reaction_times,
        "profile_accuracies": {k: v for k, v in sorted(profiles.items())},
    }


# ── Anomaly Detection Service ────────────────────────────────────────────────


def get_anomaly_data(game_id: str, school_id: int | None = None) -> dict | None:
    """Query anomaly flags from PostgreSQL for a game.

    Returns summary KPIs, profile rates, score arrays, and top flagged
    participants — the same shape expected by the frontend.
    """
    game_info = _game_info_for_id(game_id)
    if game_info is None:
        return None

    from .db import get_anomaly_summary

    summary = get_anomaly_summary(game_id, school_id)

    return {
        "game_id": game_id,
        "game_name": game_info.name,
        **summary,
    }


# ── Classification Service ───────────────────────────────────────────────────


def get_classification_data(game_id: str, school_id: int | None = None) -> dict | None:
    """Query classification results from PostgreSQL for a game.

    Returns accuracy, confusion matrix, per-class metrics, and
    misclassified participants.
    """
    game_info = _game_info_for_id(game_id)
    if game_info is None:
        return None

    from .db import get_classification_summary

    summary = get_classification_summary(game_id, school_id)
    if summary is None or summary.get("total", 0) == 0:
        return None

    return {
        "game_id": game_id,
        "game_name": game_info.name,
        **summary,
    }


# ── Concordance Service ──────────────────────────────────────────────────────


def get_concordance_data(school_id: int | None = None) -> dict:
    """Return population-level concordance stats for the admin dashboard.

    Returns KPIs, per-participant rows, and aggregate metrics.
    """
    with get_db() as conn:
        if school_id is not None:
            rows = conn.execute("""
                SELECT cc.*, u.username AS participant_id
                FROM conners_concordance cc
                JOIN users u ON u.id = cc.user_id
                WHERE u.school_id = %s
                ORDER BY cc.concordance ASC, cc.adjusted_confidence ASC
            """, (school_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT cc.*, u.username AS participant_id
                FROM conners_concordance cc
                JOIN users u ON u.id = cc.user_id
                ORDER BY cc.concordance ASC, cc.adjusted_confidence ASC
            """).fetchall()

    records = [dict(r) for r in rows]
    n = len(records)

    if n == 0:
        return {
            "total_matched": 0,
            "n_concordant": 0,
            "n_partial": 0,
            "n_discordant": 0,
            "concordance_rate": 0,
            "exact_match_rate": 0,
            "avg_adjusted_confidence": 0,
            "participants": [],
        }

    n_concordant = sum(1 for r in records if r.get("concordance") == "concordant")
    n_partial = sum(1 for r in records if r.get("concordance") == "partial")
    n_discordant = sum(1 for r in records if r.get("concordance") == "discordant")

    concordance_rate = round((n_concordant + n_partial) / n, 4)
    exact_rate = round(n_concordant / n, 4)

    adj_confs = [r["adjusted_confidence"] for r in records if r.get("adjusted_confidence") is not None]
    avg_adj = round(sum(adj_confs) / max(1, len(adj_confs)), 4) if adj_confs else 0

    return {
        "total_matched": n,
        "n_concordant": n_concordant,
        "n_partial": n_partial,
        "n_discordant": n_discordant,
        "concordance_rate": concordance_rate,
        "exact_match_rate": exact_rate,
        "avg_adjusted_confidence": avg_adj,
        "participants": records,
    }

import os
import json
import logging
import threading
import atexit
import hashlib
import base64
import binascii
import re
import secrets
import shutil
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Generator, Optional, Any, Union, List, Dict
import pandas as pd
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import DATABASE_URL, GAME_NAMES, GAME_DISPLAY_NAMES, GAME_DOMAINS
from .schemas import CORE_TELEMETRY_FIELDS

log = logging.getLogger(__name__)

HINT_METRIC_GAMES = {"puzzle"}

GAME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
GAME_INTEGRATION_MODES = {"builtin", "manual", "external_telemetry"}
GAME_MODULE_STATUSES = {"draft", "active", "paused", "archived"}

GAME_ARTIFACT_ROOT = Path(os.getenv("GAME_ARTIFACT_ROOT", "data/game_artifacts")).resolve()
REQUEST_ARTIFACT_DIR = GAME_ARTIFACT_ROOT / "requests"
PUBLISHED_GAME_DIR = GAME_ARTIFACT_ROOT / "published"
PUBLISHED_GAME_URL_PREFIX = "/game-packages"
MAX_GAME_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_PUBLISHED_GAME_BYTES = 15 * 1024 * 1024
SAFE_GAME_ARTIFACT_EXTENSIONS = {
    ".css",
    ".gif",
    ".html",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".mp3",
    ".ogg",
    ".png",
    ".svg",
    ".ttf",
    ".txt",
    ".wav",
    ".webmanifest",
    ".webp",
    ".woff",
    ".woff2",
}

REFERENCE_LABEL_SCHEMA = {
    "type": "profile_categories",
    "labels": [
        "Combined ADHD",
        "Hyperactive-Impulsive ADHD",
        "Inattentive ADHD",
        "Optimal / Neurotypical",
    ],
}

# Connection pool initialized on first use
_pool = None
_local = threading.local()
_initialized = False


def close_pool() -> None:
    """Close the global PostgreSQL connection pool, if it was opened."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


atexit.register(close_pool)


def get_pool():
    """Return the global PostgreSQL connection pool."""
    global _pool
    if _pool is None:
        log.info(f"Initializing PostgreSQL pool for {DATABASE_URL.split('@')[-1]}")
        _pool = ConnectionPool(DATABASE_URL, min_size=2, max_size=20)
    return _pool


class PostgresCursor:
    """Compatibility wrapper for legacy DB-API-style call sites."""
    def __init__(self, pg_cursor):
        self.pg_cursor = pg_cursor
        self._lastrowid = None

    def execute(self, query, params=None):
        # Log slow queries or specific ops if needed
        self.pg_cursor.execute(query, params)

        # Handle lastrowid compatibility (requires RETURNING id in the SQL)
        if "RETURNING" in query.upper() and self.pg_cursor.description:
            try:
                row = self.pg_cursor.fetchone()
                if row:
                    # In dict_row, we look for 'id' or just take the first value
                    self._lastrowid = row.get("id", list(row.values())[0])
            except Exception:
                pass
        return self

    def executemany(self, query, params_list):
        self.pg_cursor.executemany(query, params_list)
        return self

    def fetchone(self):
        return self.pg_cursor.fetchone()

    def fetchall(self):
        return self.pg_cursor.fetchall()

    @property
    def description(self):
        return self.pg_cursor.description

    def close(self):
        self.pg_cursor.close()

    @property
    def lastrowid(self):
        return self._lastrowid

    @property
    def rowcount(self):
        return self.pg_cursor.rowcount

    def __iter__(self):
        return iter(self.pg_cursor)


class PostgresConnection:
    """Compatibility wrapper for legacy DB-API-style call sites."""
    def __init__(self, pg_conn):
        self.pg_conn = pg_conn

    def cursor(self):
        return PostgresCursor(self.pg_conn.cursor(row_factory=dict_row))

    def execute(self, query: str, params: Any = None):
        try:
            return self.cursor().execute(query, params)
        except Exception as e:
            log.error(f"SQL FAILED: {query}")
            log.error(f"PARAMS: {params}")
            log.error(f"ERROR: {e}")
            raise

    def executemany(self, query, params_list):
        return self.cursor().executemany(query, params_list)

    def commit(self):
        self.pg_conn.commit()

    def rollback(self):
        self.pg_conn.rollback()

    def total_changes(self):
        # Approximation for total changes in current transaction
        return self.pg_conn.info.transaction_status

    @property
    def row_factory(self): return None
    @row_factory.setter
    def row_factory(self, v): pass


def _get_connection() -> Optional[PostgresConnection]:
    """Return the active thread-local PostgreSQL connection, if any."""
    if hasattr(_local, "conn") and _local.conn is not None:
        return _local.conn
    return None


@contextmanager
def get_db():
    """
    Context manager yielding a PostgreSQL connection with nesting support.

    Reuses the existing thread-local connection if one is active (incrementing
    a depth counter) and only returns it to the pool when the outermost
    context exits.
    """
    # Initialize thread-local depth if missing
    if not hasattr(_local, "depth"):
        _local.depth = 0

    _local.depth += 1
    conn_wrapper = _get_connection()

    is_outermost = False
    if conn_wrapper is None:
        is_outermost = True
        raw_conn = get_pool().getconn()
        conn_wrapper = PostgresConnection(raw_conn)
        _local.conn = conn_wrapper

    try:
        yield conn_wrapper
        # Only commit at the outermost level
        if is_outermost:
            conn_wrapper.commit()
    except Exception:
        # Rollback on any failure
        if conn_wrapper:
            conn_wrapper.rollback()
        raise
    finally:
        _local.depth -= 1
        # Only release back to pool when nesting depth returns to zero
        if _local.depth <= 0:
            if is_outermost and _local.conn:
                get_pool().putconn(_local.conn.pg_conn)
            _local.conn = None
            _local.depth = 0


def _seed_games(conn: PostgresConnection) -> None:
    """Seed the games registry table from config constants."""
    now = datetime.now(timezone.utc).isoformat()
    for slug in GAME_NAMES:
        conn.execute(
            """
            INSERT INTO games
                (slug, display_name, domain, is_active, sort_order, created_at)
            VALUES (%s, %s, %s, TRUE, %s, %s)
            ON CONFLICT (slug) DO NOTHING
            """,
            (
                slug,
                GAME_DISPLAY_NAMES.get(slug, slug.capitalize()),
                GAME_DOMAINS.get(slug, "Unknown"),
                GAME_NAMES.index(slug),
                now,
            ),
        )


def _default_game_feature_set(slug: str) -> list[str]:
    """Return the declared ML feature extension set for a built-in game."""
    try:
        from multigame_pipeline.preprocessor import GAME_EXTRA_FEATURES
        return list(GAME_EXTRA_FEATURES.get(slug, []))
    except Exception:
        return []


def _seed_game_metadata(conn: PostgresConnection) -> None:
    """Seed the five built-in ADHD games as reference game modules."""
    now = _now_iso()
    for slug in GAME_NAMES:
        conn.execute(
            """
            INSERT INTO game_metadata
                (game_id, integration_mode, feature_set, label_schema,
                 schema_version, ml_enabled, included_in_cross_game,
                 status, created_at, updated_at)
            VALUES (%s, 'builtin', %s, %s, '1.0', TRUE, TRUE, 'active', %s, %s)
            ON CONFLICT (game_id) DO UPDATE SET
                integration_mode = 'builtin',
                feature_set = EXCLUDED.feature_set,
                label_schema = EXCLUDED.label_schema,
                schema_version = EXCLUDED.schema_version,
                ml_enabled = TRUE,
                included_in_cross_game = TRUE,
                status = 'active',
                updated_at = EXCLUDED.updated_at
            """,
            (
                slug,
                json.dumps(_default_game_feature_set(slug)),
                json.dumps(REFERENCE_LABEL_SCHEMA),
                now,
                now,
            ),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _column_exists(
    conn: PostgresConnection,
    table_name: str,
    column_name: str,
    table_schema: str = "public",
) -> bool:
    row = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s AND column_name=%s
        """,
        (table_schema, table_name, column_name),
    ).fetchone()
    return bool(row)


def _migrate_session_metadata_columns(conn: PostgresConnection) -> None:
    """Add game-module metadata columns to existing installations."""
    columns = [
        ("protocol_id", "TEXT"),
        ("schema_version", "TEXT"),
        ("core_json", "JSONB"),
        ("extension_json", "JSONB"),
        ("raw_events_json", "JSONB"),
        ("ml_eligible", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ]
    for column_name, column_type in columns:
        if not _column_exists(conn, "sessions", column_name):
            log.info("Migrating sessions table: adding %s column", column_name)
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {column_name} {column_type}")

    conn.execute("UPDATE sessions SET protocol_id = game WHERE protocol_id IS NULL")
    conn.execute(
        """
        UPDATE sessions
        SET schema_version = COALESCE(data_json->>'schema_version', '1.0')
        WHERE schema_version IS NULL
        """
    )
    for slug in GAME_NAMES:
        conn.execute("UPDATE sessions SET ml_eligible = TRUE WHERE game = %s", (slug,))
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_game_metadata "
        "ON sessions (protocol_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_ml_eligible "
        "ON sessions (game, ml_eligible)"
    )


def _migrate_schools(conn: PostgresConnection) -> None:
    """Add school_id column to users if missing."""
    # In PostgreSQL, we check information_schema
    res = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='school_id'"
    ).fetchone()
    if not res:
        log.info("Migrating users table: adding school_id column")
        conn.execute("ALTER TABLE users ADD COLUMN school_id INTEGER REFERENCES schools(id)")


def _migrate_user_profiles(conn: PostgresConnection) -> None:
    """Add custom_school_name, conners_score, and conners_data columns if missing."""
    columns = [
        ("custom_school_name", "TEXT"),
        ("conners_score", "INTEGER"),
        ("conners_data", "JSONB"),
    ]
    for col_name, col_type in columns:
        res = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='user_profiles' AND column_name=%s",
            (col_name,)
        ).fetchone()
        if not res:
            log.info(f"Migrating user_profiles table: adding {col_name} column")
            conn.execute(f"ALTER TABLE user_profiles ADD COLUMN {col_name} {col_type}")


def _migrate_user_contact_columns(conn: PostgresConnection) -> None:
    """Add optional contact fields used by child/developer registration."""
    columns = [
        ("email", "TEXT"),
        ("phone", "TEXT"),
    ]
    for col_name, col_type in columns:
        res = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name=%s",
            (col_name,),
        ).fetchone()
        if not res:
            log.info("Migrating users table: adding %s column", col_name)
            conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")


def _migrate_developer_game_request_artifact_columns(conn: PostgresConnection) -> None:
    """Add optional static game-package artifact fields to developer requests."""
    columns = [
        ("code_artifact_path", "TEXT"),
        ("code_artifact_filename", "TEXT"),
        ("code_artifact_size", "INTEGER"),
        ("published_entry_url", "TEXT"),
    ]
    for col_name, col_type in columns:
        if not _column_exists(conn, "developer_game_requests", col_name):
            log.info("Migrating developer_game_requests table: adding %s column", col_name)
            conn.execute(f"ALTER TABLE developer_game_requests ADD COLUMN {col_name} {col_type}")


def _migrate_admin_school_assignments(conn: PostgresConnection) -> None:
    """Create admin_school_assignments pivot table if missing and migrate existing data."""
    # Create pivot table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_school_assignments (
            admin_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            school_id   INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            created_at  TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (admin_id, school_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_school_admin "
        "ON admin_school_assignments (admin_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_school_school "
        "ON admin_school_assignments (school_id)"
    )

    # Migrate existing single school_id assignments for therapist/super_admin users.
    # Legacy "admin" is included for databases that have not yet run the role migration.
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT id, school_id FROM users WHERE role IN ('therapist', 'admin', 'super_admin') AND school_id IS NOT NULL"
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO admin_school_assignments (admin_id, school_id, created_at) VALUES (%s, %s, %s) ON CONFLICT (admin_id, school_id) DO NOTHING",
            (row["id"], row["school_id"], now)
        )


def _migrate_admin_role_to_therapist(conn: PostgresConnection) -> None:
    """Rename the legacy school-scoped admin role to therapist."""
    conn.execute("UPDATE users SET role = 'therapist' WHERE role = 'admin'")


def _seed_others_school(conn: PostgresConnection) -> None:
    """Ensure the protected 'Others' institution (id=1) exists."""
    now = datetime.now(timezone.utc).isoformat()
    # In PG, we use the serial sequence or just insert with ID if it's allowed
    conn.execute(
        "INSERT INTO schools (id, name, created_at, created_by) VALUES (1, 'Others', %s, NULL) "
        "ON CONFLICT (id) DO UPDATE SET name = 'Others'",
        (now,)
    )
    conn.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('schools', 'id'),
            GREATEST((SELECT MAX(id) FROM schools), 1),
            true
        )
        """
    )


def _migrate_others_players(conn: PostgresConnection) -> None:
    """Assign school_id=1 to players who chose 'Others' (have custom_school_name but NULL school_id)."""
    conn.execute(
        """
        UPDATE users
        SET school_id = 1
        WHERE role = 'child'
          AND school_id IS NULL
          AND id IN (SELECT user_id FROM user_profiles WHERE custom_school_name IS NOT NULL)
        """
    )


def init_db() -> None:
    """Create all tables if they don't already exist.

    Schema design:
        - ``schools`` is the multi-tenancy anchor: each admin and child
          belongs to exactly one school.
        - ``games`` is the registry of all valid game types (slug PK).
        - All child tables reference ``games(slug)`` via FK on their
          ``game`` column.
        - Derived analytics tables (``baselines``, ``anomaly_flags``,
          ``classification_results``, ``report_runs``, ``behavioral_events``)
          use ``user_id INTEGER REFERENCES users(id)`` as the player key.
        - ``sessions`` keeps both ``participant_id TEXT`` (for upload compat)
          and ``user_id INTEGER`` (authoritative FK to ``users``).
    """
    with get_db() as conn:
        # ── Schools registry ─────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schools (
                id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                name        TEXT    NOT NULL UNIQUE,
                created_at  TIMESTAMPTZ NOT NULL,
                created_by  INTEGER
            )
        """)

        # ── Games registry ───────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                slug         TEXT    PRIMARY KEY,
                display_name TEXT    NOT NULL,
                domain       TEXT    NOT NULL DEFAULT '',
                description  TEXT,
                config_json  JSONB,
                is_active    BOOLEAN NOT NULL DEFAULT TRUE,
                sort_order   INTEGER NOT NULL DEFAULT 0,
                created_at   TIMESTAMPTZ NOT NULL
            )
        """)
        _seed_games(conn)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS game_metadata (
                game_id                TEXT PRIMARY KEY REFERENCES games(slug) ON DELETE CASCADE,
                integration_mode       TEXT NOT NULL DEFAULT 'manual',
                feature_set            JSONB NOT NULL DEFAULT '[]'::jsonb,
                label_schema           JSONB NOT NULL DEFAULT '{}'::jsonb,
                schema_version         TEXT NOT NULL DEFAULT '1.0',
                ml_enabled             BOOLEAN NOT NULL DEFAULT FALSE,
                included_in_cross_game BOOLEAN NOT NULL DEFAULT FALSE,
                status                 TEXT NOT NULL DEFAULT 'draft',
                created_at             TIMESTAMPTZ NOT NULL,
                updated_at             TIMESTAMPTZ NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_game_metadata_ml_enabled "
            "ON game_metadata (ml_enabled)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_game_metadata_cross_game "
            "ON game_metadata (included_in_cross_game)"
        )
        _seed_game_metadata(conn)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS game_ingestion_keys (
                id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                game_id     TEXT NOT NULL REFERENCES games(slug) ON DELETE CASCADE,
                key_prefix  TEXT NOT NULL,
                key_hash    TEXT NOT NULL UNIQUE,
                label       TEXT,
                created_by  INTEGER,
                created_at  TIMESTAMPTZ NOT NULL,
                revoked_at  TIMESTAMPTZ
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_game_ingestion_keys_game "
            "ON game_ingestion_keys (game_id, revoked_at)"
        )

        # ── Users ────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                username      TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                role          TEXT    NOT NULL DEFAULT 'child',
                email         TEXT,
                phone         TEXT,
                school_id     INTEGER REFERENCES schools(id),
                created_at    TIMESTAMPTZ NOT NULL,
                last_login_at TIMESTAMPTZ
            )
        """)
        # PG handles case-insensitive search via ILIKE or functional index
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_username_lower "
            "ON users (LOWER(username))"
        )

        # ── User profiles ────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                display_name    TEXT,
                avatar          TEXT,
                age             INTEGER,
                age_group       TEXT,
                cognitive_level TEXT,
                cluster         TEXT,
                locale_pref     TEXT,
                custom_school_name TEXT,
                conners_score   INTEGER,
                conners_data    JSONB
            )
        """)

        # ── Auth sessions ────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token       TEXT    NOT NULL UNIQUE,
                remember    BOOLEAN NOT NULL DEFAULT FALSE,
                created_at  TIMESTAMPTZ NOT NULL,
                expires_at  TIMESTAMPTZ NOT NULL,
                revoked_at  TIMESTAMPTZ,
                ip_address  TEXT,
                user_agent  TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_sessions_token "
            "ON auth_sessions (token)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user "
            "ON auth_sessions (user_id)"
        )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS developer_game_requests (
                id                    INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                developer_user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                game_id               TEXT NOT NULL,
                display_name          TEXT NOT NULL,
                cognitive_domain      TEXT NOT NULL,
                description           TEXT,
                integration_mode      TEXT NOT NULL DEFAULT 'manual',
                feature_set           JSONB NOT NULL DEFAULT '[]'::jsonb,
                code_repository_url   TEXT,
                code_summary          TEXT,
                code_artifact_path    TEXT,
                code_artifact_filename TEXT,
                code_artifact_size    INTEGER,
                telemetry_schema_json JSONB,
                sample_payload_json   JSONB,
                status                TEXT NOT NULL DEFAULT 'pending',
                reviewer_user_id      INTEGER REFERENCES users(id),
                review_notes          TEXT,
                created_game_id       TEXT,
                published_entry_url   TEXT,
                created_at            TIMESTAMPTZ NOT NULL,
                updated_at            TIMESTAMPTZ NOT NULL,
                decided_at            TIMESTAMPTZ
            )
        """)
        _migrate_developer_game_request_artifact_columns(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_developer_game_requests_status "
            "ON developer_game_requests (status, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_developer_game_requests_developer "
            "ON developer_game_requests (developer_user_id, created_at DESC)"
        )

        # ── Core sessions table ──────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id             INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                game           TEXT    NOT NULL REFERENCES games(slug),
                user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
                participant_id TEXT    NOT NULL,
                session_id     TEXT    NOT NULL UNIQUE,
                data_json      JSONB   NOT NULL,
                protocol_id    TEXT,
                schema_version TEXT,
                core_json      JSONB,
                extension_json JSONB,
                raw_events_json JSONB,
                ml_eligible    BOOLEAN NOT NULL DEFAULT FALSE,
                ingested_at    TIMESTAMPTZ NOT NULL
            )
        """)
        _migrate_session_metadata_columns(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_game "
            "ON sessions (game)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user_ingested "
            "ON sessions (user_id, ingested_at DESC)"
        )

        # ── Training runs ────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS training_runs (
                id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                session_id  TEXT    NOT NULL,
                game        TEXT    NOT NULL REFERENCES games(slug),
                run_id      TEXT    NOT NULL,
                trained_at  TIMESTAMPTZ NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'completed',
                UNIQUE(session_id, game, run_id)
            )
        """)

        # ── Drift checks ─────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS drift_checks (
                id             INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                game           TEXT    NOT NULL REFERENCES games(slug),
                session_id     TEXT,
                run_id         TEXT,
                checked_at     TIMESTAMPTZ NOT NULL,
                status         TEXT    NOT NULL DEFAULT 'completed',
                n_sessions     INTEGER NOT NULL DEFAULT 0,
                drift_detected BOOLEAN NOT NULL DEFAULT FALSE,
                drift_share    DOUBLE PRECISION
            )
        """)

        # ── Drift history ────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS drift_history (
                id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                game        TEXT    NOT NULL REFERENCES games(slug),
                feature     TEXT    NOT NULL,
                drift_type  TEXT    NOT NULL,
                drifted     BOOLEAN NOT NULL DEFAULT FALSE,
                stat_name   TEXT,
                p_value     DOUBLE PRECISION,
                effect_size DOUBLE PRECISION,
                run_id      TEXT,
                checked_at  TIMESTAMPTZ NOT NULL
            )
        """)

        # ── Report runs ──────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS report_runs (
                id                       INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                user_id                  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                run_id                   TEXT,
                generated_at             TIMESTAMPTZ NOT NULL,
                status                   TEXT    NOT NULL DEFAULT 'completed',
                report_path              TEXT,
                report_group_id          TEXT,
                locale                   TEXT,
                snapshot_until           TIMESTAMPTZ,
                session_count_at_snapshot INTEGER,
                is_canonical             BOOLEAN NOT NULL DEFAULT FALSE
            )
        """)

        # ── Baseline snapshots ───────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS baselines (
                id             INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                game           TEXT    NOT NULL REFERENCES games(slug),
                user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                metrics_json   TEXT    NOT NULL,
                n_sessions     INTEGER NOT NULL,
                created_at     TEXT    NOT NULL,
                UNIQUE(game, user_id)
            )
        """)

        # ── Anomaly detection flags ───────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS anomaly_flags (
                id             INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                game           TEXT    NOT NULL REFERENCES games(slug),
                user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                session_id     TEXT    NOT NULL,
                cluster        TEXT,
                if_score       DOUBLE PRECISION,
                ae_error       DOUBLE PRECISION,
                if_flag        BOOLEAN NOT NULL DEFAULT FALSE,
                ae_flag        BOOLEAN NOT NULL DEFAULT FALSE,
                anomaly_flag   BOOLEAN NOT NULL DEFAULT FALSE,
                pipeline_run_id TEXT,
                flagged_at     TIMESTAMPTZ NOT NULL,
                UNIQUE(game, session_id)
            )
        """)

        # ── Classification results ────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS classification_results (
                id                INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                game              TEXT    NOT NULL REFERENCES games(slug),
                user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                actual_cluster    TEXT,
                predicted_cluster TEXT,
                confidence        DOUBLE PRECISION,
                pipeline_run_id   TEXT,
                classified_at     TIMESTAMPTZ NOT NULL,
                UNIQUE(game, user_id)
            )
        """)

        # ── Pre-computed dashboard cache (single-row table) ───────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_cache (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                stats_json JSONB   NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
        """)

        # ── Behavioral events (raw event logging) ─────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS behavioral_events (
                id              INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                game            TEXT    NOT NULL REFERENCES games(slug),
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                session_id      TEXT    NOT NULL,
                event_type      TEXT    NOT NULL,
                event_timestamp DOUBLE PRECISION NOT NULL,
                event_data      JSONB,
                created_at      TIMESTAMPTZ NOT NULL
            )
        """)

        # ── Cross-game results (composite predictions) ─────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cross_game_results (
                id              INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                cluster         TEXT,
                majority_pred   TEXT,
                agreement_ratio DOUBLE PRECISION,
                all_agree       BOOLEAN NOT NULL DEFAULT FALSE,
                predictions_json JSONB   NOT NULL,
                pipeline_run_id  TEXT,
                computed_at     TIMESTAMPTZ NOT NULL,
                UNIQUE(user_id)
            )
        """)

        # ── Conners concordance results ──────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conners_concordance (
                user_id              INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                conners_score        INTEGER,
                conners_tscore       DOUBLE PRECISION,
                conners_tier         TEXT,
                ml_prediction        TEXT,
                agreement_ratio      DOUBLE PRECISION,
                concordance          TEXT,
                concordance_detail   TEXT,
                original_confidence  DOUBLE PRECISION,
                adjusted_confidence  DOUBLE PRECISION,
                correction_flag      TEXT,
                computed_at          TIMESTAMPTZ NOT NULL
            )
        """)

        # ── Audit log (admin action trail) ────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                user_id     INTEGER,
                username    TEXT,
                action      TEXT    NOT NULL,
                target_type TEXT,
                target_id   TEXT,
                detail      TEXT,
                ip_address  TEXT,
                created_at  TIMESTAMPTZ NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_log_created "
            "ON audit_log (created_at DESC)"
        )

        # ── Backward-compatible migration for existing databases ──────
        _migrate_schools(conn)
        _migrate_user_contact_columns(conn)
        _migrate_user_profiles(conn)
        _migrate_admin_role_to_therapist(conn)
        _migrate_admin_school_assignments(conn)
        _seed_others_school(conn)
        _migrate_others_players(conn)

# ── Write ─────────────────────────────────────────────────────────────────────

def _coerce_session_user_id(session_dict: dict) -> int | None:
    """Extract an internal user id from a session dict when one is available."""
    raw = session_dict.get("_user_id", session_dict.get("user_id"))
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _session_data_json(session_dict: dict) -> str:
    """Serialize uploaded session data without private DB-only fields."""
    payload = {k: v for k, v in session_dict.items() if not str(k).startswith("_")}
    return json.dumps(payload)


def _json_value(value: Any, default: Any):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default
    return value


def _get_game_module_for_storage(game_id: str, conn: PostgresConnection) -> dict | None:
    row = conn.execute(
        """
        SELECT
            g.slug AS game_id,
            g.display_name,
            g.domain AS cognitive_domain,
            g.description,
            g.config_json,
            g.is_active,
            gm.integration_mode,
            gm.feature_set,
            gm.label_schema,
            gm.schema_version,
            gm.ml_enabled,
            gm.included_in_cross_game,
            gm.status
        FROM games g
        LEFT JOIN game_metadata gm ON gm.game_id = g.slug
        WHERE g.slug=%s
        """,
        (game_id,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["feature_set"] = _json_value(result.get("feature_set"), [])
    result["label_schema"] = _json_value(result.get("label_schema"), {})
    return result


def _prepare_session_storage(
    game: str,
    session_dict: dict,
    conn: PostgresConnection,
) -> dict:
    """Build normalized JSONB sections and ML eligibility for a session."""
    payload = {k: v for k, v in session_dict.items() if not str(k).startswith("_")}
    metadata = _get_game_module_for_storage(game, conn)

    schema_version = (
        payload.get("schema_version")
        or (metadata or {}).get("schema_version")
        or "1.0"
    )
    payload["schema_version"] = schema_version

    core_json = {
        field: payload.get(field)
        for field in CORE_TELEMETRY_FIELDS
        if field in payload
    }
    extension_json = {
        key: value
        for key, value in payload.items()
        if key not in CORE_TELEMETRY_FIELDS and not str(key).startswith("_")
    }
    raw_events_json = payload.get("raw_events") or []
    data_json = dict(payload)
    data_json["game_id"] = game
    data_json["schema_version"] = schema_version

    feature_set = _json_value((metadata or {}).get("feature_set"), [])
    if not isinstance(feature_set, list):
        feature_set = []
    missing_features = [
        feature
        for feature in feature_set
        if feature not in data_json or data_json.get(feature) is None
    ]

    forced_ml_eligible = session_dict.get("_ml_eligible")
    if forced_ml_eligible is None:
        ml_enabled = bool((metadata or {}).get("ml_enabled", game in GAME_NAMES))
        ml_eligible = ml_enabled and not missing_features
    else:
        ml_eligible = bool(forced_ml_eligible)

    data_json["ml_eligible"] = ml_eligible
    if missing_features:
        data_json["missing_feature_set_fields"] = missing_features

    return {
        "protocol_id": game,
        "schema_version": schema_version,
        "data_json": json.dumps(data_json),
        "core_json": json.dumps(core_json),
        "extension_json": json.dumps(extension_json),
        "raw_events_json": json.dumps(raw_events_json),
        "ml_eligible": ml_eligible,
        "missing_features": missing_features,
    }


def insert_session(game: str, session_dict: dict) -> int:
    """
    Insert a validated game-module session into the shared sessions table.
    """
    with get_db() as conn:
        storage = _prepare_session_storage(game, session_dict, conn)
        session_dict["_ml_eligible"] = storage["ml_eligible"]
        cursor = conn.execute(
            """
            INSERT INTO sessions
                (game, user_id, participant_id, session_id, data_json,
                 protocol_id, schema_version, core_json, extension_json,
                 raw_events_json, ml_eligible, ingested_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO NOTHING
            RETURNING id
            """,
            (
                game,
                _coerce_session_user_id(session_dict),
                session_dict.get("Participant_ID", ""),
                session_dict.get("Game_Session_ID", ""),
                storage["data_json"],
                storage["protocol_id"],
                storage["schema_version"],
                storage["core_json"],
                storage["extension_json"],
                storage["raw_events_json"],
                storage["ml_eligible"],
                session_dict.get("ingested_at", datetime.now(timezone.utc).isoformat()),
            ),
        )
        return cursor.lastrowid


def insert_sessions_bulk(game: str, session_dicts: list[dict]) -> int:
    """
    Bulk-insert validated game-module sessions into the shared sessions table.
    """
    with get_db() as conn:
        rows = []
        for session in session_dicts:
            storage = _prepare_session_storage(game, session, conn)
            rows.append((
                game,
                _coerce_session_user_id(session),
                session.get("Participant_ID", ""),
                session.get("Game_Session_ID", ""),
                storage["data_json"],
                storage["protocol_id"],
                storage["schema_version"],
                storage["core_json"],
                storage["extension_json"],
                storage["raw_events_json"],
                storage["ml_eligible"],
                session.get("ingested_at", datetime.now(timezone.utc).isoformat()),
            ))
        cursor = conn.executemany(
            """
            INSERT INTO sessions
                (game, user_id, participant_id, session_id, data_json,
                 protocol_id, schema_version, core_json, extension_json,
                 raw_events_json, ml_eligible, ingested_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO NOTHING
            """,
            rows,
        )
        return cursor.rowcount


def _resolve_user_id(participant_id: str | int, conn=None) -> int | None:
    """
    Resolve a participant identifier to a numeric user_id.

    Accepts:
      - int (already a user_id) — returned directly after existence check
      - str that is purely numeric — treated as user_id
      - str (username) — looked up in users table case-insensitively
      - str (participant_id) — looked up via sessions.participant_id as a
        last-resort fallback so seeded/fake data participants are resolvable.

    Returns None if the user cannot be found.
    """
    if participant_id is None or participant_id == "":
        return None

    def _lookup(c):
        # 1. Numeric → direct user_id lookup
        try:
            uid = int(participant_id)
            row = c.execute("SELECT id FROM users WHERE id=%s", (uid,)).fetchone()
            return row["id"] if row else None
        except (TypeError, ValueError):
            pass
        # 2. Username lookup (case-insensitive)
        row = c.execute(
            "SELECT id FROM users WHERE LOWER(username)=LOWER(%s)",
            (str(participant_id),),
        ).fetchone()
        if row:
            return row["id"]
        # 3. Fallback: participant_id stored in sessions (seeded/fake data)
        row = c.execute(
            "SELECT user_id FROM sessions WHERE LOWER(participant_id)=LOWER(%s) "
            "AND user_id IS NOT NULL LIMIT 1",
            (str(participant_id),),
        ).fetchone()
        return row["user_id"] if row else None

    if conn is not None:
        return _lookup(conn)

    with get_db() as c:
        return _lookup(c)


def resolve_participant_user_id(participant_id: str | int) -> int | None:
    """Resolve an uploaded participant identifier to a NeuroGames user id."""
    return _resolve_user_id(participant_id)


# ── Games Registry Helpers ────────────────────────────────────────────────────

def list_games(active_only: bool = True) -> list[dict]:
    """Return all games from the registry, ordered by sort_order."""
    query = "SELECT * FROM games"
    if active_only:
        query += " WHERE is_active=TRUE"
    query += " ORDER BY sort_order"
    with get_db() as conn:
        rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def get_game(slug: str) -> dict | None:
    """Return the games registry row for a slug, or None if not found."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM games WHERE slug=%s", (slug,)).fetchone()
    return dict(row) if row else None


def register_game(
    slug: str,
    display_name: str,
    domain: str = "",
    description: str = None,
    config_json: str = None,
    sort_order: int = 99,
) -> bool:
    """
    Insert a new game into the registry.
    Returns True if the game was newly created.
    """
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO games
                (slug, display_name, domain, description,
                 config_json, is_active, sort_order, created_at)
            VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s)
            ON CONFLICT (slug) DO NOTHING
            """,
            (slug, display_name, domain, description, config_json, sort_order, now),
        )
        return conn.pg_conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE


def _validate_game_id(game_id: str) -> str:
    value = str(game_id or "").strip().lower()
    if not GAME_ID_RE.match(value):
        raise ValueError("game_id must use lowercase letters, numbers, _ or -")
    return value


def _normalize_feature_set(feature_set: list[str] | None) -> list[str]:
    result: list[str] = []
    for feature in feature_set or []:
        name = str(feature).strip()
        if name and name not in result:
            result.append(name)
    return result


def _module_from_row(row: dict) -> dict:
    item = dict(row)
    item["game_id"] = item.pop("slug", item.get("game_id"))
    item["cognitive_domain"] = item.pop("domain", item.get("cognitive_domain"))
    item["feature_set"] = _json_value(item.get("feature_set"), [])
    item["label_schema"] = _json_value(item.get("label_schema"), {})
    item["config_json"] = _json_value(item.get("config_json"), {})
    item["is_builtin"] = item.get("integration_mode") == "builtin"
    return item


def list_game_modules(
    active_only: bool = False,
    ml_enabled_only: bool = False,
    cross_game_only: bool = False,
) -> list[dict]:
    """Return ADHD game modules from the canonical games registry."""
    filters = ["COALESCE(gm.status, 'draft') <> 'archived'"]
    params: list[Any] = []
    if active_only:
        filters.append("g.is_active=TRUE")
        filters.append("COALESCE(gm.status, 'draft') = 'active'")
    if ml_enabled_only:
        filters.append("COALESCE(gm.ml_enabled, FALSE)=TRUE")
    if cross_game_only:
        filters.append("COALESCE(gm.included_in_cross_game, FALSE)=TRUE")
    where = " WHERE " + " AND ".join(filters) if filters else ""
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                g.slug,
                g.display_name,
                g.domain,
                g.description,
                g.config_json,
                g.is_active,
                g.sort_order,
                g.created_at,
                COALESCE(gm.integration_mode, 'manual') AS integration_mode,
                COALESCE(gm.feature_set, '[]'::jsonb) AS feature_set,
                COALESCE(gm.label_schema, '{{}}'::jsonb) AS label_schema,
                COALESCE(gm.schema_version, '1.0') AS schema_version,
                COALESCE(gm.ml_enabled, FALSE) AS ml_enabled,
                COALESCE(gm.included_in_cross_game, FALSE) AS included_in_cross_game,
                COALESCE(gm.status, 'draft') AS status,
                gm.updated_at
            FROM games g
            LEFT JOIN game_metadata gm ON gm.game_id = g.slug
            {where}
            ORDER BY CASE WHEN COALESCE(gm.integration_mode, 'manual') = 'builtin' THEN 0 ELSE 1 END,
                     g.sort_order,
                     g.display_name
            """,
            params,
        ).fetchall()
    return [_module_from_row(dict(row)) for row in rows]


def get_game_module(game_id: str) -> dict | None:
    game_id = _validate_game_id(game_id)
    modules = [m for m in list_game_modules(active_only=False) if m["game_id"] == game_id]
    return modules[0] if modules else None


def is_registered_game(game_id: str) -> bool:
    module = get_game_module(game_id)
    return bool(module and module.get("status") != "archived" and module.get("is_active"))


def list_ml_enabled_game_ids() -> list[str]:
    """Return game IDs eligible for per-game ML/MLOps workflows."""
    return [g["game_id"] for g in list_game_modules(active_only=True, ml_enabled_only=True)]


def list_cross_game_enabled_game_ids() -> list[str]:
    """Return game IDs allowed in ADHD cross-game consensus."""
    return [
        g["game_id"]
        for g in list_game_modules(
            active_only=True,
            ml_enabled_only=True,
            cross_game_only=True,
        )
    ]


def is_ml_enabled_game(game_id: str) -> bool:
    module = get_game_module(game_id)
    return bool(module and module.get("ml_enabled") and module.get("status") == "active")


def register_game_module(
    game_id: str,
    display_name: str,
    cognitive_domain: str,
    description: str | None = None,
    integration_mode: str = "manual",
    feature_set: list[str] | None = None,
    schema_version: str = "1.0",
    ml_enabled: bool = False,
    included_in_cross_game: bool = False,
    status: str = "draft",
    config_json: dict | None = None,
) -> dict:
    """Register or update an ADHD-compatible game module."""
    game_id = _validate_game_id(game_id)
    integration_mode = str(integration_mode or "manual").strip().lower()
    if integration_mode not in GAME_INTEGRATION_MODES:
        raise ValueError(f"Unsupported integration_mode: {integration_mode}")
    status = str(status or "draft").strip().lower()
    if status not in GAME_MODULE_STATUSES:
        raise ValueError(f"Unsupported game module status: {status}")
    feature_set = _normalize_feature_set(feature_set)
    now = _now_iso()
    ml_enabled = bool(ml_enabled)
    included_in_cross_game = bool(included_in_cross_game and ml_enabled)
    config_payload = config_json or {}
    config_payload.update({
        "label_schema": "adhd_4_class",
        "target_domain": "ADHD",
    })

    with get_db() as conn:
        row = conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS sort_order FROM games").fetchone()
        sort_order = int((row or {}).get("sort_order") or 0) + 1
        conn.execute(
            """
            INSERT INTO games
                (slug, display_name, domain, description,
                 config_json, is_active, sort_order, created_at)
            VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s)
            ON CONFLICT (slug) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                domain = EXCLUDED.domain,
                description = EXCLUDED.description,
                config_json = EXCLUDED.config_json,
                is_active = TRUE
            """,
            (
                game_id,
                display_name,
                cognitive_domain,
                description,
                json.dumps(config_payload),
                sort_order,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO game_metadata
                (game_id, integration_mode, feature_set, label_schema,
                 schema_version, ml_enabled, included_in_cross_game,
                 status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (game_id) DO UPDATE SET
                integration_mode = EXCLUDED.integration_mode,
                feature_set = EXCLUDED.feature_set,
                label_schema = EXCLUDED.label_schema,
                schema_version = EXCLUDED.schema_version,
                ml_enabled = EXCLUDED.ml_enabled,
                included_in_cross_game = EXCLUDED.included_in_cross_game,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
            """,
            (
                game_id,
                integration_mode,
                json.dumps(feature_set),
                json.dumps(REFERENCE_LABEL_SCHEMA),
                schema_version or "1.0",
                ml_enabled,
                included_in_cross_game,
                status,
                now,
                now,
            ),
        )
    return get_game_module(game_id) or {}


_UNSET = object()


def update_game_module(
    game_id: str,
    display_name: str | None = None,
    cognitive_domain: str | None = None,
    description: Any = _UNSET,
    integration_mode: str | None = None,
    feature_set: list[str] | None = None,
    schema_version: str | None = None,
    ml_enabled: bool | None = None,
    included_in_cross_game: bool | None = None,
    status: str | None = None,
) -> dict:
    """Update metadata for an existing ADHD game module without rotating keys."""
    game_id = _validate_game_id(game_id)
    current = get_game_module(game_id)
    if not current:
        raise ValueError(f"Unknown game module: {game_id}")

    next_integration_mode = integration_mode or current.get("integration_mode") or "manual"
    next_integration_mode = str(next_integration_mode).strip().lower()
    if next_integration_mode not in GAME_INTEGRATION_MODES:
        raise ValueError(f"Unsupported integration_mode: {next_integration_mode}")
    if current.get("is_builtin") and next_integration_mode != "builtin":
        raise ValueError("Built-in reference modules cannot change integration_mode")

    next_status = status or current.get("status") or "draft"
    next_status = str(next_status).strip().lower()
    if next_status not in GAME_MODULE_STATUSES:
        raise ValueError(f"Unsupported game module status: {next_status}")

    next_ml_enabled = bool(current.get("ml_enabled")) if ml_enabled is None else bool(ml_enabled)
    if included_in_cross_game is None:
        next_cross_game = bool(current.get("included_in_cross_game"))
    else:
        next_cross_game = bool(included_in_cross_game)
    next_cross_game = bool(next_cross_game and next_ml_enabled)

    next_feature_set = (
        _normalize_feature_set(feature_set)
        if feature_set is not None
        else list(current.get("feature_set") or [])
    )
    next_display_name = display_name or current.get("display_name") or game_id
    next_domain = cognitive_domain or current.get("cognitive_domain") or "ADHD"
    next_description = current.get("description") if description is _UNSET else description
    next_schema_version = schema_version or current.get("schema_version") or "1.0"

    config_payload = current.get("config_json") if isinstance(current.get("config_json"), dict) else {}
    config_payload.update({
        "label_schema": "adhd_4_class",
        "target_domain": "ADHD",
    })
    now = _now_iso()

    with get_db() as conn:
        conn.execute(
            """
            UPDATE games
            SET display_name=%s,
                domain=%s,
                description=%s,
                config_json=%s,
                is_active=%s
            WHERE slug=%s
            """,
            (
                next_display_name,
                next_domain,
                next_description,
                json.dumps(config_payload),
                next_status != "archived",
                game_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO game_metadata
                (game_id, integration_mode, feature_set, label_schema,
                 schema_version, ml_enabled, included_in_cross_game,
                 status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (game_id) DO UPDATE SET
                integration_mode = EXCLUDED.integration_mode,
                feature_set = EXCLUDED.feature_set,
                label_schema = EXCLUDED.label_schema,
                schema_version = EXCLUDED.schema_version,
                ml_enabled = EXCLUDED.ml_enabled,
                included_in_cross_game = EXCLUDED.included_in_cross_game,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
            """,
            (
                game_id,
                next_integration_mode,
                json.dumps(next_feature_set),
                json.dumps(REFERENCE_LABEL_SCHEMA),
                next_schema_version,
                next_ml_enabled,
                next_cross_game,
                next_status,
                current.get("created_at") or now,
                now,
            ),
        )
    return get_game_module(game_id) or {}


def _request_from_row(row: dict) -> dict:
    item = dict(row)
    item["feature_set"] = _json_value(item.get("feature_set"), [])
    item["telemetry_schema_json"] = _json_value(item.get("telemetry_schema_json"), None)
    item["sample_payload_json"] = _json_value(item.get("sample_payload_json"), None)
    return item


def _safe_artifact_filename(filename: str | None) -> str:
    """Return a safe ZIP artifact filename or raise a user-facing error."""
    cleaned = Path(str(filename or "")).name.strip()
    if not cleaned:
        raise ValueError("A ZIP filename is required when code_artifact_base64 is provided")
    if not cleaned.lower().endswith(".zip"):
        raise ValueError("Game code artifacts must be submitted as a .zip file")
    return re.sub(r"[^A-Za-z0-9._-]", "_", cleaned)


def _decode_artifact_base64(encoded: str) -> bytes:
    raw = str(encoded or "").strip()
    if not raw:
        raise ValueError("code_artifact_base64 cannot be empty")
    if raw.lower().startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        payload = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("code_artifact_base64 must be valid base64") from exc
    if not payload:
        raise ValueError("Submitted ZIP artifact is empty")
    if len(payload) > MAX_GAME_ARTIFACT_BYTES:
        raise ValueError("Submitted ZIP artifact is larger than the 5 MB prototype limit")
    return payload


def _store_request_artifact(
    request_id: int,
    filename: str | None,
    encoded: str | None,
) -> tuple[str, str, int] | None:
    """Persist the developer-submitted ZIP artifact for later superadmin review."""
    if not encoded:
        return None
    safe_name = _safe_artifact_filename(filename)
    payload = _decode_artifact_base64(encoded)
    target_dir = REQUEST_ARTIFACT_DIR / f"request_{int(request_id)}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name
    target_path.write_bytes(payload)
    return str(target_path.resolve()), safe_name, len(payload)


def _safe_zip_member(info: zipfile.ZipInfo) -> PurePosixPath | None:
    """Validate one static file from a submitted game ZIP."""
    if info.is_dir():
        return None
    normalized = info.filename.replace("\\", "/")
    rel = PurePosixPath(normalized)
    if rel.is_absolute() or ".." in rel.parts or not rel.name:
        raise ValueError(f"Unsafe path in ZIP artifact: {info.filename}")
    if rel.suffix.lower() not in SAFE_GAME_ARTIFACT_EXTENSIONS:
        raise ValueError(f"Unsupported file type in ZIP artifact: {info.filename}")
    return rel


def _publish_request_artifact(request: dict) -> str | None:
    """
    Safely extract a reviewed static HTML/JS game package.

    The package is served as static frontend code only. It cannot add Python,
    Node, database, or backend execution logic.
    """
    artifact_path = request.get("code_artifact_path")
    if not artifact_path:
        return None
    source = Path(str(artifact_path))
    if not source.exists():
        raise ValueError("The submitted ZIP artifact is missing from storage")

    game_id = _validate_game_id(str(request.get("game_id") or ""))
    publish_dir = PUBLISHED_GAME_DIR / game_id
    publish_root = publish_dir.resolve()
    entry_candidates: list[PurePosixPath] = []
    total_size = 0
    members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []

    try:
        with zipfile.ZipFile(source) as package:
            for info in package.infolist():
                rel = _safe_zip_member(info)
                if rel is None:
                    continue
                total_size += int(info.file_size or 0)
                if total_size > MAX_PUBLISHED_GAME_BYTES:
                    raise ValueError("Published ZIP expands beyond the 15 MB prototype limit")
                if rel.name.lower() == "index.html":
                    entry_candidates.append(rel)
                members.append((info, rel))

            if not members:
                raise ValueError("ZIP artifact does not contain any publishable static files")
            if not entry_candidates:
                raise ValueError("ZIP artifact must contain an index.html entry point")

            if publish_dir.exists():
                shutil.rmtree(publish_dir)
            publish_dir.mkdir(parents=True, exist_ok=True)

            for info, rel in members:
                target = (publish_dir / Path(*rel.parts)).resolve()
                if target != publish_root and publish_root not in target.parents:
                    raise ValueError(f"Unsafe extraction path in ZIP artifact: {info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile as exc:
        raise ValueError("Submitted code artifact is not a valid ZIP file") from exc

    entry_rel = sorted(entry_candidates, key=lambda path: (len(path.parts), path.as_posix()))[0]
    return f"{PUBLISHED_GAME_URL_PREFIX}/{game_id}/{entry_rel.as_posix()}"


def create_game_module_request(
    developer_user_id: int,
    game_id: str,
    display_name: str,
    cognitive_domain: str,
    description: str | None = None,
    integration_mode: str = "manual",
    feature_set: list[str] | None = None,
    code_repository_url: str | None = None,
    code_summary: str | None = None,
    code_artifact_filename: str | None = None,
    code_artifact_base64: str | None = None,
    telemetry_schema_json: Any = None,
    sample_payload_json: Any = None,
) -> dict:
    """Create a pending developer request for a compatible ADHD game module."""
    game_id = _validate_game_id(game_id)
    integration_mode = str(integration_mode or "manual").strip().lower()
    if integration_mode not in GAME_INTEGRATION_MODES:
        raise ValueError(f"Unsupported integration_mode: {integration_mode}")
    feature_set = _normalize_feature_set(feature_set)
    now = _now_iso()
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO developer_game_requests
                (developer_user_id, game_id, display_name, cognitive_domain,
                 description, integration_mode, feature_set, code_repository_url,
                 code_summary, telemetry_schema_json, sample_payload_json,
                 status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
            RETURNING id
            """,
            (
                developer_user_id,
                game_id,
                display_name,
                cognitive_domain,
                description,
                integration_mode,
                json.dumps(feature_set),
                code_repository_url,
                code_summary,
                json.dumps(telemetry_schema_json) if telemetry_schema_json is not None else None,
                json.dumps(sample_payload_json) if sample_payload_json is not None else None,
                now,
                now,
            ),
        )
        request_id = cursor.lastrowid
        artifact = _store_request_artifact(
            request_id,
            code_artifact_filename,
            code_artifact_base64,
        )
        if artifact:
            conn.execute(
                """
                UPDATE developer_game_requests
                SET code_artifact_path=%s,
                    code_artifact_filename=%s,
                    code_artifact_size=%s,
                    updated_at=%s
                WHERE id=%s
                """,
                (
                    artifact[0],
                    artifact[1],
                    artifact[2],
                    now,
                    request_id,
                ),
            )
    return get_game_module_request(request_id) or {}


def list_game_module_requests(
    status: str | None = None,
    developer_user_id: int | None = None,
) -> list[dict]:
    """Return developer game-module requests with developer contact metadata."""
    filters: list[str] = []
    params: list[Any] = []
    status_filter = str(status or "").strip().lower()
    if status_filter and status_filter != "all":
        filters.append("r.status=%s")
        params.append(status_filter)
    if developer_user_id is not None:
        filters.append("r.developer_user_id=%s")
        params.append(developer_user_id)
    where = "WHERE " + " AND ".join(filters) if filters else ""
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                r.*,
                u.username AS developer_username,
                u.email AS developer_email,
                u.phone AS developer_phone,
                reviewer.username AS reviewer_username
            FROM developer_game_requests r
            JOIN users u ON u.id = r.developer_user_id
            LEFT JOIN users reviewer ON reviewer.id = r.reviewer_user_id
            {where}
            ORDER BY
                CASE r.status WHEN 'pending' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END,
                r.created_at DESC
            """,
            params,
        ).fetchall()
    return [_request_from_row(dict(row)) for row in rows]


def get_game_module_request(request_id: int) -> dict | None:
    """Return one developer game-module request."""
    rows = list_game_module_requests()
    for row in rows:
        if int(row["id"]) == int(request_id):
            return row
    return None


def decide_game_module_request(
    request_id: int,
    reviewer_user_id: int,
    decision: str,
    review_notes: str | None = None,
    accepted_status: str = "active",
) -> dict:
    """Accept or reject a developer request. Accepted requests create a game module."""
    decision = str(decision or "").strip().lower()
    if decision not in {"accepted", "rejected"}:
        raise ValueError("decision must be accepted or rejected")
    accepted_status = str(accepted_status or "draft").strip().lower()
    if accepted_status not in GAME_MODULE_STATUSES:
        raise ValueError(f"Unsupported game module status: {accepted_status}")

    request = get_game_module_request(request_id)
    if not request:
        raise ValueError(f"Unknown game module request: {request_id}")
    if request.get("status") != "pending":
        raise ValueError("Only pending requests can be reviewed")

    created_game_id = None
    game_module = None
    ingestion_key = None
    published_entry_url = None
    if decision == "accepted":
        published_entry_url = _publish_request_artifact(request)
        runtime_type = "iframe_package" if published_entry_url else "placeholder"
        module_config = {
            "developer_request_id": request_id,
            "code_repository_url": request.get("code_repository_url"),
            "code_summary": request.get("code_summary"),
            "runtime_type": runtime_type,
        }
        if published_entry_url:
            module_config["entry_url"] = published_entry_url
        game_module = register_game_module(
            game_id=request["game_id"],
            display_name=request["display_name"],
            cognitive_domain=request["cognitive_domain"],
            description=request.get("description"),
            integration_mode=request.get("integration_mode") or "manual",
            feature_set=request.get("feature_set") or [],
            schema_version="1.0",
            ml_enabled=False,
            included_in_cross_game=False,
            status=accepted_status,
            config_json=module_config,
        )
        created_game_id = game_module.get("game_id")
        ingestion_key = create_game_ingestion_key(
            created_game_id,
            created_by=reviewer_user_id,
            label=f"request-{request_id}",
        )

    now = _now_iso()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE developer_game_requests
            SET status=%s,
                reviewer_user_id=%s,
                review_notes=%s,
                created_game_id=%s,
                published_entry_url=%s,
                updated_at=%s,
                decided_at=%s
            WHERE id=%s
            """,
            (
                decision,
                reviewer_user_id,
                review_notes,
                created_game_id,
                published_entry_url,
                now,
                now,
                request_id,
            ),
        )

    reviewed = get_game_module_request(request_id) or {}
    reviewed["game_module"] = game_module
    reviewed["ingestion_key"] = ingestion_key
    return reviewed


def _generate_game_ingestion_key(game_id: str) -> str:
    return f"ng_game_{game_id}_{secrets.token_urlsafe(24)}"


def _hash_ingestion_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_game_ingestion_key(
    game_id: str,
    created_by: int | None = None,
    label: str | None = None,
) -> dict:
    """Create a backend-only ingestion key for a registered game module."""
    game_id = _validate_game_id(game_id)
    if not get_game_module(game_id):
        raise ValueError(f"Unknown game module: {game_id}")
    raw_key = _generate_game_ingestion_key(game_id)
    key_prefix = raw_key[:24]
    now = _now_iso()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO game_ingestion_keys
                (game_id, key_prefix, key_hash, label, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                game_id,
                key_prefix,
                _hash_ingestion_key(raw_key),
                label or "default",
                created_by,
                now,
            ),
        )
    return {
        "api_key": raw_key,
        "key_prefix": key_prefix,
        "created_at": now,
        "label": label or "default",
    }


def verify_game_ingestion_key(game_id: str, raw_key: str) -> bool:
    """Return True when a game ingestion key is valid and active."""
    if not raw_key:
        return False
    try:
        game_id = _validate_game_id(game_id)
    except ValueError:
        return False
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id FROM game_ingestion_keys
            WHERE game_id=%s AND key_hash=%s AND revoked_at IS NULL
            """,
            (game_id, _hash_ingestion_key(raw_key)),
        ).fetchone()
    return bool(row)


def list_game_ingestion_keys(game_id: str) -> list[dict]:
    game_id = _validate_game_id(game_id)
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, game_id, key_prefix, label, created_by, created_at, revoked_at
            FROM game_ingestion_keys
            WHERE game_id=%s
            ORDER BY created_at DESC
            """,
            (game_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_game_ingestion_keys(game_id: str) -> int:
    game_id = _validate_game_id(game_id)
    now = _now_iso()
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE game_ingestion_keys
            SET revoked_at=%s
            WHERE game_id=%s AND revoked_at IS NULL
            """,
            (now, game_id),
        )
        return cursor.rowcount


def _parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an ISO timestamp and normalize it to UTC when possible."""
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def create_user(
    username: str,
    password_hash: str,
    role: str = "child",
    school_id: int | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> int:
    """Create an application user and return its row id."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO users (username, password_hash, role, email, phone, school_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (username) DO NOTHING
            RETURNING id
            """,
            (username.strip(), password_hash, role, email, phone, school_id, now),
        )
        return cursor.lastrowid


def get_user_by_username(username: str) -> dict | None:
    """Return a user by username, using case-insensitive lookup."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1",
            (username.strip(),),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    """Return a user by internal id."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id=%s LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def create_user_profile(
    user_id: int,
    display_name: str | None = None,
    avatar: str | None = None,
    age: int | None = None,
    age_group: str | None = None,
    cognitive_level: str | None = None,
    cluster: str | None = None,
    locale_pref: str | None = None,
    custom_school_name: str | None = None,
    conners_score: int | None = None,
    conners_data: str | None = None,
) -> None:
    """Create or replace profile metadata for a user."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO user_profiles
                (user_id, display_name, avatar, age, age_group,
                 cognitive_level, cluster, locale_pref, custom_school_name,
                 conners_score, conners_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                avatar = EXCLUDED.avatar,
                age = EXCLUDED.age,
                age_group = EXCLUDED.age_group,
                cognitive_level = EXCLUDED.cognitive_level,
                cluster = EXCLUDED.cluster,
                locale_pref = EXCLUDED.locale_pref,
                custom_school_name = EXCLUDED.custom_school_name,
                conners_score = EXCLUDED.conners_score,
                conners_data = EXCLUDED.conners_data
            """,
            (
                user_id,
                display_name,
                avatar,
                age,
                age_group,
                cognitive_level,
                cluster,
                locale_pref,
                custom_school_name,
                conners_score,
                conners_data,
            ),
        )


def get_user_profile_full(user_id: int) -> dict | None:
    """Return profile metadata for a user including account info and school name."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT up.*, u.username, u.role, u.school_id, u.created_at, s.name as school_name
            FROM user_profiles up
            JOIN users u ON u.id = up.user_id
            LEFT JOIN schools s ON s.id = u.school_id
            WHERE up.user_id = %s
            """,
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_user_profile(user_id: int) -> dict | None:
    """Return profile metadata for a user."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id=%s LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def update_last_login(user_id: int) -> None:
    """Record a successful login timestamp."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET last_login_at=%s WHERE id=%s",
            (now, user_id),
        )


def create_auth_session(
    user_id: int,
    token: str,
    remember: bool,
    expires_at: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> int:
    """Persist an opaque bearer-token session."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO auth_sessions
                (user_id, token, remember, created_at, expires_at, ip_address, user_agent)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, token, remember, now, expires_at, ip_address, user_agent),
        )
        return cursor.lastrowid


def validate_auth_session(token: str) -> dict | None:
    """Return the authenticated user/session for a valid bearer token."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                s.id AS session_id,
                s.user_id,
                s.token,
                s.remember,
                s.created_at,
                s.expires_at,
                s.revoked_at,
                u.username,
                u.role,
                u.school_id
            FROM auth_sessions s
            INNER JOIN users u ON u.id = s.user_id
            WHERE s.token=%s AND s.revoked_at IS NULL
            LIMIT 1
            """,
            (token,),
        ).fetchone()

    if not row:
        return None

    session = dict(row)
    expires_at = _parse_iso_datetime(session["expires_at"])
    now = datetime.now(timezone.utc)
    if expires_at is None or expires_at <= now:
        revoke_session(token)
        return None
    return session


def revoke_session(token: str) -> bool:
    """Revoke one bearer-token session."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE auth_sessions
            SET revoked_at=%s
            WHERE token=%s AND revoked_at IS NULL
            """,
            (now, token),
        )
        return cursor.rowcount > 0


def revoke_all_sessions(user_id: int) -> int:
    """Revoke all active sessions for a user."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE auth_sessions
            SET revoked_at=%s
            WHERE user_id=%s AND revoked_at IS NULL
            """,
            (now, user_id),
        )
        return cursor.rowcount


# ── School Management ─────────────────────────────────────────────────────────

def create_school(name: str, created_by: int | None = None) -> int:
    """Create a new school and return its row id."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO schools (name, created_at, created_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (name, now, created_by)
        )
        return cursor.lastrowid


def list_schools() -> list[dict]:
    """Return all schools ordered by name."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM schools ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_school(school_id: int) -> dict | None:
    """Return a school by id, or None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM schools WHERE id=%s LIMIT 1", (school_id,)
        ).fetchone()
    return dict(row) if row else None


def list_players_full(school_id: int | None = None) -> list[dict]:
    """Return all players with their profile data and custom school name."""
    query = """
        SELECT u.id, u.username, u.role, u.school_id, u.created_at,
               up.display_name, up.age, up.custom_school_name,
               COALESCE(up.custom_school_name, s.name) as school_name
        FROM users u
        LEFT JOIN user_profiles up ON up.user_id = u.id
        LEFT JOIN schools s ON s.id = u.school_id
        WHERE u.role = 'child'
    """
    params = []
    if school_id is not None:
        query += " AND u.school_id = %s"
        params.append(school_id)
    query += " ORDER BY u.created_at DESC LIMIT 100"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def list_admins() -> list[dict]:
    """Return all therapist and super_admin users with their school assignments."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.role, u.created_at, u.last_login_at
            FROM users u
            WHERE u.role IN ('therapist', 'admin', 'super_admin')
            ORDER BY u.created_at DESC
            """,
        ).fetchall()
        admins = []
        for row in rows:
            data = dict(row)
            # Fetch all school assignments for this admin
            school_rows = conn.execute(
                """
                SELECT s.id, s.name
                FROM admin_school_assignments asa
                JOIN schools s ON s.id = asa.school_id
                WHERE asa.admin_id = %s
                """,
                (data["id"],)
            ).fetchall()
            data["schools"] = [dict(s) for s in school_rows]
            # Legacy single-school compat field for any code still using it
            data["school_name"] = data["schools"][0]["name"] if data["schools"] else None
            data["school_id"] = data["schools"][0]["id"] if data["schools"] else None
            admins.append(data)
    return admins


def get_student_count_by_school(school_id: int) -> int:
    """Return the number of players (role='child') in a specific school."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE school_id=%s AND role='child'",
            (school_id,)
        ).fetchone()
    return row["cnt"] if row else 0


def get_admin_school_ids(admin_id: int) -> list[int]:
    """Return all school IDs assigned to a therapist/school-scoped dashboard user."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT school_id FROM admin_school_assignments WHERE admin_id = %s",
            (admin_id,)
        ).fetchall()
    return [r["school_id"] for r in rows]


def assign_admin_to_school(admin_id: int, school_id: int) -> bool:
    """Assign a therapist to a school. Returns True if inserted, False if already exists."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO admin_school_assignments (admin_id, school_id, created_at) VALUES (%s, %s, %s) ON CONFLICT (admin_id, school_id) DO NOTHING",
            (admin_id, school_id, now)
        )
    return cursor.rowcount > 0


def unassign_admin_from_school(admin_id: int, school_id: int) -> bool:
    """Remove a school assignment from a therapist. Returns True if deleted."""
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM admin_school_assignments WHERE admin_id = %s AND school_id = %s",
            (admin_id, school_id)
        )
    return cursor.rowcount > 0


def get_admin_detail_full(admin_id: int) -> dict | None:
    """Return detailed info for a therapist/superadmin, with schools and student counts."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.username, u.role, u.created_at, u.last_login_at
            FROM users u
            WHERE u.id = %s AND u.role IN ('therapist', 'admin', 'super_admin')
            """,
            (admin_id,)
        ).fetchone()

        if not row:
            return None

        data = dict(row)

        school_rows = conn.execute(
            """
            SELECT s.id, s.name
            FROM admin_school_assignments asa
            JOIN schools s ON s.id = asa.school_id
            WHERE asa.admin_id = %s
            """,
            (admin_id,)
        ).fetchall()

        schools_with_counts = []
        total_students = 0
        for s in school_rows:
            count = get_student_count_by_school(s["id"])
            total_students += count
            schools_with_counts.append({"id": s["id"], "name": s["name"], "student_count": count})

        data["schools"] = schools_with_counts
        data["student_count"] = total_students
        # Legacy compat
        data["school_id"] = schools_with_counts[0]["id"] if schools_with_counts else None
        data["school_name"] = schools_with_counts[0]["name"] if schools_with_counts else None
        return data


def list_users_by_school(school_id: int, role: str | None = None) -> list[dict]:
    """Return users belonging to a specific school, optionally filtered by role."""
    query = "SELECT id, username, role, school_id, created_at FROM users WHERE school_id=%s"
    params: list = [school_id]
    if role:
        query += " AND role=%s"
        params.append(role)
    query += " ORDER BY created_at DESC"
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]



# ── Audit Logging ─────────────────────────────────────────────────────────────

def insert_audit_log(
    user_id: int | None,
    username: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: str | None = None,
    ip_address: str | None = None,
) -> int:
    """Record an admin action in the audit log."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO audit_log
                (user_id, username, action, target_type, target_id, detail, ip_address, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, username, action, target_type, target_id, detail, ip_address, now),
        )
        return cursor.lastrowid


def get_audit_logs(limit: int = 50) -> list[dict]:
    """Return the most recent audit log entries."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Training-run tracking ─────────────────────────────────────────────────────

def mark_trained(game: str, session_ids: list[str], run_id: str = None) -> None:
    """
    Record that a list of session_ids were consumed by the training DAG.

    Inserts into training_runs (IGNORE on conflict so re-runs are idempotent).

    Args:
        game:        game name
        session_ids: list of Game_Session_ID strings to mark
        run_id:      optional MLflow run ID linking this training run
    """
    if not session_ids:
        return
    now = datetime.now(timezone.utc).isoformat()
    run_id = run_id or f"manual_training_{now}"
    rows = [(sid, game, run_id, now) for sid in session_ids]
    with get_db() as conn:
        conn.executemany(
            """
            INSERT INTO training_runs (session_id, game, run_id, trained_at)
            VALUES (%s, %s, %s, %s)
            """,
            rows,
        )


def mark_all_untrained_as_trained(game: str, run_id: str = None) -> int:
    """
    Mark ALL sessions that haven't been trained on yet for a game.

    This is the scalable path: finds untrained session IDs in SQL,
    bulk-inserts them into training_runs.

    Returns:
        Number of sessions newly marked as trained.
    """
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        # Find untrained session IDs
        rows = conn.execute(
            """
            SELECT session_id FROM sessions
            WHERE game = %s
              AND NOT EXISTS (
                  SELECT 1 FROM training_runs tr
                  WHERE tr.session_id = sessions.session_id
                    AND tr.game = %s
                    AND tr.status = 'completed'
              )
            """,
            (game, game),
        ).fetchall()

        if not rows:
            return 0

        run_id = run_id or f"manual_training_{now}"
        insert_rows = [(r["session_id"], game, run_id, now) for r in rows]
        conn.executemany(
            """
            INSERT INTO training_runs (session_id, game, run_id, trained_at)
            VALUES (%s, %s, %s, %s)
            """,
            insert_rows,
        )
        return len(insert_rows)


# ── Read (training-aware) ─────────────────────────────────────────────────────

def count_untrained(game: str, ml_eligible_only: bool = False) -> int:
    """Count sessions that have NOT been consumed by a training run yet."""
    eligibility_sql = "AND ml_eligible = TRUE" if ml_eligible_only else ""
    with get_db() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) FROM sessions
            WHERE game = %s
              {eligibility_sql}
              AND NOT EXISTS (
                  SELECT 1 FROM training_runs tr
                  WHERE tr.session_id = sessions.session_id
                    AND tr.game = %s
                    AND tr.status = 'completed'
              )
            """,
            (game, game),
        ).fetchone()
        return list(row.values())[0] if row else 0


def count_all_untrained(ml_eligible_only: bool = False) -> dict:
    """Return {game: count} for all games with untrained sessions."""
    eligibility_sql = "AND s.ml_eligible = TRUE" if ml_eligible_only else ""
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT s.game, COUNT(*) as n
            FROM sessions s
            WHERE NOT EXISTS (
                SELECT 1 FROM training_runs tr
                WHERE tr.session_id = s.session_id
                  AND tr.game = s.game
                  AND tr.status = 'completed'
            )
            {eligibility_sql}
            GROUP BY s.game
            """
        ).fetchall()
        return {r["game"]: r["n"] for r in rows}


def get_untrained(game: str) -> list[dict]:
    """
    Fetch all untrained sessions for a game as a list of dicts.
    The data_json column is decoded back to a dict automatically.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, data_json FROM sessions
            WHERE game = %s
              AND NOT EXISTS (
                  SELECT 1 FROM training_runs tr
                  WHERE tr.session_id = sessions.session_id
                    AND tr.game = %s
                    AND tr.status = 'completed'
              )
            ORDER BY id
            """,
            (game, game),
        ).fetchall()

    result = []
    for row in rows:
        try:
            d = (json.loads(row["data_json"]) if isinstance(row["data_json"], str) else row["data_json"])
            d["_db_id"] = row["id"]
            d["Game_Session_ID"] = row["session_id"]
            result.append(d)
        except Exception:
            pass
    return result


# ── DataFrame helpers ─────────────────────────────────────────────────────────

def list_untrained_session_ids(game: str, ml_eligible_only: bool = False) -> list[str]:
    """Return untrained Game_Session_ID values for a game in insertion order."""
    eligibility_sql = "AND ml_eligible = TRUE" if ml_eligible_only else ""
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT session_id FROM sessions
            WHERE game = %s
              {eligibility_sql}
              AND NOT EXISTS (
                  SELECT 1 FROM training_runs tr
                  WHERE tr.session_id = sessions.session_id
                    AND tr.game = %s
                    AND tr.status = 'completed'
              )
            ORDER BY id
            """,
            (game, game),
        ).fetchall()
    return [r["session_id"] for r in rows]


def list_game_session_ids(game: str, ml_eligible_only: bool = False) -> list[str]:
    """Return all Game_Session_ID values for a game in ingestion order."""
    eligibility_sql = "AND ml_eligible = TRUE" if ml_eligible_only else ""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT session_id FROM sessions WHERE game=%s {eligibility_sql} ORDER BY id",
            (game,),
        ).fetchall()
    return [r["session_id"] for r in rows]


def load_game_df(
    game: str,
    trained_only: bool = False,
    ml_eligible_only: bool | None = None,
) -> "pd.DataFrame":
    """
    Load all sessions for a given game as a flat pandas DataFrame.

    Args:
        game:        Game name (e.g. 'gonogo').
        trained_only: If True, return only sessions that have been marked
                      as trained (i.e. used in a training run). Useful for
                      drift detection to compare baseline vs. new data.
        ml_eligible_only: If True, exclude sessions that passed ingestion but
                          are not eligible for ML. Defaults to True for
                          registered ML-enabled game modules.

    Returns:
        A pd.DataFrame with one row per session. Empty DataFrame if no data.
    """
    import pandas as pd

    if ml_eligible_only is None:
        ml_eligible_only = is_ml_enabled_game(game)
    eligibility_sql = "AND s.ml_eligible = TRUE" if ml_eligible_only else ""

    if trained_only:
        query = (
            "SELECT DISTINCT s.user_id, s.participant_id, s.session_id, "
            "s.data_json, s.extension_json, s.ingested_at, p.cluster AS profile_cluster "
            "FROM sessions s "
            "INNER JOIN training_runs tr ON tr.session_id = s.session_id AND tr.game = s.game "
            "LEFT JOIN user_profiles p ON p.user_id = s.user_id "
            f"WHERE s.game = %s AND tr.status = 'completed' {eligibility_sql} "
            "ORDER BY s.ingested_at ASC"
        )
    else:
        query = (
            "SELECT s.user_id, s.participant_id, s.session_id, "
            "s.data_json, s.extension_json, p.cluster AS profile_cluster "
            "FROM sessions s "
            "LEFT JOIN user_profiles p ON p.user_id = s.user_id "
            f"WHERE s.game=%s {eligibility_sql} "
            "ORDER BY s.ingested_at ASC"
        )

    with get_db() as conn:
        rows = conn.execute(query, (game,)).fetchall()

    if not rows:
        return pd.DataFrame()

    records = []
    for row in rows:
        try:
            record = (json.loads(row["data_json"]) if isinstance(row["data_json"], str) else row["data_json"])
        except Exception:
            continue
        try:
            extension_record = (
                json.loads(row["extension_json"])
                if isinstance(row.get("extension_json"), str)
                else row.get("extension_json")
            )
            if isinstance(extension_record, dict):
                record.update(extension_record)
        except Exception:
            pass

        if game in GAME_NAMES and game not in HINT_METRIC_GAMES:
            record.pop("Hint_Usage", None)

        record["Participant_ID"] = row["participant_id"]
        record["Game_Session_ID"] = row["session_id"]
        if row["profile_cluster"]:
            record["cluster"] = row["profile_cluster"]

        # ── Smart Feature Normalization ──
        # If the user changed data collection, columns names like 'Correct_Responses'
        # might be missing but present under aliases like 'hits' or 'levels_won'.

        # Map: StandardName -> list of possible aliases in raw data_json
        aliases = {
            "Correct_Responses": ["hits", "levels_won", "levels_completed", "correct_placements", "pairs_found", "solved"],
            "Incorrect_Responses": ["misses", "levels_failed", "wrong_placements", "false_alarms"],
            "Total_Actions": ["total_level_attempts", "levels_played", "rounds_played", "Total_Actions", "total_attempts"],
            "Time_Spent": ["time_s", "completion_time_s", "duration"],
            "Reaction_Time": ["avg_rt_ms", "avg_time_per_level", "avg_time_per_round"],
        }

        for std_col, fallback_list in aliases.items():
            if std_col not in record or record[std_col] is None:
                # Try to find a value in aliases
                for fb in fallback_list:
                    if fb in record and record[fb] is not None:
                        record[std_col] = record[fb]
                        break

        # Finally, provide safe numeric defaults for required columns if still missing
        core_defaults = {
            "Total_Actions": 0,
            "Correct_Responses": 0,
            "Incorrect_Responses": 0,
            "Time_Spent": 0.0,
            "Reaction_Time": 0.0,
            "Touch_Interactions": 0,
            "Age_Group": "Unknown",
            "Cognitive_Level": "Unknown",
            "Game_Completion_Status": "Not Completed",
            "Performance_Level": "Medium"
        }
        if game in HINT_METRIC_GAMES:
            core_defaults["Hint_Usage"] = 0
        for field, default in core_defaults.items():
            if field not in record or record[field] is None:
                record[field] = default

        record.pop("participant_id", None)
        record.pop("session_id", None)
        record.pop("user_id", None)
        records.append(record)

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    if "frustration_clicks" not in df.columns:
        df["frustration_clicks"] = 0
    else:
        df["frustration_clicks"] = df["frustration_clicks"].fillna(0).astype(int)

    return df


def count_game_sessions(
    game: str,
    trained_only: bool = False,
    ml_eligible_only: bool = False,
) -> int:
    """
    Return the number of sessions for a game.

    Args:
        game:         Game name.
        trained_only: If True, count only training-consumed sessions.
        ml_eligible_only: If True, count only ML-eligible sessions.
    """
    eligibility_sql = "AND s.ml_eligible = TRUE" if ml_eligible_only else ""
    if trained_only:
        query = (
            "SELECT COUNT(DISTINCT s.session_id) FROM sessions s "
            "INNER JOIN training_runs tr ON tr.session_id = s.session_id AND tr.game = s.game "
            f"WHERE s.game = %s AND tr.status = 'completed' {eligibility_sql}"
        )
    else:
        eligibility_sql = "AND ml_eligible = TRUE" if ml_eligible_only else ""
        query = f"SELECT COUNT(*) FROM sessions WHERE game=%s {eligibility_sql}"

    with get_db() as conn:
        row = conn.execute(query, (game,)).fetchone()
        return list(row.values())[0] if row else 0


def list_participant_ids(game: str = None) -> list:
    """
    Return a sorted list of unique participant IDs (usernames).
    Resolves via the users table for FK integrity.

    Args:
        game: If provided, restrict to sessions for that game only.
    """
    if game:
        query = (
            "SELECT DISTINCT u.username FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.game=%s ORDER BY u.username"
        )
        params: list = [game]
    else:
        query = (
            "SELECT DISTINCT u.username FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "ORDER BY u.username"
        )
        params = []

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [r["username"] for r in rows]


def list_user_ids(game: str = None) -> list[int]:
    """
    Return sorted list of unique user_ids that have sessions.

    Args:
        game: If provided, restrict to sessions for that game only.
    """
    if game:
        query = "SELECT DISTINCT user_id FROM sessions WHERE game=%s AND user_id IS NOT NULL ORDER BY user_id"
        params: list = [game]
    else:
        query = "SELECT DISTINCT user_id FROM sessions WHERE user_id IS NOT NULL ORDER BY user_id"
        params = []

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [r["user_id"] for r in rows]


# ── Windowed Queries ──────────────────────────────────────────────────────────

def load_recent_sessions(
    participant_id: str | int, game: str, limit: int = 10, until_iso: str = None,
) -> "pd.DataFrame":
    """
    Load the N most recent sessions for a player+game as a DataFrame.
    Uses SQL LIMIT — never loads the full history.

    Args:
        participant_id: user_id (int) or username (str) — both accepted.
        until_iso: If provided, only include sessions ingested on or before
                   this ISO timestamp (snapshot semantics).
    """
    import pandas as pd

    uid = _resolve_user_id(participant_id)
    if uid is None:
        return pd.DataFrame()

    if until_iso:
        query = (
            "SELECT data_json FROM sessions "
            "WHERE game=%s AND user_id=%s AND ingested_at <= %s "
            "ORDER BY ingested_at DESC LIMIT %s"
        )
        params = (game, uid, until_iso, limit)
    else:
        query = (
            "SELECT data_json FROM sessions "
            "WHERE game=%s AND user_id=%s "
            "ORDER BY ingested_at DESC LIMIT %s"
        )
        params = (game, uid, limit)
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return pd.DataFrame()

    records = []
    for row in rows:
        try:
            record = (json.loads(row["data_json"]) if isinstance(row["data_json"], str) else row["data_json"])
            if game in GAME_NAMES and game not in HINT_METRIC_GAMES:
                record.pop("Hint_Usage", None)
            records.append(record)
        except Exception:
            continue

    if not records:
        return pd.DataFrame()

    records.reverse()
    df = pd.DataFrame(records)

    if "frustration_clicks" not in df.columns:
        df["frustration_clicks"] = 0
    else:
        df["frustration_clicks"] = df["frustration_clicks"].fillna(0).astype(int)

    return df


def count_participant_sessions(
    participant_id: str | int, game: str = None, until_iso: str = None,
) -> int:
    """
    Count total sessions for a participant (optionally filtered by game).

    Args:
        participant_id: user_id (int) or username (str) — both accepted.
        until_iso: If provided, only count sessions ingested on or before
                   this ISO timestamp (snapshot semantics).
    """
    uid = _resolve_user_id(participant_id)
    if uid is None:
        return 0

    clauses = ["user_id=%s"]
    params: list = [uid]
    if game:
        clauses.append("game=%s")
        params.append(game)
    if until_iso:
        clauses.append("ingested_at <= %s")
        params.append(until_iso)

    where = " AND ".join(clauses)
    query = f"SELECT COUNT(*) FROM sessions WHERE {where}"

    with get_db() as conn:
        row = conn.execute(query, params).fetchone()
        return list(row.values())[0] if row else 0


def count_sessions_since(participant_id: str | int, since_iso: str, game: str = None) -> int:
    """
    Count sessions ingested after a given ISO timestamp.
    Used to check if enough new sessions exist since last report.

    Args:
        participant_id: user_id (int) or username (str) — both accepted.
    """
    uid = _resolve_user_id(participant_id)
    if uid is None:
        return 0

    if game:
        query = (
            "SELECT COUNT(*) FROM sessions "
            "WHERE user_id=%s AND game=%s AND ingested_at > %s"
        )
        params = [uid, game, since_iso]
    else:
        query = (
            "SELECT COUNT(*) FROM sessions "
            "WHERE user_id=%s AND ingested_at > %s"
        )
        params = [uid, since_iso]

    with get_db() as conn:
        row = conn.execute(query, params).fetchone()
        return list(row.values())[0] if row else 0


# ── Drift Check Tracking ──────────────────────────────────────────────────────

def insert_drift_check(
    game: str,
    run_id: str = None,
    session_ids: list[str] = None,
    n_sessions: int = 0,
    drift_detected: bool = False,
    drift_share: float = 0.0,
    status: str = "completed",
) -> int:
    """
    Record a drift check outcome for a game.

    Returns:
        The rowid of the inserted row.
    """
    now = datetime.now(timezone.utc).isoformat()
    run_id = run_id or f"drift_check_{now}"
    with get_db() as conn:
        if session_ids:
            rows = [
                (
                    game,
                    session_id,
                    run_id,
                    now,
                    status,
                    n_sessions,
                    bool(drift_detected),
                    drift_share,
                )
                for session_id in session_ids
            ]
            conn.executemany(
                """
                INSERT INTO drift_checks
                    (game, session_id, run_id, checked_at, status,
                     n_sessions, drift_detected, drift_share)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
            return len(rows)

        cursor = conn.execute(
            """
            INSERT INTO drift_checks
                (game, session_id, run_id, checked_at, status, n_sessions, drift_detected, drift_share)
            VALUES (%s, NULL, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (game, run_id, now, status, n_sessions, bool(drift_detected), drift_share),
        )
        return cursor.lastrowid


def get_latest_drift_check(game: str) -> dict | None:
    """Return the most recent drift check record for a game."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM drift_checks WHERE game=%s
            ORDER BY checked_at DESC LIMIT 1
            """,
            (game,),
        ).fetchone()
    return dict(row) if row else None


def insert_drift_history_batch(rows: list[dict]) -> int:
    """Bulk-insert per-feature drift history records.

    Each dict should have keys: game, feature, drift_type, drifted,
    stat_name, p_value, effect_size (optional), run_id.

    Returns:
        Number of rows inserted.
    """
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    values = [
        (
            r.get("game", ""),
            r.get("feature", ""),
            r.get("drift_type", "data"),
            bool(r.get("drifted", False)),
            r.get("stat_name"),
            r.get("p_value"),
            r.get("effect_size"),
            r.get("run_id"),
            now,
        )
        for r in rows
    ]
    with get_db() as conn:
        conn.executemany(
            """
            INSERT INTO drift_history
                (game, feature, drift_type, drifted,
                 stat_name, p_value, effect_size, run_id, checked_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            values,
        )
        return len(values)


def load_drift_history(
    game: str,
    drift_type: str = None,
    feature: str = None,
    limit: int = 100,
) -> list[dict]:
    """Load per-feature drift history for a game.

    Args:
        game:       Game name.
        drift_type: Optional filter ('data', 'prediction', 'label_dist', 'concept').
        feature:    Optional filter by feature name.
        limit:      Max rows to return (default 100).

    Returns:
        List of drift history dicts, most recent first.
    """
    clauses = ["game = %s"]
    params: list = [game]
    if drift_type:
        clauses.append("drift_type = %s")
        params.append(drift_type)
    if feature:
        clauses.append("feature = %s")
        params.append(feature)
    params.append(limit)

    where = " AND ".join(clauses)
    query = (
        f"SELECT * FROM drift_history WHERE {where} "
        f"ORDER BY checked_at DESC LIMIT %s"
    )
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ── Report Run Tracking ───────────────────────────────────────────────────────

def insert_report_run(
    participant_id: str | int,
    run_id: str = None,
    report_path: str = None,
    status: str = "completed",
    report_group_id: str = None,
    locale: str = None,
    snapshot_until: str = None,
    session_count_at_snapshot: int = None,
    is_canonical: bool = False,
) -> int:
    """
    Record a report generation event for a participant.

    Args:
        participant_id: user_id (int) or username (str) — both accepted.

    Returns:
        The rowid of the inserted row, or -1 if user not found.
    """
    uid = _resolve_user_id(participant_id)
    if uid is None:
        log.warning("insert_report_run: user not found for %r", participant_id)
        return -1

    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO report_runs
                (user_id, run_id, generated_at, status, report_path,
                 report_group_id, locale, snapshot_until,
                 session_count_at_snapshot, is_canonical)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                uid, run_id, now, status, report_path,
                report_group_id, locale, snapshot_until,
                session_count_at_snapshot, bool(is_canonical),
            ),
        )
        return cursor.lastrowid


def get_latest_report_run(participant_id: str | int) -> dict | None:
    """Return the most recent report run record for a participant.

    Args:
        participant_id: user_id (int) or username (str) — both accepted.
    """
    uid = _resolve_user_id(participant_id)
    if uid is None:
        return None
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM report_runs WHERE user_id=%s
            ORDER BY generated_at DESC LIMIT 1
            """,
            (uid,),
        ).fetchone()
    return dict(row) if row else None


# ── Baseline Snapshots ────────────────────────────────────────────────────────

def get_baseline(participant_id: str | int, game: str) -> dict | None:
    """Read the cached baseline snapshot for a player+game.

    Args:
        participant_id: user_id (int) or username (str) — both accepted.
    """
    uid = _resolve_user_id(participant_id)
    if uid is None:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT metrics_json FROM baselines WHERE game=%s AND user_id=%s",
            (game, uid),
        ).fetchone()
    if row:
        try:
            return (json.loads(row["metrics_json"]) if isinstance(row["metrics_json"], str) else row["metrics_json"])
        except Exception:
            return None
    return None


def save_baseline(participant_id: str | int, game: str, metrics: dict, n_sessions: int = 5) -> bool:
    """
    Write-once: save a baseline snapshot. Silently skips if one already exists.
    Returns True if a new baseline was created.

    Args:
        participant_id: user_id (int) or username (str) — both accepted.
    """
    uid = _resolve_user_id(participant_id)
    if uid is None:
        return False
    try:
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO baselines
                    (game, user_id, metrics_json, n_sessions, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (game, user_id) DO NOTHING
                RETURNING id
                """,
                (
                    game,
                    uid,
                    json.dumps(metrics),
                    n_sessions,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cursor.lastrowid is not None
    except Exception:
        return False


def ensure_baseline(participant_id: str | int, game: str, min_sessions: int = 5) -> dict | None:
    """
    Check if a baseline exists; if not and the player has enough sessions,
    compute and save one from the earliest sessions.
    Returns the baseline metrics dict, or None if not enough data.

    Args:
        participant_id: user_id (int) or username (str) — both accepted.
    """
    existing = get_baseline(participant_id, game)
    if existing:
        return existing

    uid = _resolve_user_id(participant_id)
    if uid is None:
        return None

    n = count_participant_sessions(uid, game)
    if n < min_sessions:
        return None

    import pandas as pd
    query = (
        "SELECT data_json FROM sessions "
        "WHERE game=%s AND user_id=%s "
        "ORDER BY ingested_at ASC LIMIT %s"
    )
    with get_db() as conn:
        rows = conn.execute(query, (game, uid, min_sessions)).fetchall()

    if len(rows) < min_sessions:
        return None

    records = []
    for row in rows:
        try:
            record = (json.loads(row["data_json"]) if isinstance(row["data_json"], str) else row["data_json"])
            if game not in HINT_METRIC_GAMES:
                record.pop("Hint_Usage", None)
            records.append(record)
        except Exception:
            continue

    if len(records) < min_sessions:
        return None

    df = pd.DataFrame(records)
    if "frustration_clicks" not in df.columns:
        df["frustration_clicks"] = 0

    def _safe(col):
        if col in df.columns and df[col].notna().any():
            return round(float(df[col].mean()), 3)
        return None

    metrics = {
        "avg_correct": _safe("Correct_Responses"),
        "avg_incorrect": _safe("Incorrect_Responses"),
        "avg_rt": _safe("Reaction_Time"),
        "avg_time_spent": _safe("Time_Spent"),
        "avg_frustration_clicks": _safe("frustration_clicks"),
        "avg_total_actions": _safe("Total_Actions"),
    }

    if metrics["avg_correct"] is not None and metrics["avg_incorrect"] is not None:
        total = metrics["avg_correct"] + metrics["avg_incorrect"]
        metrics["accuracy_rate"] = round(metrics["avg_correct"] / max(0.01, total), 3)

    save_baseline(uid, game, metrics, min_sessions)
    return metrics


# ── Anomaly Flags ─────────────────────────────────────────────────────────────

def insert_anomaly_flags(game: str, flags_df: "pd.DataFrame", run_id: str = None) -> int:
    """
    Bulk-upsert anomaly flags from the pipeline's DataFrame into the DB.

    Args:
        game:     Game name (e.g. 'gonogo').
        flags_df: DataFrame with columns: Participant_ID, Game_Session_ID,
                  cluster, if_score, ae_error, if_flag, ae_flag, anomaly_flag.
        run_id:   Optional MLflow run ID to link this detection run.

    Returns:
        Number of rows inserted/updated.
    """
    now = datetime.now(timezone.utc).isoformat()
    records = flags_df.to_dict(orient="records")
    rows = []
    with get_db() as conn:
        for r in records:
            raw_pid = r.get("Participant_ID", "")
            uid = _resolve_user_id(raw_pid, conn=conn)
            if uid is None:
                log.warning("insert_anomaly_flags: user not found for %r — skipping", raw_pid)
                continue
            rows.append((
                game,
                uid,
                str(r.get("Game_Session_ID", "")),
                str(r.get("cluster", "")) if r.get("cluster") is not None else None,
                float(r.get("if_score", 0)),
                float(r.get("ae_error", 0)),
                bool(r.get("if_flag", False)),
                bool(r.get("ae_flag", False)),
                bool(r.get("anomaly_flag", False)),
                run_id,
                now,
            ))
        if rows:
            cursor = conn.executemany(
                """
                INSERT INTO anomaly_flags
                    (game, user_id, session_id, cluster,
                     if_score, ae_error, if_flag, ae_flag, anomaly_flag,
                     pipeline_run_id, flagged_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (game, session_id) DO UPDATE SET
                    user_id=EXCLUDED.user_id, cluster=EXCLUDED.cluster, if_score=EXCLUDED.if_score,
                    ae_error=EXCLUDED.ae_error, if_flag=EXCLUDED.if_flag, ae_flag=EXCLUDED.ae_flag,
                    anomaly_flag=EXCLUDED.anomaly_flag, pipeline_run_id=EXCLUDED.pipeline_run_id,
                    flagged_at=EXCLUDED.flagged_at
                """,
                rows,
            )
            return cursor.rowcount
        return 0


def get_anomaly_summary(game: str, school_id: int | None = None) -> dict | None:
    """
    Query the anomaly_flags table for a game and return a summary suitable
    for the admin dashboard API (KPIs, profile rates, score arrays, top flagged).
    """
    with get_db() as conn:
        join_sql = "LEFT JOIN users u ON u.id = af.user_id" if school_id else ""
        where_sql = "AND u.school_id=%s" if school_id else ""
        params: list = [game, school_id] if school_id else [game]

        row = conn.execute(
            f"SELECT COUNT(*) as total, "
            f"SUM(af.anomaly_flag::int) as combined, "
            f"SUM(af.if_flag::int) as if_cnt, "
            f"SUM(af.ae_flag::int) as ae_cnt "
            f"FROM anomaly_flags af {join_sql} WHERE af.game=%s {where_sql}",
            params,
        ).fetchone()

    if not row or row["total"] == 0:
        return {
            "total_sessions": 0,
            "combined_flagged": 0,
            "if_flagged": 0,
            "ae_flagged": 0,
            "flag_rate": 0.0,
            "profile_rates": [],
            "if_scores": [],
            "ae_errors": [],
            "top_flagged": [],
        }

    total = row["total"]
    combined = row["combined"] or 0
    if_cnt = row["if_cnt"] or 0
    ae_cnt = row["ae_cnt"] or 0

    with get_db() as conn:
        profile_rows = conn.execute(
            f"SELECT af.cluster, COUNT(*) as cnt, SUM(af.anomaly_flag::int) as flagged "
            f"FROM anomaly_flags af {join_sql} WHERE af.game=%s {where_sql} GROUP BY af.cluster ORDER BY flagged DESC",
            params,
        ).fetchall()

        score_rows = conn.execute(
            f"SELECT af.if_score, af.ae_error FROM anomaly_flags af {join_sql} WHERE af.game=%s {where_sql}",
            params,
        ).fetchall()

        top_rows = conn.execute(
            f"SELECT af.user_id, u.username, "
            f"SUM(af.anomaly_flag::int) as flagged, "
            f"COUNT(*) as total "
            f"FROM anomaly_flags af "
            f"LEFT JOIN users u ON u.id = af.user_id "
            f"WHERE af.game=%s {where_sql} "
            f"GROUP BY af.user_id, u.username "
            f"HAVING SUM(af.anomaly_flag::int) > 0 "
            f"ORDER BY flagged DESC LIMIT 10",
            params,
        ).fetchall()

    profile_rates = [
        {
            "profile": r["cluster"] or "Unknown",
            "total": r["cnt"],
            "flagged": r["flagged"] or 0,
            "rate": round((r["flagged"] or 0) / max(1, r["cnt"]), 4),
        }
        for r in profile_rows
    ]
    import random
    if_scores = [r["if_score"] for r in score_rows if r["if_score"] is not None]
    ae_errors = [r["ae_error"] for r in score_rows if r["ae_error"] is not None]

    if len(if_scores) > 1000:
        if_scores = random.sample(if_scores, 1000)
    if len(ae_errors) > 1000:
        ae_errors = random.sample(ae_errors, 1000)

    top_flagged = [
        {
            "participant_id": r["username"] or str(r["user_id"]),
            "flagged_sessions": r["flagged"] or 0,
            "total_sessions": r["total"],
            "rate": round((r["flagged"] or 0) / max(1, r["total"]), 4),
        }
        for r in top_rows
    ]

    return {
        "total_sessions": total,
        "combined_flagged": combined,
        "if_flagged": if_cnt,
        "ae_flagged": ae_cnt,
        "flag_rate": round(combined / max(1, total), 4),
        "profile_rates": profile_rates,
        "if_scores": if_scores,
        "ae_errors": ae_errors,
        "top_flagged": top_flagged,
    }


# ── Classification helpers ────────────────────────────────────────────────────

def insert_classification_results(rows: list[dict]) -> int:
    """
    Bulk upsert classification predictions.
    Each dict: game, participant_id (str or int user_id), actual_cluster,
               predicted_cluster, confidence, pipeline_run_id, classified_at

    participant_id is resolved to user_id; rows for unknown users are skipped.
    """
    if not rows:
        return 0
    inserted = 0
    with get_db() as conn:
        for r in rows:
            raw_pid = r.get("participant_id", r.get("user_id", ""))
            uid = _resolve_user_id(raw_pid, conn=conn)
            if uid is None:
                log.warning("insert_classification_results: user not found for %r — skipping", raw_pid)
                continue
            conn.execute(
                """
                INSERT INTO classification_results
                    (game, user_id, actual_cluster, predicted_cluster,
                     confidence, pipeline_run_id, classified_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (game, user_id) DO UPDATE SET
                    actual_cluster=EXCLUDED.actual_cluster, predicted_cluster=EXCLUDED.predicted_cluster,
                    confidence=EXCLUDED.confidence, pipeline_run_id=EXCLUDED.pipeline_run_id,
                    classified_at=EXCLUDED.classified_at
                """,
                (
                    r["game"],
                    uid,
                    r.get("actual_cluster"),
                    r.get("predicted_cluster"),
                    r.get("confidence"),
                    r.get("pipeline_run_id"),
                    r["classified_at"],
                ),
            )
            inserted += 1
    return inserted


def load_classification_predictions_df(game: str) -> "pd.DataFrame":
    """
    Load recent classification predictions for a game into a DataFrame.
    Returns columns: Participant_ID, cluster, predicted_cluster, confidence
    """
    import pandas as pd
    query = """
        SELECT
            u.username AS "Participant_ID",
            cr.actual_cluster AS cluster,
            cr.predicted_cluster,
            cr.confidence
        FROM classification_results cr
        JOIN users u ON cr.user_id = u.id
        WHERE cr.game = %s
    """
    with get_db() as conn:
        rows = conn.execute(query, (game,)).fetchall()
        return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def insert_cross_game_results(merged_df: "pd.DataFrame") -> int:
    """
    Persist composite cross-game predictions to the cross_game_results table.

    Expected DataFrame columns:
        Participant_ID, cluster, majority_pred, agreement_ratio, all_agree,
        plus per-game pred_* columns.

    Returns:
        Number of rows inserted/updated.
    """
    now = datetime.now(timezone.utc).isoformat()
    pred_cols = [c for c in merged_df.columns if c.startswith("pred_")]
    inserted = 0

    with get_db() as conn:
        for _, row in merged_df.iterrows():
            uid = _resolve_user_id(row.get("Participant_ID", ""), conn=conn)
            if uid is None:
                log.warning(
                    "insert_cross_game_results: user not found for %r — skipping",
                    row.get("Participant_ID"),
                )
                continue

            preds = {c: str(row[c]) for c in pred_cols if c in row}
            conn.execute(
                """
                INSERT INTO cross_game_results
                    (user_id, cluster, majority_pred, agreement_ratio,
                     all_agree, predictions_json, computed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    cluster = EXCLUDED.cluster,
                    majority_pred = EXCLUDED.majority_pred,
                    agreement_ratio = EXCLUDED.agreement_ratio,
                    all_agree = EXCLUDED.all_agree,
                    predictions_json = EXCLUDED.predictions_json,
                    computed_at = EXCLUDED.computed_at
                """,
                (
                    uid,
                    str(row.get("cluster", "")) if row.get("cluster") is not None else None,
                    str(row.get("majority_pred", "")) if row.get("majority_pred") is not None else None,
                    float(row.get("agreement_ratio", 0)),
                    bool(row.get("all_agree", False)),
                    json.dumps(preds),
                    now,
                ),
            )
            inserted += 1

    return inserted


def load_cross_game_results_df() -> "pd.DataFrame":
    """
    Load all cross-game composite predictions into a DataFrame.

    Returns columns: Participant_ID, cluster, majority_pred,
    agreement_ratio, all_agree, plus per-game pred_* columns
    reconstructed from the stored JSON.
    """
    import pandas as pd

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                u.username AS "Participant_ID",
                cg.cluster,
                cg.majority_pred,
                cg.agreement_ratio,
                cg.all_agree,
                cg.predictions_json
            FROM cross_game_results cg
            JOIN users u ON cg.user_id = u.id
            """
        ).fetchall()

    if not rows:
        return pd.DataFrame()

    records = []
    for row in rows:
        rec = {
            "Participant_ID": row["Participant_ID"],
            "cluster": row["cluster"],
            "majority_pred": row["majority_pred"],
            "agreement_ratio": row["agreement_ratio"],
            "all_agree": bool(row["all_agree"]),
        }
        try:
            preds = (json.loads(row["predictions_json"]) if isinstance(row["predictions_json"], str) else row["predictions_json"])
            rec.update(preds)
        except (json.JSONDecodeError, TypeError):
            pass
        records.append(rec)

    return pd.DataFrame(records)


def load_anomaly_flags_df(game: str) -> "pd.DataFrame":
    """
    Load anomaly flags for a game into a DataFrame for the dashboard.

    Returns columns: Participant_ID, Game_Session_ID, cluster,
    if_score, ae_error, if_flag, ae_flag, anomaly_flag.
    """
    import pandas as pd

    query = """
        SELECT
            u.username      AS "Participant_ID",
            af.session_id   AS "Game_Session_ID",
            af.cluster,
            af.if_score,
            af.ae_error,
            af.if_flag,
            af.ae_flag,
            af.anomaly_flag
        FROM anomaly_flags af
        JOIN users u ON af.user_id = u.id
        WHERE af.game = %s
    """
    with get_db() as conn:
        rows = conn.execute(query, (game,)).fetchall()
        return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def get_classification_summary(game: str, school_id: int | None = None) -> dict:
    """
    Returns classification summary for a game:
    accuracy, confusion matrix, per-class metrics, misclassified participants.
    """
    with get_db() as conn:
        where_sql = "AND u.school_id=%s" if school_id else ""
        params: list = [game, school_id] if school_id else [game]
        rows = conn.execute(
            f"SELECT cr.user_id, u.username, cr.actual_cluster, cr.predicted_cluster, cr.confidence "
            f"FROM classification_results cr "
            f"LEFT JOIN users u ON u.id = cr.user_id "
            f"WHERE cr.game=%s {where_sql}",
            params,
        ).fetchall()

    if not rows:
        return {"total": 0}

    total = len(rows)
    correct = sum(1 for r in rows if r["actual_cluster"] == r["predicted_cluster"])
    accuracy = round(correct / max(1, total), 4)
    avg_confidence = round(
        sum(r["confidence"] or 0 for r in rows) / max(1, total), 4
    )

    classes = sorted(set(r["actual_cluster"] for r in rows if r["actual_cluster"]))

    cm = {}
    for c in classes:
        cm[c] = {}
        for c2 in classes:
            cm[c][c2] = 0
    for r in rows:
        a, p = r["actual_cluster"], r["predicted_cluster"]
        if a in cm and p in cm[a]:
            cm[a][p] += 1

    per_class = []
    for c in classes:
        tp = cm[c].get(c, 0)
        fp = sum(cm[other].get(c, 0) for other in classes if other != c)
        fn = sum(v for k, v in cm[c].items() if k != c)
        precision = round(tp / max(1, tp + fp), 4)
        recall = round(tp / max(1, tp + fn), 4)
        f1 = round(2 * precision * recall / max(1e-9, precision + recall), 4)
        support = sum(cm[c].values())
        per_class.append({
            "class": c,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
        })

    misclassified = [
        {
            "participant_id": r["username"] or str(r["user_id"]),
            "actual": r["actual_cluster"],
            "predicted": r["predicted_cluster"],
            "confidence": round(r["confidence"] or 0, 4),
        }
        for r in rows
        if r["actual_cluster"] != r["predicted_cluster"]
    ]
    misclassified.sort(key=lambda x: x["confidence"])

    return {
        "total": total,
        "accuracy": accuracy,
        "avg_confidence": avg_confidence,
        "classes": classes,
        "confusion_matrix": cm,
        "per_class": per_class,
        "misclassified": misclassified[:20],
    }

# ── Conners Concordance Helpers ───────────────────────────────────────────────

def load_conners_for_participants() -> "pd.DataFrame":
    """
    Load Conners scores for all participants that have one.

    Returns a DataFrame with columns:
        Participant_ID, conners_score, conners_data
    """
    import pandas as pd
    query = """
        SELECT u.username AS "Participant_ID",
               up.conners_score,
               up.conners_data
        FROM user_profiles up
        JOIN users u ON u.id = up.user_id
        WHERE up.conners_score IS NOT NULL
    """
    with get_db() as conn:
        rows = conn.execute(query).fetchall()
        return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def insert_conners_concordance(rows: list[dict]) -> int:
    """
    Bulk-upsert concordance results.

    Each dict: participant_id, conners_score, conners_tscore, conners_tier,
               ml_prediction, agreement_ratio, concordance, concordance_detail,
               original_confidence, adjusted_confidence, correction_flag.
    """
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    with get_db() as conn:
        for r in rows:
            uid = _resolve_user_id(r["participant_id"], conn=conn)
            if uid is None:
                continue
            conn.execute(
                """
                INSERT INTO conners_concordance
                    (user_id, conners_score, conners_tscore, conners_tier,
                     ml_prediction, agreement_ratio, concordance,
                     concordance_detail, original_confidence,
                     adjusted_confidence, correction_flag, computed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    conners_score = EXCLUDED.conners_score,
                    conners_tscore = EXCLUDED.conners_tscore,
                    conners_tier = EXCLUDED.conners_tier,
                    ml_prediction = EXCLUDED.ml_prediction,
                    agreement_ratio = EXCLUDED.agreement_ratio,
                    concordance = EXCLUDED.concordance,
                    concordance_detail = EXCLUDED.concordance_detail,
                    original_confidence = EXCLUDED.original_confidence,
                    adjusted_confidence = EXCLUDED.adjusted_confidence,
                    correction_flag = EXCLUDED.correction_flag,
                    computed_at = EXCLUDED.computed_at
                """,
                (
                    uid,
                    r.get("conners_score"),
                    r.get("conners_tscore"),
                    r.get("conners_tier"),
                    r.get("ml_prediction"),
                    r.get("agreement_ratio"),
                    r.get("concordance"),
                    r.get("concordance_detail"),
                    r.get("original_confidence"),
                    r.get("adjusted_confidence"),
                    r.get("correction_flag"),
                    now,
                ),
            )
            inserted += 1
    return inserted


def get_conners_concordance(participant_id: str | int) -> dict | None:
    """Return the concordance record for a single participant."""
    uid = _resolve_user_id(participant_id)
    if uid is None:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM conners_concordance WHERE user_id = %s",
            (uid,),
        ).fetchone()
    return dict(row) if row else None


# ── Dashboard Cache ───────────────────────────────────────────────────────────

def read_dashboard_cache() -> dict | None:
    """Read the pre-computed dashboard stats from the cache table."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT stats_json, updated_at FROM dashboard_cache WHERE id = 1"
        ).fetchone()
    if row:
        try:
            return (json.loads(row["stats_json"]) if isinstance(row["stats_json"], str) else row["stats_json"])
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def write_dashboard_cache(stats: dict) -> None:
    """Upsert the single-row dashboard cache with new stats."""
    now = datetime.now(timezone.utc).isoformat()
    stats_json = json.dumps(stats)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO dashboard_cache (id, stats_json, updated_at)
            VALUES (1, %s, %s)
            ON CONFLICT(id) DO UPDATE SET stats_json = excluded.stats_json,
                                         updated_at = excluded.updated_at
            """,
            (stats_json, now),
        )


# ── Behavioral Event Helpers ──────────────────────────────────────────────────

def insert_behavioral_events(game: str, events: list[dict]) -> int:
    """
    Bulk-insert behavioral events for a session.

    Args:
        game:   Game name (e.g. 'gonogo').
        events: List of dicts with keys: participant_id, session_id,
                event_type, event_timestamp, event_data (optional JSON).

    Returns:
        Number of rows inserted.
    """
    if not events:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    with get_db() as conn:
        for e in events:
            raw_pid = e.get("participant_id", e.get("user_id", ""))
            uid = _resolve_user_id(raw_pid, conn=conn)
            if uid is None:
                log.warning("insert_behavioral_events: user not found for %r — skipping", raw_pid)
                continue
            conn.execute(
                """
                INSERT INTO behavioral_events
                    (game, user_id, session_id, event_type,
                     event_timestamp, event_data, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    game,
                    uid,
                    e.get("session_id", ""),
                    e.get("event_type", "unknown"),
                    float(e.get("event_timestamp", 0)),
                    json.dumps(e.get("event_data")) if e.get("event_data") else None,
                    now,
                ),
            )
            inserted += 1
    return inserted


def load_session_events(session_id: str) -> list[dict]:
    """
    Load all behavioral events for a session, ordered by timestamp.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT event_type, event_timestamp, event_data "
            "FROM behavioral_events WHERE session_id=%s "
            "ORDER BY event_timestamp ASC",
            (session_id,),
        ).fetchall()

    result = []
    for row in rows:
        evt = {
            "event_type": row["event_type"],
            "event_timestamp": row["event_timestamp"],
        }
        if row["event_data"]:
            try:
                evt["event_data"] = (json.loads(row["event_data"]) if isinstance(row["event_data"], str) else row["event_data"])
            except Exception:
                evt["event_data"] = row["event_data"]
        result.append(evt)
    return result


# ── Management Helpers ────────────────────────────────────────────────────────

def delete_school(school_id: int) -> bool:
    """
    Delete a school. Users assigned to this school are reassigned to the
    'Default School' (ID 1) before deletion to prevent orphaned records.
    """
    if school_id == 1:
        log.warning("delete_school: attempt to delete Default School (id=1) blocked")
        return False

    with get_db() as conn:
        # Reassign users to school 1
        conn.execute("UPDATE users SET school_id = 1 WHERE school_id = %s", (school_id,))
        # Delete the school
        cursor = conn.execute("DELETE FROM schools WHERE id = %s", (school_id,))
        return cursor.rowcount > 0


def delete_user(user_id: int) -> bool:
    """
    Delete a user account.
    Note: Foreign keys for sessions, analytics, etc. should handle this via
    CASCADE if configured, or manual cleanup (currently relying on FKs).
    """
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
        return cursor.rowcount > 0


def update_user_school(user_id: int, school_id: int) -> bool:
    """Change which school a user is assigned to."""
    with get_db() as conn:
        cursor = conn.execute("UPDATE users SET school_id = %s WHERE id = %s", (school_id, user_id))
        return cursor.rowcount > 0

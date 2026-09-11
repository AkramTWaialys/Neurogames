"""
Pydantic schemas for the NeuroGames web-facing API.

Provides request/response models for participant profiles, session history,
game metadata, and clinical reports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── Game Metadata ─────────────────────────────────────────────────────────────

class GameInfo(BaseModel):
    """Public metadata about a cognitive game."""
    id: str = Field(..., examples=["gonogo"])
    name: str = Field(..., examples=["Go / No-Go"])
    icon: str = Field(..., examples=["🎯"])
    cognitive_domain: str = Field(..., examples=["Inhibitory control"])
    description: str
    difficulty_system: str = Field(..., examples=["Adaptive (DifficultyProfile)"])
    color: str = Field(..., examples=["#6C5CE7"])
    runtime_type: Optional[str] = None
    entry_url: Optional[str] = None


class GameListResponse(BaseModel):
    """Response for GET /api/games."""
    games: list[GameInfo]
    count: int


# ── Session Submission ────────────────────────────────────────────────────────

class SessionSubmission(BaseModel):
    """
    Simplified session payload for the NeuroGames frontend.

    Vocabulary is aligned with the MLOps BaseSessionSchema so sessions
    can be forwarded to the ingestion pipeline without transformation.
    """
    participant_id: str = Field(..., examples=["P_00310C"])
    game_session_id: str = Field(..., examples=["S_00310C_001"])
    game_id: str = Field(..., examples=["gonogo"])
    age_group: Literal["6-8", "9-11", "12-14"] = "9-11"
    cognitive_level: Literal["Advanced", "Developing", "Early"] = "Developing"
    performance_level: Literal["Optimal", "Struggling", "Disengaged"] = "Struggling"
    time_spent: float = Field(..., ge=0, examples=[120.5])
    total_actions: int = Field(..., ge=0, examples=[45])
    correct_responses: int = Field(..., ge=0, examples=[38])
    incorrect_responses: int = Field(..., ge=0, examples=[7])
    reaction_time: float = Field(..., ge=0, examples=[0.85])

    model_config = {"extra": "allow"}


class SessionResponse(BaseModel):
    """Response after successful session upload."""
    status: str = "ok"
    game_id: str
    session_id: str
    participant_id: str
    timestamp: datetime


# ── Participant Summary ───────────────────────────────────────────────────────

class GameStat(BaseModel):
    """Per-game stats for a participant."""
    game_id: str
    game_name: str
    sessions_played: int
    avg_accuracy: float
    best_score: Optional[float] = None
    last_played: Optional[str] = None


class ParticipantSummary(BaseModel):
    """Aggregated profile for a participant."""
    participant_id: str
    total_sessions: int
    avg_accuracy: float
    games: list[GameStat]
    xp: int = 0
    level: str = "Débutant"


# ── Individual Session Detail ─────────────────────────────────────────────────

class SessionDetail(BaseModel):
    """Single session row enriched with game metadata."""
    id: int
    game_id: str
    game_name: str
    icon: str
    correct: int
    total_actions: int
    accuracy: float               # 0-100 scale
    level: int
    duration_s: float
    stars: str
    stored_at: str                # ISO timestamp from DB
    reaction_time: Optional[float] = None
    performance_level: Optional[str] = None


class SessionHistoryResponse(BaseModel):
    """Response for GET /api/participants/{pid}/sessions."""
    participant_id: str
    sessions: list[SessionDetail]
    count: int

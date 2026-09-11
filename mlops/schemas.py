"""
MLOps Pydantic Schemas
=======================
Validation models for incoming game session data.
Each game has its own schema with core + game-specific fields.
"""

from pydantic import BaseModel, Field, field_validator


CORE_TELEMETRY_FIELDS = [
    "Participant_ID",
    "Game_Session_ID",
    "Age_Group",
    "Cognitive_Level",
    "Game_Completion_Status",
    "Performance_Level",
    "Time_Spent",
    "Total_Actions",
    "Correct_Responses",
    "Incorrect_Responses",
    "Hint_Usage",
    "Touch_Interactions",
    "Reaction_Time",
    "pause_count",
    "retry_count",
    "rt_cv",
    "level",
    "max_level_reached",
    "rounds_played",
    "rounds_passed",
    "session_outcome",
    "schema_version",
]

# ── Core fields (shared by all games) ─────────────────────────────────────────

class BaseSessionSchema(BaseModel):
    """Fields present in every game session upload."""

    Participant_ID: str = Field(..., description="Unique participant identifier")
    Game_Session_ID: str = Field(..., description="Unique session identifier")
    Age_Group: str = Field(..., description="e.g. '6-8', '9-11', '12-14'")
    Cognitive_Level: str = Field(..., description="e.g. 'Low', 'Medium', 'High'")
    Game_Completion_Status: str = Field(..., description="'Completed' or 'Not Completed'")
    Performance_Level: str = Field(..., description="e.g. 'Low', 'Medium', 'High'")
    Time_Spent: float = Field(..., ge=0, description="Seconds spent on session")
    Total_Actions: int = Field(..., ge=0)
    Correct_Responses: int = Field(..., ge=0)
    Incorrect_Responses: int = Field(..., ge=0)
    Hint_Usage: int = Field(default=0, ge=0)
    Touch_Interactions: int = Field(..., ge=0)
    Reaction_Time: float = Field(..., ge=0, description="Average reaction time in seconds")
    cluster: str = Field(
        default="Unknown",
        description="ADHD profile label (assigned by pipeline, can be 'Unknown' on upload)"
    )
    level: int = Field(default=1, ge=0, description="Current or final session level")
    max_level_reached: int = Field(default=1, ge=0, description="Max difficulty level reached")
    pause_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    rt_cv: float = Field(default=0.0, ge=0)
    rounds_played: int = Field(default=0, ge=0)
    rounds_passed: int = Field(default=0, ge=0)
    session_outcome: str = Field(default="completed")
    schema_version: str = Field(default="1.0")

    @field_validator("Game_Completion_Status")
    @classmethod
    def validate_completion(cls, v):
        allowed = {"Completed", "Not Completed", "completed", "not completed"}
        if v not in allowed:
            raise ValueError(f"Game_Completion_Status must be one of {allowed}")
        return v


class DynamicGameSession(BaseSessionSchema):
    """
    Generic schema for registered ADHD-compatible game modules.

    Built-in games keep their detailed schemas below. A future compatible game
    still has to send the shared ADHD telemetry fields, and the adapter checks
    its declared game-specific feature set separately.
    """

    model_config = {"extra": "allow"}


# ── Game-specific schemas ─────────────────────────────────────────────────────

class GoNoGoSession(BaseSessionSchema):
    """Go/No-Go game session with inhibitory control metrics."""

    hits: int = Field(default=0, ge=0)
    misses: int = Field(default=0, ge=0)
    false_alarms: int = Field(default=0, ge=0)
    correct_rejections: int = Field(default=0, ge=0)
    commission_error_rate: float = Field(default=0.0, ge=0, le=1)
    omission_error_rate: float = Field(default=0.0, ge=0, le=1)
    d_prime_approx: float = Field(default=0.0)
    rt_variability_ms: float = Field(default=0.0, ge=0)


class MemorySession(BaseSessionSchema):
    """Memory Match game session."""

    pairs_found: int = Field(default=0, ge=0)
    total_pairs: int = Field(default=0, ge=0)
    total_attempts: int = Field(default=0, ge=0)
    match_accuracy: float = Field(default=0.0, ge=0, le=1)
    completion_time_s: float = Field(default=0.0, ge=0)


class TrackingSession(BaseSessionSchema):
    """Visual Tracking game session."""

    hits: int = Field(default=0, ge=0)
    misses: int = Field(default=0, ge=0)
    hit_rate: float = Field(default=0.0, ge=0, le=1)
    avg_rt_ms: float = Field(default=0.0, ge=0)
    rt_std_ms: float = Field(default=0.0, ge=0)
    avg_target_speed: float = Field(default=0.0, ge=0)
    avg_distractor_count: float = Field(default=0.0, ge=0)


class ShapesSession(BaseSessionSchema):
    """Shape Sorting game session (session-level aggregates)."""

    levels_played: int = Field(default=0, ge=0)
    levels_won: int = Field(default=0, ge=0)
    levels_completed: int = Field(default=0, ge=0)
    levels_failed: int = Field(default=0, ge=0)
    redo_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    total_level_attempts: int = Field(default=0, ge=0)
    total_moves: int = Field(default=0, ge=0)
    session_accuracy: float = Field(default=0.0, ge=0, le=1)
    avg_moves_per_level: float = Field(default=0.0, ge=0)
    avg_time_per_level: float = Field(default=0.0, ge=0)
    avg_move_efficiency: float = Field(default=0.0, ge=0, le=2)
    avg_rotations: float = Field(default=0.0, ge=0)

    # Per-level detail fields can be present in round_details or flattened rows.
    moves_used: int = Field(default=0, ge=0)
    optimal_moves: int = Field(default=0, ge=0)
    move_efficiency: float = Field(default=0.0, ge=0, le=2)
    time_s: float = Field(default=0.0, ge=0)
    solved: int = Field(default=0, ge=0, le=1)
    completed: int = Field(default=0, ge=0, le=1)
    failed: int = Field(default=0, ge=0, le=1)
    redo: int = Field(default=0, ge=0, le=1)


class PuzzleSession(BaseSessionSchema):
    """Puzzle game session."""

    Hint_Usage: int = Field(default=0, ge=0)
    levels_completed: int = Field(default=0, ge=0)
    rounds_played: int = Field(default=0, ge=0)
    rounds_passed: int = Field(default=0, ge=0)
    correct_placements: int = Field(default=0, ge=0)
    wrong_placements: int = Field(default=0, ge=0)
    total_pieces: int = Field(default=0, ge=0)
    pieces_per_round: int = Field(default=0, ge=0)
    placement_accuracy: float = Field(default=0.0, ge=0, le=1)
    avg_mastery_index: float = Field(default=0.0, ge=0, le=1)
    avg_planning_score: float = Field(default=0.0, ge=0, le=2)
    avg_impulsivity_score: float = Field(default=0.0, ge=0, le=1)
    avg_attention_score: float = Field(default=0.0, ge=0, le=1)
    avg_frustration_score: float = Field(default=0.0, ge=0, le=1)
    avg_time_score: float = Field(default=0.0, ge=0, le=1)
    avg_time_per_round: float = Field(default=0.0, ge=0)
    pieces_count: int = Field(default=0, ge=0)
    mastery_index: float = Field(default=0.0, ge=0, le=1)
    planning_score: float = Field(default=0.0, ge=0, le=2)
    impulsivity_score: float = Field(default=0.0, ge=0, le=1)
    attention_score: float = Field(default=0.0, ge=0, le=1)
    frustration_score: float = Field(default=0.0, ge=0, le=1)
    time_score: float = Field(default=0.0, ge=0, le=1)


# ── Schema registry ──────────────────────────────────────────────────────────

GAME_SCHEMAS = {
    "gonogo": GoNoGoSession,
    "memory": MemorySession,
    "tracking": TrackingSession,
    "shapes": ShapesSession,
    "puzzle": PuzzleSession,
}


def get_schema(game_name: str):
    """Return the built-in Pydantic schema class for a given game name."""
    schema = GAME_SCHEMAS.get(game_name)
    if schema is None:
        raise ValueError(
            f"No built-in schema for '{game_name}'. Valid built-ins: {list(GAME_SCHEMAS.keys())}"
        )
    return schema

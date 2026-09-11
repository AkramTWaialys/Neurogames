"""
Behavioral Event Schemas
=========================
Pydantic models for granular event-level data capture
(stimulus onset, response, timeout, hint, retry, level change, pause).

Usage:
    from mlops.event_schemas import EventBatch

    batch = EventBatch(**request_body)
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


VALID_EVENT_TYPES = {
    "stimulus",       # Stimulus appeared on screen
    "response",       # Player responded (tap, click, drag)
    "timeout",        # Player failed to respond in time
    "hint",           # Hint was used
    "retry",          # Player retried a trial / puzzle step
    "level_change",   # Game level increased or decreased
    "pause",          # Player paused the game
    "resume",         # Player resumed after pause
    "error",          # Incorrect response (commission / omission)
}


class BehavioralEvent(BaseModel):
    """A single discrete event within a game session."""
    event_type: str = Field(
        ...,
        description="Type of event: stimulus, response, timeout, hint, retry, level_change, pause, resume, error",
    )
    event_timestamp: float = Field(
        ..., ge=0,
        description="Milliseconds since session start",
    )
    event_data: Optional[dict] = Field(
        default=None,
        description="Arbitrary JSON payload for event-specific fields (e.g. RT, target_id, level)",
    )


class EventBatch(BaseModel):
    """Batch of events for a single session, sent from the frontend."""
    game: str
    participant_id: str
    session_id: str
    events: list[BehavioralEvent] = Field(
        ..., min_length=1,
        description="At least one event required",
    )

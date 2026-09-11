"""
Game Registry — Single Source of Truth
========================================
All game metadata is defined exactly once here.
Every other module (config, web_service, reporter, etc.)
imports from this file rather than redeclaring its own copy.

Structure
---------
GAMES           : dict[str, GameInfo]  — full metadata per game
GAME_NAMES      : list[str]            — ordered list of game IDs
GAME_DISPLAY_NAMES : dict[str, str]   — id → display name
GAME_DOMAINS    : dict[str, str]       — id → cognitive domain
"""

from .web_schemas import GameInfo

# ── Canonical registry ────────────────────────────────────────────────────────

GAMES: dict[str, GameInfo] = {
    "gonogo": GameInfo(
        id="gonogo",
        name="Go / No-Go",
        icon="🎯",
        cognitive_domain="Inhibitory Control",
        description=(
            "Press when you see the target, hold back when you see the distractor. "
            "Tests impulse control and sustained attention."
        ),
        difficulty_system="Adaptive (DifficultyProfile)",
        color="#6C5CE7",
    ),
    "memory": GameInfo(
        id="memory",
        name="Memory Match",
        icon="🃏",
        cognitive_domain="Working Memory",
        description=(
            "Find matching pairs of cards. "
            "Tests short-term memory capacity and visual recognition."
        ),
        difficulty_system="Adaptive (DifficultyProfile)",
        color="#00B894",
    ),
    "tracking": GameInfo(
        id="tracking",
        name="Visual Tracking",
        icon="👁️",
        cognitive_domain="Visual Attention",
        description=(
            "Follow the moving target with your eyes. "
            "Tests sustained visual attention and tracking accuracy."
        ),
        difficulty_system="Adaptive (DifficultyProfile)",
        color="#FDCB6E",
    ),
    "shapes": GameInfo(
        id="shapes",
        name="Shape Builder",
        icon="🔷",
        cognitive_domain="Spatial Reasoning",
        description=(
            "Arrange geometric shapes to match the target pattern. "
            "Tests spatial awareness and mental rotation."
        ),
        difficulty_system="Internal level cycling (1→9)",
        color="#E17055",
    ),
    "puzzle": GameInfo(
        id="puzzle",
        name="Puzzle Quest",
        icon="🧩",
        cognitive_domain="Planning & Cognitive Flexibility",
        description=(
            "Solve multi-step puzzles under time pressure. "
            "Tests executive function and cognitive flexibility."
        ),
        difficulty_system="Mastery streak system",
        color="#0984E3",
    ),
}

# ── Derived constants — keep these in sync with GAMES automatically ───────────

GAME_NAMES: list[str] = list(GAMES.keys())
GAME_DISPLAY_NAMES: dict[str, str] = {k: v.name for k, v in GAMES.items()}
GAME_DOMAINS: dict[str, str] = {k: v.cognitive_domain for k, v in GAMES.items()}

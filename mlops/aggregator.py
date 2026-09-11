"""
MLOps Data Aggregator (PostgreSQL Edition)
==========================================
Exposes untrained-session counts from the centralised PostgreSQL database.
Training state is written by successful training tasks, not this module.

Replaces the legacy CSV-based aggregation workflow entirely.
"""

from .db import (
    count_untrained,
    count_all_untrained,
    count_game_sessions,
    list_game_modules,
)
from .config import GAME_NAMES


def _registered_game_ids() -> list[str]:
    """Return active registered game IDs, falling back to the built-ins."""
    try:
        games = [g["game_id"] for g in list_game_modules(active_only=True)]
        return games or list(GAME_NAMES)
    except Exception:
        return list(GAME_NAMES)


def count_incoming(game_name: str) -> int:
    """Count untrained sessions for a game."""
    return count_untrained(game_name)


def count_all_incoming() -> dict:
    """Return {game_name: n_untrained_sessions} for all games."""
    pending = count_all_untrained()
    return {g: pending.get(g, 0) for g in _registered_game_ids()}


def aggregate_game(game_name: str, archive: bool = True, run_id: str = None) -> dict:
    """
    Legacy compatibility endpoint; does not mark sessions as trained.

    Training completion is recorded only after classification succeeds.

    Args:
        game_name: Name of the game
        archive:   Ignored (kept for API compatibility)
        run_id:    Optional MLflow run ID to associate with this training batch

    Returns:
        dict with keys: game, n_new, n_total, n_duplicates_removed, output_path
    """
    n_new = count_untrained(game_name)

    n_total = count_game_sessions(game_name)

    return {
        "game": game_name,
        "n_new": n_new,
        "n_total": n_total,
        "n_duplicates_removed": 0,
        "output_path": "postgresql",
        "legacy_noop": True,
    }


def aggregate_all_games(archive: bool = True) -> list:
    """Return untrained counts for all games without changing training state."""
    results = []
    for game in _registered_game_ids():
        result = aggregate_game(game, archive=archive)
        results.append(result)
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Aggregate incoming session data")
    parser.add_argument("--game", default="all")
    args = parser.parse_args()

    if args.game == "all":
        results = aggregate_all_games()
    else:
        results = [aggregate_game(args.game)]

    for r in results:
        print(f"  {r['game']:12s} | new={r['n_new']:4d} | total={r['n_total']:5d}")

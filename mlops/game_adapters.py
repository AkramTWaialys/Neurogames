"""
Game module adapters for ADHD-compatible NeuroGames ingestion.

The built-in five games have strict Pydantic schemas. A future compatible game
uses the dynamic adapter: it must provide the shared ADHD telemetry contract
and the feature names declared in the game module registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .schemas import DynamicGameSession, GAME_SCHEMAS


class GameAdapterError(ValueError):
    """Raised when a game payload cannot be validated for ingestion."""


@dataclass(frozen=True)
class GameAdapter:
    game_id: str
    display_name: str
    feature_set: list[str]

    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class BuiltInGameAdapter(GameAdapter):
    schema_cls: type

    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.schema_cls(**payload).model_dump()
        except ValidationError as exc:
            raise GameAdapterError(str(exc)) from exc


@dataclass(frozen=True)
class DynamicGameAdapter(GameAdapter):
    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            validated = DynamicGameSession(**payload).model_dump()
        except ValidationError as exc:
            raise GameAdapterError(str(exc)) from exc

        missing_features = [
            feature
            for feature in self.feature_set
            if feature not in validated or validated.get(feature) is None
        ]
        if missing_features:
            raise GameAdapterError(
                "Missing declared game feature(s): " + ", ".join(missing_features)
            )
        return validated


class GameFactory:
    @staticmethod
    def get_adapter(game_id: str) -> GameAdapter:
        from .db import get_game_module

        module = get_game_module(game_id)
        if not module:
            raise GameAdapterError(f"Unknown game module: {game_id}")
        if module.get("status") not in {"active", "draft"}:
            raise GameAdapterError(f"Game module '{game_id}' is not active")

        feature_set = module.get("feature_set") or []
        display_name = module.get("display_name") or game_id
        schema_cls = GAME_SCHEMAS.get(game_id)
        if schema_cls is not None:
            return BuiltInGameAdapter(
                game_id=game_id,
                display_name=display_name,
                feature_set=feature_set,
                schema_cls=schema_cls,
            )
        return DynamicGameAdapter(
            game_id=game_id,
            display_name=display_name,
            feature_set=feature_set,
        )


def validate_game_payload(game_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a payload for a registered ADHD game module."""
    return GameFactory.get_adapter(game_id).validate(payload)

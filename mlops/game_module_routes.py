"""
ADHD game-module registry routes.

These routes register compatible ADHD game modules. They do not upload or host
arbitrary backend code; optional ZIP packages are reviewed and published as
static browser games that send telemetry like the existing five modules.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from .auth_routes import get_current_auth_session
from .db import (
    count_game_sessions,
    create_game_module_request,
    create_game_ingestion_key,
    decide_game_module_request,
    get_game_module,
    list_game_module_requests,
    list_game_ingestion_keys,
    list_game_modules,
    register_game_module,
    revoke_game_ingestion_keys,
    update_game_module,
)
from .game_module_scanner import scan_game_module_request
from .schemas import CORE_TELEMETRY_FIELDS, DynamicGameSession, GAME_SCHEMAS

router = APIRouter(prefix="/api/game-modules", tags=["game-modules"])

ADHD_LABELS = [
    "Combined ADHD",
    "Hyperactive-Impulsive ADHD",
    "Inattentive ADHD",
    "Optimal / Neurotypical",
]

GAME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class GameModuleRegistrationRequest(BaseModel):
    game_id: str = Field(..., description="Stable slug, e.g. auditory_attention")
    display_name: str = Field(..., min_length=2, max_length=120)
    cognitive_domain: str = Field(..., min_length=2, max_length=120)
    description: str | None = None
    integration_mode: Literal["manual", "external_telemetry"] = "manual"
    feature_set: list[str] = Field(default_factory=list)
    schema_version: str = "1.0"
    ml_enabled: bool = False
    included_in_cross_game: bool = False
    status: Literal["draft", "active", "paused", "archived"] = "draft"

    @field_validator("game_id")
    @classmethod
    def validate_game_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not GAME_ID_RE.match(normalized):
            raise ValueError("game_id must use lowercase letters, numbers, _ or -")
        return normalized

    @field_validator("feature_set")
    @classmethod
    def validate_features(cls, values: list[str]) -> list[str]:
        seen: list[str] = []
        for value in values:
            feature = str(value).strip()
            if not feature:
                continue
            if feature in CORE_TELEMETRY_FIELDS:
                raise ValueError(f"{feature} is already part of the shared ADHD contract")
            if feature not in seen:
                seen.append(feature)
        return seen


class GameModuleUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=120)
    cognitive_domain: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = None
    integration_mode: Literal["manual", "external_telemetry"] | None = None
    feature_set: list[str] | None = None
    schema_version: str | None = None
    ml_enabled: bool | None = None
    included_in_cross_game: bool | None = None
    status: Literal["draft", "active", "paused", "archived"] | None = None

    @field_validator("feature_set")
    @classmethod
    def validate_features(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        seen: list[str] = []
        for value in values:
            feature = str(value).strip()
            if not feature:
                continue
            if feature in CORE_TELEMETRY_FIELDS:
                raise ValueError(f"{feature} is already part of the shared ADHD contract")
            if feature not in seen:
                seen.append(feature)
        return seen


class GameModuleDeveloperRequest(BaseModel):
    game_id: str = Field(..., description="Stable slug, e.g. auditory_attention")
    display_name: str = Field(..., min_length=2, max_length=120)
    cognitive_domain: str = Field(..., min_length=2, max_length=120)
    description: str | None = None
    integration_mode: Literal["manual", "external_telemetry"] = "manual"
    feature_set: list[str] = Field(default_factory=list)
    code_repository_url: str | None = Field(default=None, max_length=500)
    code_summary: str | None = None
    code_artifact_filename: str | None = Field(default=None, max_length=255)
    code_artifact_base64: str | None = None
    telemetry_schema_json: Any = None
    sample_payload_json: Any = None

    @field_validator("game_id")
    @classmethod
    def validate_game_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not GAME_ID_RE.match(normalized):
            raise ValueError("game_id must use lowercase letters, numbers, _ or -")
        return normalized

    @field_validator("feature_set")
    @classmethod
    def validate_features(cls, values: list[str]) -> list[str]:
        seen: list[str] = []
        for value in values:
            feature = str(value).strip()
            if not feature:
                continue
            if feature in CORE_TELEMETRY_FIELDS:
                raise ValueError(f"{feature} is already part of the shared ADHD contract")
            if feature not in seen:
                seen.append(feature)
        return seen


class GameModuleDecisionRequest(BaseModel):
    decision: Literal["accepted", "rejected"]
    review_notes: str | None = None
    accepted_status: Literal["draft", "active"] = "active"


def _require_roles(auth_session: dict, roles: set[str]) -> dict:
    if auth_session.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    return auth_session


def _require_super_admin(auth_session: dict) -> dict:
    return _require_roles(auth_session, {"super_admin"})


def _require_developer(auth_session: dict) -> dict:
    return _require_roles(auth_session, {"developer"})


def _sample_payload(module: dict | None = None) -> dict:
    feature_set = (module or {}).get("feature_set") or [
        "target_speed",
        "move_efficiency",
        "planning_score",
    ]
    payload = {
        "Participant_ID": "existing_neurogames_player",
        "Game_Session_ID": f"{(module or {}).get('game_id', 'new_game')}_session_001",
        "Age_Group": "9-11",
        "Cognitive_Level": "Medium",
        "Game_Completion_Status": "Completed",
        "Performance_Level": "Medium",
        "Time_Spent": 92.4,
        "Total_Actions": 48,
        "Correct_Responses": 31,
        "Incorrect_Responses": 17,
        "Hint_Usage": 2,
        "Touch_Interactions": 48,
        "Reaction_Time": 0.83,
        "pause_count": 1,
        "retry_count": 0,
        "rt_cv": 0.18,
        "level": 3,
        "max_level_reached": 4,
        "rounds_played": 6,
        "rounds_passed": 4,
        "session_outcome": "completed",
        "schema_version": (module or {}).get("schema_version", "1.0"),
    }
    payload.update({feature: 0 for feature in feature_set})
    return payload


def _module_status(module: dict) -> dict:
    missing = []
    if not module.get("feature_set"):
        missing.append("feature_set")
    if module.get("included_in_cross_game") and not module.get("ml_enabled"):
        missing.append("ml_enabled_required_for_cross_game")
    if module.get("is_builtin"):
        decision = "Built-in ADHD reference module"
    elif module.get("ml_enabled") and module.get("included_in_cross_game"):
        decision = "Accepted: ML-ready and cross-game-enabled"
    elif module.get("ml_enabled"):
        decision = "Accepted: ML-ready per-game module"
    else:
        decision = "Accepted: telemetry/analytics only"
    return {
        "decision": decision,
        "missing": missing,
        "ml_enabled": bool(module.get("ml_enabled")),
        "included_in_cross_game": bool(module.get("included_in_cross_game")),
        "status": module.get("status"),
    }


@router.get("/adhd-contract")
def get_adhd_contract():
    """Return the shared ADHD telemetry contract for compatible games."""
    return {
        "target_domain": "ADHD",
        "label_schema": {
            "type": "adhd_profile_categories",
            "labels": ADHD_LABELS,
        },
        "core_fields": CORE_TELEMETRY_FIELDS,
        "dynamic_schema": DynamicGameSession.model_json_schema(),
        "rules": [
            "A compatible game is registered as a NeuroGames ADHD game module.",
            "Optional gameplay code is submitted as a reviewed static ZIP package, not as backend code.",
            "Accepted packages must contain index.html and run in a sandboxed browser iframe.",
            "The module must send the shared ADHD telemetry fields.",
            "Declared game-specific features are preserved in sessions.data_json.",
            "A new module is excluded from ML and cross-game consensus until explicitly enabled.",
        ],
        "sample_payload": _sample_payload(),
    }


@router.get("")
def list_modules(auth_session: dict = Depends(get_current_auth_session)):
    _ = auth_session
    modules = []
    for module in list_game_modules(active_only=False):
        item = dict(module)
        item["session_count"] = count_game_sessions(item["game_id"])
        item["ml_eligible_sessions"] = count_game_sessions(item["game_id"], ml_eligible_only=True)
        item["ingestion_keys"] = list_game_ingestion_keys(item["game_id"])
        item["integration_status"] = _module_status(item)
        modules.append(item)
    return {"game_modules": modules, "count": len(modules)}


@router.post("")
def create_module(
    body: GameModuleRegistrationRequest,
    auth_session: dict = Depends(get_current_auth_session),
):
    _require_super_admin(auth_session)
    try:
        module = register_game_module(
            game_id=body.game_id,
            display_name=body.display_name,
            cognitive_domain=body.cognitive_domain,
            description=body.description,
            integration_mode=body.integration_mode,
            feature_set=body.feature_set,
            schema_version=body.schema_version,
            ml_enabled=body.ml_enabled,
            included_in_cross_game=body.included_in_cross_game,
            status=body.status,
        )
        key = create_game_ingestion_key(
            body.game_id,
            created_by=auth_session.get("user_id"),
            label="default",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "game_module": module,
        "ingestion_key": key,
        "integration_status": _module_status(module),
        "schema_url": f"/api/game-modules/{body.game_id}/schema",
    }


@router.patch("/{game_id}")
def patch_module(
    game_id: str,
    body: GameModuleUpdateRequest,
    auth_session: dict = Depends(get_current_auth_session),
):
    _require_super_admin(auth_session)
    try:
        module = update_game_module(game_id=game_id, **body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    module["session_count"] = count_game_sessions(game_id)
    module["ml_eligible_sessions"] = count_game_sessions(game_id, ml_eligible_only=True)
    module["ingestion_keys"] = list_game_ingestion_keys(game_id)
    module["integration_status"] = _module_status(module)
    return {"status": "ok", "game_module": module, "integration_status": _module_status(module)}


@router.get("/requests")
def list_requests(
    status: str | None = None,
    auth_session: dict = Depends(get_current_auth_session),
):
    role = auth_session.get("role")
    if role == "super_admin":
        return {"requests": list_game_module_requests(status=status)}
    if role == "developer":
        return {
            "requests": list_game_module_requests(
                status=status,
                developer_user_id=auth_session.get("user_id"),
            )
        }
    raise HTTPException(status_code=403, detail="Insufficient privileges")


@router.post("/requests")
def submit_request(
    body: GameModuleDeveloperRequest,
    auth_session: dict = Depends(get_current_auth_session),
):
    _require_developer(auth_session)
    existing = get_game_module(body.game_id)
    if existing and existing.get("status") != "archived":
        raise HTTPException(status_code=409, detail="A game module with this game_id already exists")
    try:
        request = create_game_module_request(
            developer_user_id=auth_session["user_id"],
            game_id=body.game_id,
            display_name=body.display_name,
            cognitive_domain=body.cognitive_domain,
            description=body.description,
            integration_mode=body.integration_mode,
            feature_set=body.feature_set,
            code_repository_url=body.code_repository_url,
            code_summary=body.code_summary,
            code_artifact_filename=body.code_artifact_filename,
            code_artifact_base64=body.code_artifact_base64,
            telemetry_schema_json=body.telemetry_schema_json,
            sample_payload_json=body.sample_payload_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "request": request}


@router.post("/requests/{request_id}/scan")
def scan_request(
    request_id: int,
    auth_session: dict = Depends(get_current_auth_session),
):
    _require_super_admin(auth_session)
    try:
        report = scan_game_module_request(request_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "scan": report}


@router.patch("/requests/{request_id}/decision")
def decide_request(
    request_id: int,
    body: GameModuleDecisionRequest,
    auth_session: dict = Depends(get_current_auth_session),
):
    _require_super_admin(auth_session)
    try:
        if body.decision == "accepted":
            scan = scan_game_module_request(request_id)
            if scan.get("overall_status") == "FAIL":
                raise ValueError(
                    "Scan failed. Fix the package, feature set, or telemetry sample before accepting this game module."
                )
        request = decide_game_module_request(
            request_id=request_id,
            reviewer_user_id=auth_session["user_id"],
            decision=body.decision,
            review_notes=body.review_notes,
            accepted_status=body.accepted_status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "request": request}


@router.get("/{game_id}")
def get_module(game_id: str, auth_session: dict = Depends(get_current_auth_session)):
    _ = auth_session
    module = get_game_module(game_id)
    if not module:
        raise HTTPException(status_code=404, detail=f"Unknown game module: {game_id}")
    module["session_count"] = count_game_sessions(game_id)
    module["ml_eligible_sessions"] = count_game_sessions(game_id, ml_eligible_only=True)
    module["ingestion_keys"] = list_game_ingestion_keys(game_id)
    module["integration_status"] = _module_status(module)
    return module


@router.get("/{game_id}/schema")
def get_module_schema(game_id: str, auth_session: dict = Depends(get_current_auth_session)):
    _ = auth_session
    module = get_game_module(game_id)
    if not module:
        raise HTTPException(status_code=404, detail=f"Unknown game module: {game_id}")
    schema_cls = GAME_SCHEMAS.get(game_id) or DynamicGameSession
    return {
        "game_module": module,
        "core_fields": CORE_TELEMETRY_FIELDS,
        "feature_set": module.get("feature_set") or [],
        "schema": schema_cls.model_json_schema(),
        "sample_payload": _sample_payload(module),
    }


@router.post("/{game_id}/rotate-key")
def rotate_module_key(
    game_id: str,
    auth_session: dict = Depends(get_current_auth_session),
):
    _require_super_admin(auth_session)
    module = get_game_module(game_id)
    if not module:
        raise HTTPException(status_code=404, detail=f"Unknown game module: {game_id}")
    revoked = revoke_game_ingestion_keys(game_id)
    key = create_game_ingestion_key(
        game_id,
        created_by=auth_session.get("user_id"),
        label="rotated",
    )
    return {"status": "ok", "revoked": revoked, "ingestion_key": key}

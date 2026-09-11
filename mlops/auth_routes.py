"""
Authentication API routes for NeuroGames.

Provides registration, login, session validation, and logout endpoints.
Supported roles: 'child' (game players), 'therapist' (school-scoped dashboard),
and 'super_admin' (global view + school/therapist management).
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import hash_password, verify_password, generate_token, compute_expiry
from .db import (
    create_user,
    create_user_profile,
    get_user_by_username,
    get_user_profile,
    update_last_login,
    create_auth_session,
    validate_auth_session,
    revoke_session,
    revoke_all_sessions,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Request / Response Models ─────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=30)
    password: str = Field(..., min_length=4, max_length=128)
    email: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    # The role field is removed from public registration to prevent self-service privilege escalation.
    # It will automatically be 'child' in the route.
    school_id: int | None = None
    display_name: str | None = None
    avatar: str = Field(default="🦁")
    age: int | None = Field(default=None, ge=4, le=18)
    age_group: str | None = None
    cognitive_level: str = Field(default="Medium")
    cluster: str = Field(default="Optimal / Neurotypical")
    locale_pref: str = Field(default="fr")
    custom_school_name: str | None = None
    conners_score: int | None = None
    conners_data: str | None = None


class DeveloperRegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=30)
    password: str = Field(..., min_length=4, max_length=128)
    email: str = Field(..., min_length=3, max_length=120)
    phone: str | None = Field(default=None, max_length=40)


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False


class SchoolCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)


class AdminCreateRequest(BaseModel):
    """Create a therapist account. Class name is kept for API compatibility."""

    username: str = Field(..., min_length=2, max_length=30)
    password: str = Field(..., min_length=4, max_length=128)
    school_id: int

class AdminPatchRequest(BaseModel):
    school_id: int | None = None


class ProfileResponse(BaseModel):
    user_id: int
    username: str
    role: str
    email: str | None = None
    phone: str | None = None
    display_name: str | None
    avatar: str
    age: int | None
    age_group: str | None
    cognitive_level: str
    cluster: str
    locale_pref: str
    conners_score: int | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_token(request: Request) -> str:
    """Extract Bearer token from the Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return auth[7:]


def get_current_auth_session(request: Request) -> dict:
    """Validate the bearer token and return the authenticated session."""
    token = _extract_token(request)
    session = validate_auth_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return session


def _build_profile_response(user: dict, profile: dict | None) -> dict:
    """Merge user + profile into a single response dict."""
    return {
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "email": user.get("email"),
        "phone": user.get("phone"),
        "display_name": (profile or {}).get("display_name"),
        "avatar": (profile or {}).get("avatar", "🦁"),
        "age": (profile or {}).get("age"),
        "age_group": (profile or {}).get("age_group"),
        "cognitive_level": (profile or {}).get("cognitive_level", "Medium"),
        "cluster": (profile or {}).get("cluster", "Optimal / Neurotypical"),
        "locale_pref": (profile or {}).get("locale_pref", "fr"),
        "conners_score": (profile or {}).get("conners_score"),
    }


def _age_to_group(age: int | None) -> str | None:
    """Convert a numeric age to the standard age group bracket."""
    if age is None:
        return None
    if age <= 8:
        return "6-8"
    if age <= 11:
        return "9-11"
    return "12-14"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/register")
async def register(body: RegisterRequest):
    """
    Register a new child account.
    
    Role is strictly set to 'child'.
    A valid school_id must be provided.
    """
    from .db import get_school
    
    # Check if school exists
    # Validate school if provided
    if body.school_id is not None:
        school = get_school(body.school_id)
        if not school:
            raise HTTPException(status_code=400, detail="Invalid school_id")
    elif not body.custom_school_name:
        raise HTTPException(status_code=400, detail="Either school_id or custom_school_name is required")

    # Check for duplicate username
    existing = get_user_by_username(body.username)
    if existing:
        raise HTTPException(status_code=409, detail="Username already taken")

    # Create user
    pw_hash = hash_password(body.password)
    # If custom_school_name is provided, they are part of the "Others" institution (id=1)
    effective_school_id = body.school_id
    if body.custom_school_name and effective_school_id is None:
        effective_school_id = 1
        
    user_id = create_user(
        username=body.username,
        password_hash=pw_hash,
        role="child",
        email=body.email,
        phone=body.phone,
        school_id=effective_school_id,
    )

    # Auto-compute age_group from age if not provided
    age_group = body.age_group or _age_to_group(body.age)

    # Create profile
    create_user_profile(
        user_id=user_id,
        display_name=body.display_name,
        avatar=body.avatar,
        age=body.age,
        age_group=age_group,
        cognitive_level=body.cognitive_level,
        cluster=body.cluster,
        locale_pref=body.locale_pref,
        custom_school_name=body.custom_school_name,
        conners_score=body.conners_score,
        conners_data=body.conners_data,
    )

    log.info("Registered child %s (id=%d, school=%d)", body.username, user_id, body.school_id)

    return {
        "status": "ok",
        "user_id": user_id,
        "username": body.username,
    }


@router.post("/developer-register")
async def register_developer(body: DeveloperRegisterRequest):
    """
    Register a developer account.

    Developer accounts can submit game-module integration requests, but they
    cannot directly add games to the NeuroGames registry.
    """
    existing = get_user_by_username(body.username)
    if existing:
        raise HTTPException(status_code=409, detail="Username already taken")

    pw_hash = hash_password(body.password)
    user_id = create_user(
        username=body.username,
        password_hash=pw_hash,
        role="developer",
        email=body.email,
        phone=body.phone,
    )

    log.info("Registered developer %s (id=%d)", body.username, user_id)

    return {
        "status": "ok",
        "user_id": user_id,
        "username": body.username,
    }


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    """
    Authenticate with username + password.

    Returns a session token and the user profile.
    """
    user = get_user_by_username(body.username)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Generate session
    token = generate_token()
    expires_at = compute_expiry(body.remember_me)
    create_auth_session(
        user_id=user["id"],
        token=token,
        remember=body.remember_me,
        expires_at=expires_at,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )
    update_last_login(user["id"])

    profile = get_user_profile(user["id"])
    resp = _build_profile_response(user, profile)

    log.info("Login: user=%s remember=%s", body.username, body.remember_me)

    return {
        "status": "ok",
        "token": token,
        "expires_at": expires_at,
        "profile": resp,
    }


@router.get("/me")
async def me(request: Request):
    """
    Validate the current session token and return the user profile.

    Requires Authorization: Bearer <token> header.
    """
    session = get_current_auth_session(request)

    user = get_user_by_username(session["username"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    profile = get_user_profile(user["id"])
    return _build_profile_response(user, profile)


@router.post("/logout")
async def logout(request: Request):
    """Revoke the current session token."""
    token = _extract_token(request)
    revoked = revoke_session(token)
    return {"status": "ok", "revoked": revoked}


@router.post("/logout-all")
async def logout_all(request: Request):
    """Revoke all sessions for the current user."""
    session = get_current_auth_session(request)
    count = revoke_all_sessions(session["user_id"])
    return {"status": "ok", "revoked_count": count}


# ── Super Admin & Schools Routes ──────────────────────────────────────────────

def _require_super_admin(request: Request) -> dict:
    """Validate token and require super_admin role."""
    session = get_current_auth_session(request)
    if session.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Super Admin privileges required")
    return session


def _audit(session: dict, action: str, target_type: str, target_id, detail: str, request: Request):
    """Helper to record an audit log entry."""
    from .db import insert_audit_log
    insert_audit_log(
        user_id=session.get("user_id"),
        username=session.get("username"),
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        detail=detail,
        ip_address=request.client.host if request.client else None,
    )


@router.get("/schools")
async def list_schools():
    """List all schools (public route for registration dropdown)."""
    from .db import list_schools as db_list_schools
    return {"schools": db_list_schools()}


@router.post("/schools")
async def create_school_endpoint(body: SchoolCreateRequest, request: Request):
    """Create a new school. Required: super_admin."""
    session = _require_super_admin(request)
    from .db import create_school, list_schools
    # Ensure no duplicates
    existing = [s for s in list_schools() if s["name"].lower() == body.name.lower()]
    if existing:
        raise HTTPException(status_code=409, detail="School name already exists")
    
    school_id = create_school(body.name, created_by=session["user_id"])
    _audit(session, "create_school", "school", school_id, f"Created school '{body.name}'", request)
    return {"status": "ok", "school_id": school_id, "name": body.name}


@router.post("/therapist-register")
@router.post("/admin-register")
async def register_admin(body: AdminCreateRequest, request: Request):
    """Create a new therapist assigned to a school. Required: super_admin."""
    session = _require_super_admin(request)
    
    # Check duplicate
    existing = get_user_by_username(body.username)
    if existing:
        raise HTTPException(status_code=409, detail="Username already taken")
        
    pw_hash = hash_password(body.password)
    user_id = create_user(
        username=body.username,
        password_hash=pw_hash,
        role="therapist",
        school_id=body.school_id,
    )
    
    # Automatically add the therapist to the school assignment pivot table.
    from .db import assign_admin_to_school
    assign_admin_to_school(user_id, body.school_id)

    _audit(session, "create_therapist", "user", user_id, f"Created therapist '{body.username}' for school {body.school_id}", request)
    return {"status": "ok", "user_id": user_id, "username": body.username, "school_id": body.school_id}

@router.get("/therapists")
@router.get("/admins")
async def get_admins(request: Request):
    """List all therapists and their school assignments. Required: super_admin."""
    _require_super_admin(request)
    from .db import list_admins
    therapists = list_admins()
    return {"therapists": therapists, "admins": therapists}


@router.get("/therapists/{admin_id}")
@router.get("/admins/{admin_id}")
async def get_admin_detail_endpoint(admin_id: int, request: Request):
    """Retrieve detailed info for a specific therapist. Required: super_admin."""
    _require_super_admin(request)
    from .db import get_admin_detail_full
    admin = get_admin_detail_full(admin_id)
    if not admin:
        raise HTTPException(status_code=404, detail="Therapist not found")
    return admin


@router.delete("/schools/{school_id}")
async def delete_school_endpoint(school_id: int, request: Request):
    """Delete a school. Required: super_admin. School id=1 (Others) is protected."""
    session = _require_super_admin(request)
    if school_id == 1:
        raise HTTPException(status_code=403, detail="The 'Others' institution is protected and cannot be deleted")
    from .db import delete_school
    success = delete_school(school_id)
    if not success:
        raise HTTPException(status_code=404, detail="School not found or cannot be deleted")
    _audit(session, "delete_school", "school", school_id, f"Deleted school {school_id}", request)
    return {"status": "ok", "message": f"School {school_id} deleted"}


@router.delete("/therapists/{admin_id}")
@router.delete("/admins/{admin_id}")
async def delete_admin_endpoint(admin_id: int, request: Request):
    """Delete a therapist user. Required: super_admin."""
    session = _require_super_admin(request)
    if admin_id == session["user_id"]:
        raise HTTPException(status_code=403, detail="Cannot delete your own account")
    from .db import delete_user
    success = delete_user(admin_id)
    if not success:
        raise HTTPException(status_code=404, detail="Therapist not found")
    _audit(session, "delete_therapist", "user", admin_id, f"Deleted therapist user {admin_id}", request)
    return {"status": "ok", "message": f"Therapist {admin_id} deleted"}


@router.patch("/therapists/{admin_id}")
@router.patch("/admins/{admin_id}")
async def update_admin_endpoint(admin_id: int, body: AdminPatchRequest, request: Request):
    """Update a therapist (legacy single school reassign). Required: super_admin."""
    session = _require_super_admin(request)
    from .db import update_user_school
    
    if body.school_id is not None:
        success = update_user_school(admin_id, body.school_id)
        if not success:
            raise HTTPException(status_code=404, detail="Therapist or School not found")
        _audit(session, "update_therapist", "user", admin_id, f"Reassigned therapist {admin_id} to school {body.school_id}", request)
            
    return {"status": "ok", "message": f"Therapist {admin_id} updated"}


@router.post("/therapists/{admin_id}/schools/{school_id}")
@router.post("/admins/{admin_id}/schools/{school_id}")
async def assign_admin_school_endpoint(admin_id: int, school_id: int, request: Request):
    """Assign a therapist to a school. Required: super_admin."""
    session = _require_super_admin(request)
    from .db import assign_admin_to_school, get_school
    if not get_school(school_id):
        raise HTTPException(status_code=404, detail="School not found")
    assign_admin_to_school(admin_id, school_id)
    _audit(session, "assign_therapist_school", "user", admin_id, f"Assigned therapist {admin_id} to school {school_id}", request)
    return {"status": "ok", "message": f"Therapist {admin_id} assigned to school {school_id}"}


@router.delete("/therapists/{admin_id}/schools/{school_id}")
@router.delete("/admins/{admin_id}/schools/{school_id}")
async def unassign_admin_school_endpoint(admin_id: int, school_id: int, request: Request):
    """Remove a school assignment from a therapist. Required: super_admin."""
    session = _require_super_admin(request)
    from .db import unassign_admin_from_school
    success = unassign_admin_from_school(admin_id, school_id)
    if not success:
        raise HTTPException(status_code=404, detail="Assignment not found")
    _audit(session, "unassign_therapist_school", "user", admin_id, f"Removed therapist {admin_id} from school {school_id}", request)
    return {"status": "ok", "message": f"Therapist {admin_id} removed from school {school_id}"}


@router.get("/players")
async def get_players_endpoint(school_id: int | None = None, request: Request = None):
    """
    List players (role='child').
    Required: therapist (scoped to their school(s)).
    Super Admin is restricted from viewing player data.
    """
    session = get_current_auth_session(request)
    role = session.get("role")
    admin_id = session.get("user_id")
    
    from .db import list_players_full, get_admin_school_ids, list_schools
    
    if role == "super_admin":
        # Super Admin can see everyone (or optionally filter by school_id)
        if school_id:
            return {"players": list_players_full(school_id)}
        else:
            all_players = []
            for s in list_schools():
                all_players.extend(list_players_full(s["id"]))
            return {"players": all_players}
            
    if role not in ("therapist", "admin"):
        raise HTTPException(status_code=403, detail="Access denied")

    # Therapist: must be assigned to the requested school_id.
    assigned_school_ids = get_admin_school_ids(admin_id)
    if not assigned_school_ids:
        # Fallback for legacy
        legacy_sid = session.get("school_id")
        if legacy_sid:
            assigned_school_ids = [legacy_sid]

    if school_id:
        if school_id not in assigned_school_ids:
            raise HTTPException(status_code=403, detail="Access denied to this school")
        return {"players": list_players_full(school_id)}
    
    # No school_id requested: return all assigned schools
    all_players = []
    for sid in assigned_school_ids:
        all_players.extend(list_players_full(sid))
    return {"players": all_players}


@router.get("/audit-log")
async def get_audit_log_endpoint(request: Request, limit: int = 50):
    """View recent admin audit log entries. Required: super_admin."""
    _require_super_admin(request)
    from .db import get_audit_logs
    return {"entries": get_audit_logs(limit=min(limit, 200))}

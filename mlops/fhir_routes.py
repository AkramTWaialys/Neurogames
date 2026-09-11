"""Secured FHIR export routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth_routes import get_current_auth_session
from .db import get_admin_school_ids
from .fhir_export import build_fhir_bundle, get_participant_fhir_source


router = APIRouter(prefix="/api/fhir", tags=["fhir"])


def _can_access_participant(session: dict, participant: dict) -> bool:
    role = session.get("role")
    if role == "super_admin":
        return True
    if role not in ("therapist", "admin"):
        return False

    participant_school_id = participant.get("school_id")
    if participant_school_id is None:
        return False

    assigned_school_ids = get_admin_school_ids(session.get("user_id"))
    if not assigned_school_ids and session.get("school_id") is not None:
        assigned_school_ids = [session["school_id"]]
    return participant_school_id in assigned_school_ids


@router.get("/participants/{pid}/bundle")
def export_participant_bundle(pid: str, request: Request):
    """
    Export one participant's latest NeuroGames screening/report evidence as a
    FHIR R4 JSON Bundle.

    ML outputs are represented as Observation resources only. They are not
    exported as Condition or any diagnostic assertion.
    """
    session = get_current_auth_session(request)
    if session.get("role") not in ("therapist", "admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Therapist privileges required")

    source = get_participant_fhir_source(pid)
    if source is None:
        raise HTTPException(status_code=404, detail="Participant not found")
    if not _can_access_participant(session, source["participant"]):
        raise HTTPException(status_code=403, detail="Access denied to this participant")

    try:
        bundle = build_fhir_bundle(source)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filename = f"neurogames-fhir-{source['participant']['username']}.json"
    return JSONResponse(
        content=bundle,
        media_type="application/fhir+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

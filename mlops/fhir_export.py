"""
FHIR R4 export helpers for NeuroGames.

The exporter is intentionally read-only: it maps existing NeuroGames users,
schools, sessions, Conners screening data, ML outputs, and generated reports
to FHIR-shaped JSON resources without changing the application schema.
"""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any


FHIR_VERSION = "4.0.1"
SYSTEM_BASE = "https://neurogames.local/fhir"
LOINC_SYSTEM = "http://loinc.org"
SNOMED_SYSTEM = "http://snomed.info/sct"


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _to_fhir_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    return text or None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resource_id(prefix: str, raw: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9\-.]", "-", str(raw)).strip("-")
    text = text[:48] or uuid.uuid4().hex[:12]
    return f"{prefix}-{text}"


def _full_url(resource: dict) -> str:
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(resource.get('id'), sort_keys=True))}"


def _local_code(code: str, display: str) -> dict:
    return {"coding": [{"system": f"{SYSTEM_BASE}/CodeSystem/neurogames", "code": code, "display": display}], "text": display}


def _reference(resource: dict, display: str | None = None) -> dict:
    ref = {"reference": f"{resource['resourceType']}/{resource['id']}"}
    if display:
        ref["display"] = display
    return ref


def _safe_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _game_display_name(game: str) -> str:
    try:
        from .config import GAME_DISPLAY_NAMES

        return GAME_DISPLAY_NAMES.get(game, game)
    except Exception:
        return game


def _game_domain(game: str) -> str:
    try:
        from .config import GAME_DOMAINS

        return GAME_DOMAINS.get(game, "Cognitive gameplay task")
    except Exception:
        return "Cognitive gameplay task"


def get_participant_fhir_source(pid: str) -> dict | None:
    """Collect all source records needed for a participant FHIR export."""
    from .db import get_db, get_latest_report_run

    with get_db() as conn:
        participant = conn.execute(
            """
            SELECT
                u.id, u.username, u.role, u.school_id, u.created_at,
                up.display_name, up.age, up.age_group, up.cognitive_level,
                up.cluster, up.locale_pref, up.custom_school_name,
                up.conners_score, up.conners_data,
                s.name AS school_name
            FROM users u
            LEFT JOIN user_profiles up ON up.user_id = u.id
            LEFT JOIN schools s ON s.id = u.school_id
            WHERE LOWER(u.username) = LOWER(%s)
            LIMIT 1
            """,
            (pid,),
        ).fetchone()
        if not participant or participant.get("role") != "child":
            return None

        uid = participant["id"]
        sessions = conn.execute(
            """
            SELECT id, game, participant_id, session_id, data_json, ingested_at
            FROM sessions
            WHERE user_id = %s OR LOWER(participant_id) = LOWER(%s)
            ORDER BY ingested_at ASC
            """,
            (uid, pid),
        ).fetchall()
        classifications = conn.execute(
            """
            SELECT game, actual_cluster, predicted_cluster, confidence,
                   pipeline_run_id, classified_at
            FROM classification_results
            WHERE user_id = %s
            ORDER BY classified_at DESC
            """,
            (uid,),
        ).fetchall()
        cross_game = conn.execute(
            """
            SELECT cluster, majority_pred, agreement_ratio, all_agree,
                   predictions_json, pipeline_run_id, computed_at
            FROM cross_game_results
            WHERE user_id = %s
            LIMIT 1
            """,
            (uid,),
        ).fetchone()
        conners = conn.execute(
            """
            SELECT conners_score, conners_tscore, conners_tier, ml_prediction,
                   agreement_ratio, concordance, concordance_detail,
                   original_confidence, adjusted_confidence, correction_flag,
                   computed_at
            FROM conners_concordance
            WHERE user_id = %s
            LIMIT 1
            """,
            (uid,),
        ).fetchone()

    latest_run = get_latest_report_run(pid)
    latest_report = _load_latest_report(pid, latest_run)

    return {
        "participant": dict(participant),
        "sessions": [dict(row) for row in sessions],
        "classifications": [dict(row) for row in classifications],
        "cross_game": dict(cross_game) if cross_game else None,
        "conners": dict(conners) if conners else None,
        "latest_report_run": latest_run,
        "latest_report": latest_report,
    }


def _load_latest_report(pid: str, latest_run: dict | None) -> dict | None:
    """Load latest report text/metadata from report_runs or the report cache."""
    report_path = (latest_run or {}).get("report_path")
    report = _read_report_path(report_path) if report_path else None
    if report:
        report.update(
            {
                "generated_at": _to_fhir_datetime((latest_run or {}).get("generated_at")) or report.get("generated_at"),
                "snapshot_until": _to_fhir_datetime((latest_run or {}).get("snapshot_until")) or report.get("snapshot_until"),
                "report_group_id": (latest_run or {}).get("report_group_id") or report.get("report_group_id"),
                "locale": (latest_run or {}).get("locale") or report.get("locale"),
                "session_count_at_snapshot": (latest_run or {}).get("session_count_at_snapshot")
                or report.get("session_count_at_snapshot"),
            }
        )
        return report

    try:
        from .reporter import find_latest_report

        found = find_latest_report(pid)
    except Exception:
        found = None
    if not found:
        return None

    return {
        "path": found.get("path"),
        "filename": found.get("filename"),
        "report_text": found.get("report_text"),
        "structured": found.get("structured"),
        "method": found.get("method"),
        "generated_at": found.get("generated_at"),
        "snapshot_until": found.get("snapshot_until"),
        "report_group_id": found.get("report_group_id"),
        "locale": found.get("locale"),
        "session_count_at_snapshot": found.get("session_count_at_snapshot"),
    }


def _read_report_path(report_path: str | None) -> dict | None:
    if not report_path or not os.path.exists(report_path):
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None

    sidecar = {}
    sidecar_path = os.path.splitext(report_path)[0] + ".json"
    if os.path.exists(sidecar_path):
        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                sidecar = json.load(f)
        except (OSError, json.JSONDecodeError):
            sidecar = {}

    metadata = sidecar.get("metadata", {}) if isinstance(sidecar.get("metadata"), dict) else {}
    return {
        "path": report_path,
        "filename": os.path.basename(report_path),
        "report_text": text,
        "structured": sidecar.get("structured"),
        "method": sidecar.get("method"),
        "generated_at": metadata.get("generated_at"),
        "snapshot_until": metadata.get("snapshot_until"),
        "report_group_id": metadata.get("report_group_id"),
        "locale": metadata.get("locale"),
        "session_count_at_snapshot": metadata.get("session_count_at_snapshot"),
    }


def build_fhir_bundle(source: dict) -> dict:
    """Build a FHIR R4 Bundle from collected NeuroGames source records."""
    participant = source["participant"]
    patient = _build_patient(participant)
    organization = _build_organization(participant)
    observations: list[dict] = []
    questionnaire = _build_conners_questionnaire_response(participant, patient)

    conners_observation = _build_conners_observation(participant, source.get("conners"), patient)
    if conners_observation:
        observations.append(conners_observation)

    for observation in _build_session_observations(source.get("sessions", []), patient):
        observations.append(observation)

    for row in source.get("classifications", []):
        observations.append(_build_classification_observation(row, patient))

    cross_game_observation = _build_cross_game_observation(source.get("cross_game"), patient)
    if cross_game_observation:
        observations.append(cross_game_observation)

    report = _build_diagnostic_report(
        participant=participant,
        patient=patient,
        organization=organization,
        observations=observations,
        latest_report=source.get("latest_report"),
        latest_run=source.get("latest_report_run"),
    )
    document = _build_document_reference(source.get("latest_report"), patient, organization)

    resources = [organization, patient]
    if questionnaire:
        resources.append(questionnaire)
    resources.extend(observations)
    resources.append(report)
    if document:
        report.setdefault("presentedForm", []).append(document["content"][0]["attachment"])
        resources.append(document)

    bundle = {
        "resourceType": "Bundle",
        "id": _resource_id("bundle", f"{participant['username']}-{datetime.now(timezone.utc).timestamp()}"),
        "meta": {"profile": ["http://hl7.org/fhir/StructureDefinition/Bundle"]},
        "type": "collection",
        "timestamp": _now(),
        "identifier": {
            "system": f"{SYSTEM_BASE}/NamingSystem/fhir-export",
            "value": f"neurogames-fhir-export-{participant['username']}",
        },
        "entry": [{"fullUrl": _full_url(resource), "resource": resource} for resource in resources],
    }
    validate_fhir_bundle(bundle)
    return bundle


def _build_organization(participant: dict) -> dict:
    school_id = participant.get("school_id") or "unknown"
    name = participant.get("custom_school_name") or participant.get("school_name") or "Unknown institution"
    organization = {
        "resourceType": "Organization",
        "id": _resource_id("organization", school_id),
        "identifier": [{"system": f"{SYSTEM_BASE}/NamingSystem/school-id", "value": str(school_id)}],
        "name": name,
    }
    return organization


def _build_patient(participant: dict) -> dict:
    extensions = []
    for key, label in (
        ("age", "age"),
        ("age_group", "age-group"),
        ("cognitive_level", "cognitive-level"),
        ("cluster", "internal-screening-profile"),
        ("locale_pref", "locale-preference"),
    ):
        value = participant.get(key)
        if value is None:
            continue
        ext = {"url": f"{SYSTEM_BASE}/StructureDefinition/{label}"}
        if isinstance(value, int):
            ext["valueInteger"] = value
        else:
            ext["valueString"] = str(value)
        extensions.append(ext)

    patient = {
        "resourceType": "Patient",
        "id": _resource_id("patient", participant["id"]),
        "identifier": [
            {"system": f"{SYSTEM_BASE}/NamingSystem/participant-id", "value": participant["username"]}
        ],
        "active": True,
        "managingOrganization": {"reference": f"Organization/{_resource_id('organization', participant.get('school_id') or 'unknown')}"},
    }
    if participant.get("display_name"):
        patient["name"] = [{"text": participant["display_name"]}]
    if extensions:
        patient["extension"] = extensions
    return patient


def _build_conners_questionnaire_response(participant: dict, patient: dict) -> dict | None:
    conners_data = participant.get("conners_data")
    conners_score = participant.get("conners_score")
    if conners_score is None and not conners_data:
        return None

    data = _as_dict(conners_data)
    items = []
    if conners_score is not None:
        items.append(
            {
                "linkId": "conners-total-score",
                "text": "Conners screening total score",
                "answer": [{"valueInteger": int(conners_score)}],
            }
        )
    for key, value in data.items():
        answer: dict[str, Any]
        if isinstance(value, bool):
            answer = {"valueBoolean": value}
        elif isinstance(value, int):
            answer = {"valueInteger": value}
        elif isinstance(value, float):
            answer = {"valueDecimal": value}
        else:
            answer = {"valueString": str(value)}
        items.append({"linkId": str(key), "text": str(key).replace("_", " "), "answer": [answer]})

    return {
        "resourceType": "QuestionnaireResponse",
        "id": _resource_id("questionnaire-response-conners", participant["id"]),
        "questionnaire": f"{SYSTEM_BASE}/Questionnaire/conners-screening",
        "status": "completed",
        "subject": _reference(patient, participant["username"]),
        "authored": _to_fhir_datetime(participant.get("created_at")) or _now(),
        "item": items,
    }


def _build_conners_observation(participant: dict, concordance: dict | None, patient: dict) -> dict | None:
    score = participant.get("conners_score")
    if score is None and not concordance:
        return None

    observation = {
        "resourceType": "Observation",
        "id": _resource_id("observation-conners", participant["id"]),
        "status": "final",
        "category": [_local_code("screening", "Screening assessment")],
        "code": _local_code("conners-screening-score", "Conners screening score"),
        "subject": _reference(patient, participant["username"]),
        "effectiveDateTime": _to_fhir_datetime((concordance or {}).get("computed_at")) or _now(),
        "note": [{"text": "Screening evidence only. This Observation is not a diagnosis."}],
    }
    if score is not None:
        observation["valueInteger"] = int(score)
    components = []
    if concordance:
        for key, label in (
            ("conners_tscore", "Conners T-score"),
            ("conners_tier", "Conners tier"),
            ("ml_prediction", "ML screening prediction used for concordance"),
            ("agreement_ratio", "ML/conners agreement ratio"),
            ("concordance", "Concordance status"),
            ("adjusted_confidence", "Adjusted screening confidence"),
        ):
            value = concordance.get(key)
            if value is None:
                continue
            component = {"code": _local_code(key, label)}
            if isinstance(value, (int, float)):
                component["valueQuantity"] = {"value": float(value)}
            else:
                component["valueString"] = str(value)
            components.append(component)
    if components:
        observation["component"] = components
    return observation


def _build_session_observations(sessions: list[dict], patient: dict) -> list[dict]:
    by_game: dict[str, list[dict]] = {}
    for row in sessions:
        by_game.setdefault(row["game"], []).append(row)

    observations = []
    for game, rows in sorted(by_game.items()):
        parsed = [_as_dict(row.get("data_json")) for row in rows]
        actions = sum(_safe_int(item.get("Total_Actions")) or 0 for item in parsed)
        correct = sum(_safe_int(item.get("Correct_Responses")) or 0 for item in parsed)
        reaction_times = [_safe_number(item.get("Reaction_Time")) for item in parsed]
        reaction_times = [value for value in reaction_times if value is not None]
        levels = [
            _safe_int(item.get("level", item.get("max_level_reached")))
            for item in parsed
        ]
        levels = [value for value in levels if value is not None]
        durations = [_safe_number(item.get("Time_Spent")) for item in parsed]
        durations = [value for value in durations if value is not None]
        latest = rows[-1].get("ingested_at") if rows else None
        display = _game_display_name(game)

        components = [
            {"code": _local_code("session-count", "Session count"), "valueInteger": len(rows)},
            {
                "code": _local_code("average-accuracy", "Average accuracy"),
                "valueQuantity": {
                    "value": round(correct / max(actions, 1) * 100, 2),
                    "unit": "%",
                    "system": "http://unitsofmeasure.org",
                    "code": "%",
                },
            },
            {"code": _local_code("total-actions", "Total actions"), "valueInteger": actions},
            {"code": _local_code("correct-responses", "Correct responses"), "valueInteger": correct},
        ]
        if reaction_times:
            components.append(
                {
                    "code": _local_code("average-reaction-time", "Average reaction time"),
                    "valueQuantity": {
                        "value": round(sum(reaction_times) / len(reaction_times), 4),
                        "unit": "s",
                        "system": "http://unitsofmeasure.org",
                        "code": "s",
                    },
                }
            )
        if durations:
            components.append(
                {
                    "code": _local_code("total-duration", "Total play duration"),
                    "valueQuantity": {
                        "value": round(sum(durations), 2),
                        "unit": "s",
                        "system": "http://unitsofmeasure.org",
                        "code": "s",
                    },
                }
            )
        if levels:
            components.append({"code": _local_code("max-level", "Highest level reached"), "valueInteger": max(levels)})

        observations.append(
            {
                "resourceType": "Observation",
                "id": _resource_id("observation-game-summary", game),
                "status": "final",
                "category": [_local_code("game-performance", "Game performance summary")],
                "code": _local_code("game-session-summary", f"{display} session summary"),
                "subject": _reference(patient),
                "effectiveDateTime": _to_fhir_datetime(latest) or _now(),
                "method": {"text": "NeuroGames gameplay telemetry aggregation"},
                "bodySite": {"text": _game_domain(game)},
                "component": components,
            }
        )
    return observations


def _build_classification_observation(row: dict, patient: dict) -> dict:
    game = row.get("game") or "unknown"
    display = _game_display_name(game)
    components = []
    for key, label in (
        ("predicted_cluster", "ML screening classification"),
        ("confidence", "Model confidence"),
        ("actual_cluster", "Internal training label"),
        ("pipeline_run_id", "Pipeline run id"),
    ):
        value = row.get(key)
        if value is None:
            continue
        component = {"code": _local_code(key, label)}
        if key == "confidence":
            component["valueQuantity"] = {"value": float(value)}
        else:
            component["valueString"] = str(value)
        components.append(component)

    observation = {
        "resourceType": "Observation",
        "id": _resource_id("observation-ml", f"{game}-{patient['id']}"),
        "status": "final",
        "category": [_local_code("ml-screening", "ML screening evidence")],
        "code": _local_code("neurogames-ml-classification", f"{display} ML screening classification"),
        "subject": _reference(patient),
        "effectiveDateTime": _to_fhir_datetime(row.get("classified_at")) or _now(),
        "method": {"text": "NeuroGames machine-learning screening model"},
        "note": [{"text": "ML output is screening evidence only and must not be interpreted as a medical diagnosis."}],
        "component": components,
    }
    if row.get("predicted_cluster"):
        observation["valueString"] = str(row["predicted_cluster"])
    return observation


def _build_cross_game_observation(row: dict | None, patient: dict) -> dict | None:
    if not row:
        return None
    predictions = _as_dict(row.get("predictions_json"))
    components = []
    for key, label in (
        ("majority_pred", "Cross-game majority screening classification"),
        ("agreement_ratio", "Agreement ratio across games"),
        ("all_agree", "All game models agree"),
        ("cluster", "Internal training label"),
        ("pipeline_run_id", "Pipeline run id"),
    ):
        value = row.get(key)
        if value is None:
            continue
        component = {"code": _local_code(key, label)}
        if isinstance(value, bool):
            component["valueBoolean"] = value
        elif isinstance(value, (int, float)):
            component["valueQuantity"] = {"value": float(value)}
        else:
            component["valueString"] = str(value)
        components.append(component)
    for key, value in sorted(predictions.items()):
        components.append(
            {
                "code": _local_code(key, f"Per-game prediction {key}"),
                "valueString": str(value),
            }
        )
    observation = {
        "resourceType": "Observation",
        "id": _resource_id("observation-cross-game", patient["id"]),
        "status": "final",
        "category": [_local_code("ml-screening", "ML screening evidence")],
        "code": _local_code("neurogames-cross-game-classification", "Cross-game ML screening classification"),
        "subject": _reference(patient),
        "effectiveDateTime": _to_fhir_datetime(row.get("computed_at")) or _now(),
        "method": {"text": "NeuroGames cross-game model aggregation"},
        "note": [{"text": "Cross-game ML output is screening evidence only and is not a diagnosis."}],
        "component": components,
    }
    if row.get("majority_pred"):
        observation["valueString"] = str(row["majority_pred"])
    return observation


def _build_diagnostic_report(
    participant: dict,
    patient: dict,
    organization: dict,
    observations: list[dict],
    latest_report: dict | None,
    latest_run: dict | None,
) -> dict:
    generated_at = (
        _to_fhir_datetime((latest_report or {}).get("generated_at"))
        or _to_fhir_datetime((latest_run or {}).get("generated_at"))
        or _now()
    )
    snapshot_until = (
        _to_fhir_datetime((latest_report or {}).get("snapshot_until"))
        or _to_fhir_datetime((latest_run or {}).get("snapshot_until"))
        or generated_at
    )
    structured = (latest_report or {}).get("structured") or {}
    conclusion = (
        structured.get("overall", {}).get("narrative_summary")
        if isinstance(structured, dict)
        else None
    )
    if not conclusion:
        conclusion = (
            "NeuroGames screening export assembled from gameplay, Conners, and ML evidence. "
            "No diagnosis is asserted by this report."
        )

    return {
        "resourceType": "DiagnosticReport",
        "id": _resource_id("diagnostic-report", (latest_report or {}).get("report_group_id") or participant["id"]),
        "status": "final" if latest_report else "partial",
        "category": [
            {
                "coding": [
                    {"system": SNOMED_SYSTEM, "code": "386053000", "display": "Evaluation procedure"}
                ],
                "text": "Screening assessment",
            }
        ],
        "code": _local_code("neurogames-screening-report", "NeuroGames screening report"),
        "subject": _reference(patient, participant["username"]),
        "effectiveDateTime": snapshot_until,
        "issued": generated_at,
        "performer": [_reference(organization)],
        "result": [_reference(observation) for observation in observations],
        "conclusion": conclusion,
        "conclusionCode": [_local_code("screening-evidence-only", "Screening evidence only; no diagnosis asserted")],
    }


def _build_document_reference(
    latest_report: dict | None, patient: dict, organization: dict
) -> dict | None:
    if not latest_report or not latest_report.get("report_text"):
        return None
    report_text = latest_report["report_text"]
    encoded = base64.b64encode(report_text.encode("utf-8")).decode("ascii")
    generated_at = _to_fhir_datetime(latest_report.get("generated_at")) or _now()
    title = latest_report.get("filename") or "NeuroGames screening report"
    attachment = {
        "contentType": "text/markdown; charset=utf-8",
        "data": encoded,
        "title": title,
        "creation": generated_at,
    }
    if latest_report.get("locale"):
        attachment["language"] = latest_report["locale"]
    return {
        "resourceType": "DocumentReference",
        "id": _resource_id("document-reference", latest_report.get("report_group_id") or title),
        "status": "current",
        "docStatus": "final",
        "type": _local_code("neurogames-report-document", "NeuroGames generated report document"),
        "subject": _reference(patient),
        "date": generated_at,
        "author": [_reference(organization)],
        "description": "Generated NeuroGames report document. Screening evidence only; not a diagnosis.",
        "content": [{"attachment": attachment}],
    }


def validate_fhir_bundle(bundle: dict) -> None:
    """Minimal structural validation for the generated FHIR Bundle."""
    if bundle.get("resourceType") != "Bundle":
        raise ValueError("FHIR export must be a Bundle")
    if bundle.get("type") not in {"collection", "document"}:
        raise ValueError("FHIR Bundle type must be collection or document")
    entries = bundle.get("entry")
    if not isinstance(entries, list) or not entries:
        raise ValueError("FHIR Bundle must contain entries")
    resource_types = {entry.get("resource", {}).get("resourceType") for entry in entries}
    required = {"Patient", "Organization", "DiagnosticReport"}
    missing = required - resource_types
    if missing:
        raise ValueError(f"FHIR Bundle missing required resource(s): {', '.join(sorted(missing))}")
    for entry in entries:
        resource = entry.get("resource")
        if not entry.get("fullUrl") or not isinstance(resource, dict):
            raise ValueError("Each Bundle entry must include fullUrl and resource")
        if not resource.get("resourceType") or not resource.get("id"):
            raise ValueError("Each FHIR resource must include resourceType and id")

"""
Technical scanner for developer-submitted ADHD game modules.

The scanner checks integration readiness only: static package safety, declared
features, telemetry contract compatibility, and basic browser-runtime signals.
It does not validate clinical usefulness or diagnostic accuracy.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from .db import (
    MAX_PUBLISHED_GAME_BYTES,
    SAFE_GAME_ARTIFACT_EXTENSIONS,
    get_game_module_request,
)
from .schemas import CORE_TELEMETRY_FIELDS, DynamicGameSession


FEATURE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
EXTERNAL_URL_RE = re.compile(r"https?://", re.IGNORECASE)
POSTMESSAGE_RE = re.compile(r"postMessage\s*\(", re.IGNORECASE)
SESSION_MESSAGE_RE = re.compile(r"neurogames:session", re.IGNORECASE)

TEXT_SUFFIXES = {".html", ".js", ".css", ".json", ".txt", ".svg", ".webmanifest"}
FAIL_TOKENS = {
    "eval(": "Uses eval(), which is blocked for reviewed packages.",
    "new Function": "Uses dynamic function construction, which is blocked for reviewed packages.",
    "getUserMedia": "Requests camera/microphone access, which is not needed for this prototype.",
    "geolocation": "Requests location access, which is not needed for this prototype.",
}
WARNING_TOKENS = {
    "localStorage": "Uses localStorage; verify it does not store personal or clinical data.",
    "document.cookie": "Uses cookies; verify it does not store personal or clinical data.",
    "fetch(": "Performs network calls; verify the destination and necessity.",
    "XMLHttpRequest": "Performs network calls; verify the destination and necessity.",
}


def _check(category: str, name: str, status: str, message: str, details: Any = None) -> dict:
    return {
        "category": category,
        "name": name,
        "status": status,
        "message": message,
        "details": details,
    }


def _overall_status(checks: list[dict]) -> str:
    statuses = {check["status"] for check in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARNING" in statuses:
        return "WARNING"
    return "PASS"


def _recommendation(status: str) -> str:
    if status == "PASS":
        return "Acceptable for technical publishing. Keep ML and cross-game disabled until enough labeled data exists."
    if status == "WARNING":
        return "Can be accepted only after superadmin review of the warnings. This is not a clinical validation."
    return "Reject or ask the developer to fix the package/telemetry before accepting."


def _safe_zip_member(info: zipfile.ZipInfo) -> PurePosixPath | None:
    if info.is_dir():
        return None
    normalized = info.filename.replace("\\", "/")
    rel = PurePosixPath(normalized)
    if rel.is_absolute() or ".." in rel.parts or not rel.name:
        raise ValueError(f"Unsafe path in ZIP artifact: {info.filename}")
    if rel.suffix.lower() not in SAFE_GAME_ARTIFACT_EXTENSIONS:
        raise ValueError(f"Unsupported file type in ZIP artifact: {info.filename}")
    return rel


def _read_text_member(package: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    if info.file_size > 1024 * 1024:
        return ""
    with package.open(info) as source:
        return source.read().decode("utf-8", errors="ignore")


def _scan_artifact_package(request: dict) -> tuple[list[dict], dict]:
    checks: list[dict] = []
    package_summary: dict[str, Any] = {
        "filename": request.get("code_artifact_filename"),
        "size_bytes": request.get("code_artifact_size"),
        "entry_point": None,
        "files": [],
    }
    artifact_path = request.get("code_artifact_path")
    if not artifact_path:
        checks.append(_check("package", "ZIP package", "FAIL", "No ZIP package is attached to this request."))
        return checks, package_summary

    source = Path(str(artifact_path))
    if not source.exists():
        checks.append(_check("package", "ZIP package", "FAIL", "Attached ZIP package is missing from storage."))
        return checks, package_summary

    try:
        with zipfile.ZipFile(source) as package:
            members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            entry_candidates: list[PurePosixPath] = []
            total_size = 0
            text_blob = ""
            external_urls: set[str] = set()

            for info in package.infolist():
                try:
                    rel = _safe_zip_member(info)
                except ValueError as exc:
                    checks.append(_check("package", "Static file allowlist", "FAIL", str(exc)))
                    continue
                if rel is None:
                    continue
                total_size += int(info.file_size or 0)
                members.append((info, rel))
                package_summary["files"].append(rel.as_posix())
                if rel.name.lower() == "index.html":
                    entry_candidates.append(rel)

                if rel.suffix.lower() in TEXT_SUFFIXES:
                    text = _read_text_member(package, info)
                    text_blob += "\n" + text
                    if EXTERNAL_URL_RE.search(text):
                        external_urls.add(rel.as_posix())

            if not members:
                checks.append(_check("package", "Files", "FAIL", "ZIP does not contain publishable static files."))
            else:
                checks.append(_check("package", "Files", "PASS", f"{len(members)} static file(s) are publishable."))

            if total_size > MAX_PUBLISHED_GAME_BYTES:
                checks.append(_check("package", "Expanded size", "FAIL", "ZIP expands beyond the 15 MB prototype limit."))
            else:
                checks.append(_check("package", "Expanded size", "PASS", f"Expanded package size is {total_size} bytes."))

            if entry_candidates:
                entry = sorted(entry_candidates, key=lambda path: (len(path.parts), path.as_posix()))[0]
                package_summary["entry_point"] = entry.as_posix()
                checks.append(_check("package", "Entry point", "PASS", f"Found {entry.as_posix()}."))
            else:
                checks.append(_check("package", "Entry point", "FAIL", "ZIP must contain index.html."))

            if POSTMESSAGE_RE.search(text_blob) and SESSION_MESSAGE_RE.search(text_blob):
                checks.append(_check("runtime", "Telemetry bridge", "PASS", "Package appears to send neurogames:session via postMessage."))
            else:
                checks.append(_check("runtime", "Telemetry bridge", "FAIL", "Package does not appear to send neurogames:session via postMessage."))

            for token, message in FAIL_TOKENS.items():
                if token in text_blob:
                    checks.append(_check("security", token, "FAIL", message))

            for token, message in WARNING_TOKENS.items():
                if token in text_blob:
                    checks.append(_check("security", token, "WARNING", message))

            if external_urls:
                checks.append(
                    _check(
                        "security",
                        "External URLs",
                        "WARNING",
                        "Package references external URLs; review them before accepting.",
                        sorted(external_urls),
                    )
                )
            else:
                checks.append(_check("security", "External URLs", "PASS", "No external URLs detected in text assets."))
    except zipfile.BadZipFile:
        checks.append(_check("package", "ZIP package", "FAIL", "Attached artifact is not a valid ZIP file."))

    return checks, package_summary


def _schema_property_names(schema: Any) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    props = schema.get("properties")
    if isinstance(props, dict):
        return {str(key) for key in props.keys()}
    return {str(key) for key in schema.keys() if isinstance(key, str)}


def _scan_feature_contract(request: dict) -> tuple[list[dict], dict]:
    checks: list[dict] = []
    feature_set = [str(feature).strip() for feature in (request.get("feature_set") or []) if str(feature).strip()]
    feature_summary = {
        "declared_features": feature_set,
        "feature_count": len(feature_set),
        "label_schema": "ADHD 4 profile classes",
    }

    if not feature_set:
        checks.append(_check("features", "Declared feature set", "WARNING", "No game-specific features were declared; only core telemetry will be stored."))
    else:
        checks.append(_check("features", "Declared feature set", "PASS", f"{len(feature_set)} game-specific feature(s) declared."))

    duplicates = sorted({feature for feature in feature_set if feature_set.count(feature) > 1})
    if duplicates:
        checks.append(_check("features", "Duplicate features", "FAIL", "Duplicate feature names are not allowed.", duplicates))
    else:
        checks.append(_check("features", "Duplicate features", "PASS", "Feature names are unique."))

    conflicts = sorted(set(feature_set).intersection(CORE_TELEMETRY_FIELDS))
    if conflicts:
        checks.append(_check("features", "Core field conflicts", "FAIL", "Feature set must not redefine shared ADHD telemetry fields.", conflicts))
    else:
        checks.append(_check("features", "Core field conflicts", "PASS", "No declared feature overwrites the core contract."))

    invalid = [feature for feature in feature_set if not FEATURE_NAME_RE.match(feature)]
    if invalid:
        checks.append(_check("features", "Feature name format", "FAIL", "Feature names must be stable API-safe identifiers.", invalid))
    else:
        checks.append(_check("features", "Feature name format", "PASS", "Feature names are API-safe."))

    non_snake = [feature for feature in feature_set if not SNAKE_CASE_RE.match(feature)]
    if non_snake:
        checks.append(_check("features", "Feature naming style", "WARNING", "Prefer lower snake_case for analytics and ML consistency.", non_snake))
    else:
        checks.append(_check("features", "Feature naming style", "PASS", "Feature names follow lower snake_case."))

    if len(feature_set) > 40:
        checks.append(_check("features", "Feature count", "WARNING", "Large feature sets are harder to validate and model.", len(feature_set)))

    schema_fields = _schema_property_names(request.get("telemetry_schema_json"))
    if schema_fields:
        missing_from_schema = sorted(set(feature_set).difference(schema_fields))
        if missing_from_schema:
            checks.append(_check("features", "Telemetry schema coverage", "WARNING", "Some declared features are missing from telemetry_schema_json.", missing_from_schema))
        else:
            checks.append(_check("features", "Telemetry schema coverage", "PASS", "Telemetry schema includes declared features."))
    else:
        checks.append(_check("features", "Telemetry schema coverage", "WARNING", "No structured telemetry_schema_json was provided."))

    return checks, feature_summary


def _scan_sample_payload(request: dict) -> tuple[list[dict], dict]:
    checks: list[dict] = []
    feature_set = [str(feature).strip() for feature in (request.get("feature_set") or []) if str(feature).strip()]
    payload = request.get("sample_payload_json")
    payload_summary: dict[str, Any] = {
        "has_sample_payload": isinstance(payload, dict),
        "missing_core_fields": [],
        "missing_declared_features": [],
        "extra_fields": [],
    }

    if not isinstance(payload, dict):
        checks.append(_check("telemetry", "Sample payload", "FAIL", "A JSON object sample_payload_json is required for technical acceptance."))
        return checks, payload_summary

    missing_core = [field for field in CORE_TELEMETRY_FIELDS if field not in payload or payload.get(field) is None]
    payload_summary["missing_core_fields"] = missing_core
    if missing_core:
        checks.append(_check("telemetry", "Core ADHD contract", "FAIL", "Sample payload is missing mandatory shared telemetry fields.", missing_core))
    else:
        checks.append(_check("telemetry", "Core ADHD contract", "PASS", "Sample payload contains all shared ADHD telemetry fields."))

    missing_features = [feature for feature in feature_set if feature not in payload or payload.get(feature) is None]
    payload_summary["missing_declared_features"] = missing_features
    if missing_features:
        checks.append(_check("telemetry", "Declared features in sample", "FAIL", "Sample payload is missing declared game-specific features.", missing_features))
    else:
        checks.append(_check("telemetry", "Declared features in sample", "PASS", "Sample payload includes all declared game-specific features."))

    allowed_fields = set(CORE_TELEMETRY_FIELDS).union(feature_set).union({"Age", "cluster", "raw_events", "round_details"})
    extra_fields = sorted(str(field) for field in payload.keys() if field not in allowed_fields)
    payload_summary["extra_fields"] = extra_fields
    if extra_fields:
        checks.append(_check("telemetry", "Undeclared sample fields", "WARNING", "Sample payload has fields that are not in the core contract or feature_set.", extra_fields))
    else:
        checks.append(_check("telemetry", "Undeclared sample fields", "PASS", "Sample payload fields match the declared contract."))

    try:
        DynamicGameSession(**payload)
        checks.append(_check("telemetry", "Pydantic validation", "PASS", "Sample payload passes the dynamic ADHD session schema."))
    except ValidationError as exc:
        checks.append(
            _check(
                "telemetry",
                "Pydantic validation",
                "FAIL",
                "Sample payload does not pass the dynamic ADHD session schema.",
                json.loads(exc.json()),
            )
        )

    return checks, payload_summary


def scan_game_module_request(request_id: int) -> dict:
    """Return a technical scan report for one developer game-module request."""
    request = get_game_module_request(request_id)
    if not request:
        raise ValueError(f"Unknown game module request: {request_id}")

    checks: list[dict] = []
    checks.extend(
        [
            _check("metadata", "Game ID", "PASS", f"Game ID '{request.get('game_id')}' is registered in the request."),
            _check("metadata", "Target domain", "PASS", "Request is evaluated as an ADHD-compatible game module."),
            _check("metadata", "Label schema", "PASS", "Uses the shared ADHD 4-class profile label schema."),
        ]
    )

    feature_checks, feature_summary = _scan_feature_contract(request)
    payload_checks, payload_summary = _scan_sample_payload(request)
    package_checks, package_summary = _scan_artifact_package(request)
    checks.extend(feature_checks)
    checks.extend(payload_checks)
    checks.extend(package_checks)

    status = _overall_status(checks)
    return {
        "request_id": int(request_id),
        "game_id": request.get("game_id"),
        "display_name": request.get("display_name"),
        "overall_status": status,
        "recommendation": _recommendation(status),
        "checks": checks,
        "features": feature_summary,
        "telemetry": payload_summary,
        "package": package_summary,
    }

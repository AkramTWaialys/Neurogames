"""
Authentication utilities for NeuroGames.

Provides password hashing (PBKDF2-HMAC-SHA256) and opaque
server-side session token generation.  No external dependencies
— uses only Python stdlib (hashlib, secrets, os).
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

# ── Configuration ─────────────────────────────────────────────────────────────

# Session TTLs
SESSION_TTL_DEFAULT = timedelta(hours=24)
SESSION_TTL_REMEMBER = timedelta(days=30)

# PBKDF2 parameters
_PBKDF2_ITERATIONS = 600_000
_SALT_LENGTH = 32
_HASH_LENGTH = 64


# ── Password Hashing ─────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """
    Hash a password using PBKDF2-HMAC-SHA256.

    Returns a string in the format: ``salt_hex$hash_hex$iterations``.
    """
    salt = os.urandom(_SALT_LENGTH)
    dk = hashlib.pbkdf2_hmac(
        "sha256", plain.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_HASH_LENGTH
    )
    return f"{salt.hex()}${dk.hex()}${_PBKDF2_ITERATIONS}"


def verify_password(plain: str, stored: str) -> bool:
    """Verify a password against its stored hash."""
    try:
        salt_hex, hash_hex, iterations_str = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        iterations = int(iterations_str)
        dk = hashlib.pbkdf2_hmac(
            "sha256", plain.encode("utf-8"), salt, iterations, dklen=len(bytes.fromhex(hash_hex))
        )
        return secrets.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ── Token Generation ──────────────────────────────────────────────────────────

def generate_token() -> str:
    """Generate a cryptographically secure opaque session token."""
    return secrets.token_urlsafe(48)


def compute_expiry(remember: bool = False) -> str:
    """Return an ISO-8601 expiry timestamp based on remember preference."""
    ttl = SESSION_TTL_REMEMBER if remember else SESSION_TTL_DEFAULT
    return (datetime.now(timezone.utc) + ttl).isoformat()

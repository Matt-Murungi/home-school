"""Hashing and code generation. Pure functions, no I/O."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_code() -> str:
    """Six digits from the OS CSPRNG, leading zeros kept."""
    return f"{secrets.randbelow(10**6):06d}"


def new_salt() -> str:
    return secrets.token_hex(16)


def hash_identifier(pepper: bytes, scope: str, value: str) -> str:
    """HMAC-SHA256(pepper, scope|value) as hex: a stable pseudonym for a phone number or an IP."""
    return hmac.new(pepper, f"id|{scope}|{value}".encode(), hashlib.sha256).hexdigest()


def hash_code(pepper: bytes, challenge_id: str, salt: str, code: str) -> str:
    """Salted (per challenge) and peppered (server secret) hash. The 6-digit space is tiny, so the
    pepper is what stops an attacker with a database copy from brute-forcing codes offline; the
    attempt and failure caps stop online guessing."""
    msg = f"code|{challenge_id}|{salt}|{code}".encode()
    return hmac.new(pepper, msg, hashlib.sha256).hexdigest()


def hashes_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)

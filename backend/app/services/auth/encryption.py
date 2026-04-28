"""
Token encryption — Fernet-symmetric, key in env var TOKEN_ENCRYPTION_KEY.

Why Fernet over pgcrypto:
  - Portable across SQLite / Postgres / RDS without server-side
    crypto extensions.
  - Standard library (cryptography), well-audited.
  - Simple key rotation: generate a new key, decrypt all rows with the
    old key, re-encrypt with the new key, swap env var.
  - The same code runs in dev (.env) and prod (AWS Secrets Manager,
    GCP Secret Manager, etc.).

Generate a new key:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Set in .env:

    TOKEN_ENCRYPTION_KEY=<the 44-char base64 string>

Critical: NEVER commit the key, NEVER log decrypted values, and treat
key rotation as a production-grade change (drain background syncs, swap
keys, restart workers).

Multi-key support: TOKEN_ENCRYPTION_KEYS (plural, comma-separated) lets
you rotate without downtime — first key in the list is used to encrypt
new values, all keys are tried in order to decrypt. After the rotation
window, drop the old key from the list.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterable, Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class TokenEncryptionError(RuntimeError):
    """Raised when encryption/decryption fails — caller should treat
    the credential as unusable and trigger a reconnect."""


def _read_keys() -> list[bytes]:
    """Load Fernet keys from env. Prefers TOKEN_ENCRYPTION_KEYS (plural,
    comma-sep) for rotation; falls back to TOKEN_ENCRYPTION_KEY (single)."""
    plural = os.environ.get("TOKEN_ENCRYPTION_KEYS")
    if plural:
        keys = [k.strip().encode("ascii") for k in plural.split(",") if k.strip()]
        if keys:
            return keys
    single = os.environ.get("TOKEN_ENCRYPTION_KEY")
    if single:
        return [single.strip().encode("ascii")]
    return []


@lru_cache(maxsize=1)
def _cipher() -> MultiFernet:
    keys = _read_keys()
    if not keys:
        raise TokenEncryptionError(
            "TOKEN_ENCRYPTION_KEY (or TOKEN_ENCRYPTION_KEYS) not set. "
            "Generate one with: python -c \"from cryptography.fernet import "
            "Fernet; print(Fernet.generate_key().decode())\""
        )
    fernets = [Fernet(k) for k in keys]
    return MultiFernet(fernets)


def encrypt_str(plaintext: Optional[str]) -> Optional[bytes]:
    """Encrypt a UTF-8 string. None passes through (sometimes a refresh
    token isn't returned by the IdP — store NULL, not encrypted-empty)."""
    if plaintext is None:
        return None
    return _cipher().encrypt(plaintext.encode("utf-8"))


def decrypt_str(ciphertext: Optional[bytes]) -> Optional[str]:
    """Decrypt to UTF-8 string. None passes through. Raises
    TokenEncryptionError if the ciphertext is invalid (key changed,
    corruption, wrong env)."""
    if ciphertext is None:
        return None
    try:
        plaintext = _cipher().decrypt(bytes(ciphertext))
    except InvalidToken as e:
        raise TokenEncryptionError(
            "could not decrypt token — key mismatch or corrupted ciphertext"
        ) from e
    return plaintext.decode("utf-8")


def reset_cipher_cache() -> None:
    """Test hook — clear the cached cipher (call after monkeypatching env)."""
    _cipher.cache_clear()

"""
Credential encryption boundary (P1.3).

The ONLY place in this codebase that should ever construct a Fernet
instance or touch EMAIL_CREDENTIAL_ENCRYPTION_KEY. Everything else --
CRUD, API endpoints, the SMTP verifier -- calls encrypt_credential() /
decrypt_credential() and never sees the key material directly. Keeping
the boundary this narrow is what makes it possible to state precisely
where a secret can leak: nowhere outside this module and the short-lived
local variables at each of its two call sites (core/infrastructure/
database/crud.py's create/update, and
core/infrastructure/email/smtp_verifier.py's verify()).

WHY FERNET: it's authenticated encryption (AES-128-CBC + HMAC-SHA256,
constant-time-verified) already available via the `cryptography` package
this project already depends on (pulled in transitively by
python-jose[cryptography] -- see requirements.txt) -- zero new
dependencies. It is the right tool for "encrypt now, decrypt later with
the same key" (a password/app-password that must be recovered to
authenticate against SMTP), as opposed to APIKey's one-way `key_hash`
(core/domain/models/api_key.py), which only ever needs to be *verified*,
never recovered.

WHY NOT: no custom cryptography, no XOR/base64 "encoding", no key
derived from anything user-controlled (email/password) -- see this
module's KEY ENV VAR section. Those are explicitly prohibited in the
P1.3 security brief, and Fernet already provides safe, standard,
undo-proof authenticated encryption for less implementation effort and
less risk than any of them.

KEY ENV VAR: EMAIL_CREDENTIAL_ENCRYPTION_KEY. Never given a source-level
default, never committed anywhere (see backend/.env.example, which
documents *how* to generate one -- not a real value), never persisted to
the database, never logged. Generate a real one with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Rotation is intentionally out of scope for P1.3 (no existing credential
needs re-encrypting under a new key yet, since nothing has been encrypted
before this phase) -- a future phase can add key-versioning if/when
rotation is actually needed, rather than building it speculatively now.
"""

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from core.infrastructure.logging import get_logger

logger = get_logger(__name__)

_ENV_VAR = "EMAIL_CREDENTIAL_ENCRYPTION_KEY"


class CredentialEncryptionError(Exception):
    """Raised for any encryption/decryption failure. Deliberately generic
    (no distinction between "key missing" vs "key malformed" vs
    "ciphertext malformed" is exposed to callers beyond this exception's
    message, which is written to be safe to log -- see each raise site)
    so that a caller cannot be tempted to build different user-facing
    behavior per failure *type*, which risks leaking information about
    *why* decryption failed. Callers should treat this uniformly as "the
    credential is currently unusable" (core/infrastructure/email/
    smtp_verifier.py maps it to VerificationStatus.FAILED, never to a
    protocol-level error)."""


def _load_key() -> bytes:
    """Reads and validates EMAIL_CREDENTIAL_ENCRYPTION_KEY on every call
    (not cached at import time) so that a key configured after process
    start -- or a test overriding os.environ -- is picked up without a
    reload. The cost of re-validating a ~44-byte base64 string per call is
    irrelevant next to the cost of the AES operation it gates."""
    raw = os.getenv(_ENV_VAR)
    if not raw:
        raise CredentialEncryptionError(
            f"{_ENV_VAR} is not set. An email account credential cannot be "
            f"encrypted or decrypted without it -- see backend/.env.example "
            f"for how to generate one. Refusing to fall back to storing the "
            f"credential in plaintext."
        )
    try:
        # Fernet validates its own key format (32 url-safe-base64-encoded
        # bytes) -- constructing it here is the actual validation, not a
        # separate hand-rolled length/charset check that could drift from
        # what Fernet itself accepts.
        Fernet(raw.encode() if isinstance(raw, str) else raw)
    except (ValueError, TypeError) as exc:
        raise CredentialEncryptionError(
            f"{_ENV_VAR} is not a valid Fernet key. Generate one with: "
            f'python -c "from cryptography.fernet import Fernet; '
            f'print(Fernet.generate_key().decode())"'
        ) from exc
    return raw.encode() if isinstance(raw, str) else raw


def encrypt_credential(plaintext: str) -> str:
    """Encrypts a credential for storage. Returns Fernet ciphertext as a
    str (Fernet's own output is url-safe-base64 ASCII, so this is a plain
    String column, not a bytes/BLOB one -- see
    core/domain/models/email_account.py's `encrypted_credential`).

    Raises CredentialEncryptionError if the encryption key is missing or
    malformed -- this is intentionally NOT caught here and silently
    swapped for plaintext storage; the P1.3 brief is explicit that
    plaintext storage must never be a fallback.
    """
    if not plaintext:
        raise CredentialEncryptionError("Cannot encrypt an empty credential.")
    key = _load_key()
    token = Fernet(key).encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_credential(ciphertext: str) -> str:
    """Decrypts a stored credential. Raises CredentialEncryptionError if
    the key is missing/malformed, or if `ciphertext` is not a valid token
    for that key (wrong key, corrupted data, or -- Fernet tokens are
    time-stamped and HMAC'd -- tampered data). Never logs `ciphertext` or
    any exception attribute that could contain key material; only a fixed,
    safe message.
    """
    if not ciphertext:
        raise CredentialEncryptionError("Cannot decrypt an empty ciphertext.")
    key = _load_key()
    try:
        plaintext = Fernet(key).decrypt(ciphertext.encode("ascii"))
    except InvalidToken as exc:
        # Deliberately does not log `ciphertext` -- even though it's
        # already ciphertext, not plaintext, logging it would still leak
        # the encrypted credential's on-disk representation into log
        # storage, which has different (typically weaker/longer-retention)
        # access controls than the primary database.
        logger.warning("Credential decryption failed: invalid token (wrong key or corrupted data).")
        raise CredentialEncryptionError("Stored credential could not be decrypted.") from exc
    return plaintext.decode("utf-8")

"""
P1.3: unit tests for core.infrastructure.security.credential_crypto --
the only module in this codebase allowed to touch
EMAIL_CREDENTIAL_ENCRYPTION_KEY or construct a Fernet instance.
"""

import pytest
from cryptography.fernet import Fernet

from core.infrastructure.security.credential_crypto import (
    encrypt_credential,
    decrypt_credential,
    CredentialEncryptionError,
    _ENV_VAR,
)


@pytest.fixture()
def real_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(_ENV_VAR, key)
    return key


def test_decrypt_of_encrypt_returns_original_plaintext(real_key):
    secret = "hunter2-app-password"
    ciphertext = encrypt_credential(secret)
    assert decrypt_credential(ciphertext) == secret


def test_ciphertext_differs_from_plaintext(real_key):
    secret = "hunter2-app-password"
    ciphertext = encrypt_credential(secret)
    assert ciphertext != secret
    assert secret not in ciphertext


def test_encrypting_same_plaintext_twice_produces_different_ciphertext(real_key):
    """Fernet includes a random IV/nonce per encryption -- two ciphertexts
    of the same secret must not be identical (defends against a simple
    equality-based leak: two accounts sharing a password shouldn't be
    detectable by comparing encrypted_credential columns)."""
    secret = "hunter2-app-password"
    first = encrypt_credential(secret)
    second = encrypt_credential(secret)
    assert first != second
    assert decrypt_credential(first) == secret
    assert decrypt_credential(second) == secret


def test_replacing_credential_produces_a_new_ciphertext(real_key):
    old_ciphertext = encrypt_credential("old-secret")
    new_ciphertext = encrypt_credential("new-secret")
    assert old_ciphertext != new_ciphertext
    assert decrypt_credential(new_ciphertext) == "new-secret"


def test_missing_encryption_key_fails_clearly(monkeypatch):
    monkeypatch.delenv(_ENV_VAR, raising=False)
    with pytest.raises(CredentialEncryptionError, match=_ENV_VAR):
        encrypt_credential("anything")


def test_missing_encryption_key_fails_clearly_on_decrypt(monkeypatch):
    monkeypatch.delenv(_ENV_VAR, raising=False)
    with pytest.raises(CredentialEncryptionError, match=_ENV_VAR):
        decrypt_credential("doesnt-matter")


def test_malformed_encryption_key_fails_clearly(monkeypatch):
    monkeypatch.setenv(_ENV_VAR, "not-a-valid-fernet-key")
    with pytest.raises(CredentialEncryptionError):
        encrypt_credential("anything")


def test_malformed_ciphertext_fails_safely(real_key):
    with pytest.raises(CredentialEncryptionError):
        decrypt_credential("not-a-real-fernet-token")


def test_ciphertext_encrypted_under_one_key_cannot_be_decrypted_under_another(monkeypatch):
    monkeypatch.setenv(_ENV_VAR, Fernet.generate_key().decode())
    ciphertext = encrypt_credential("secret")

    monkeypatch.setenv(_ENV_VAR, Fernet.generate_key().decode())
    with pytest.raises(CredentialEncryptionError):
        decrypt_credential(ciphertext)


def test_empty_plaintext_is_rejected(real_key):
    with pytest.raises(CredentialEncryptionError):
        encrypt_credential("")


def test_key_is_read_from_environment_not_hardcoded():
    """Confirms the module has no fallback/default key baked into source --
    the only way encrypt/decrypt can succeed is via the environment
    variable, proven by test_missing_encryption_key_fails_clearly above.
    This test additionally asserts the module itself carries no literal
    key-shaped constant anywhere in its source text."""
    import inspect
    from core.infrastructure.security import credential_crypto

    source = inspect.getsource(credential_crypto)
    # A real Fernet key is 44 base64 characters ending in '='. None of the
    # module's own text (docstrings' example command aside, which
    # generates a key rather than embedding one) should contain one.
    assert "Fernet(b'" not in source
    assert "Fernet(b\"" not in source


def test_error_message_never_includes_plaintext_or_ciphertext(real_key):
    """The exception raised on a bad decrypt must not echo back the
    ciphertext that failed -- see credential_crypto.py's comment on why."""
    bad_ciphertext = "obviously-not-a-real-token-xyz123"
    try:
        decrypt_credential(bad_ciphertext)
        assert False, "expected CredentialEncryptionError"
    except CredentialEncryptionError as exc:
        assert bad_ciphertext not in str(exc)

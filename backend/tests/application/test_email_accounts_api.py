"""
P1.3: HTTP-level tests for /api/v2/organizations/{org_id}/email-accounts.

Covers, per the P1.3 brief's mandatory test categories:
  - API response security (GET/POST/PATCH/verify/error responses never
    contain any credential-shaped field)
  - a log-capture test proving the supplied secret is absent from
    emitted logs
  - tenancy (organization A cannot GET/PATCH/DELETE/VERIFY organization
    B's account; queries are organization-scoped, not fetch-then-check)
  - create/update credential semantics (omitted credential preserves the
    existing one; a real credential replaces it and invalidates
    verification; unrelated metadata edits don't invalidate verification)
  - verification status transitions (mocked smtp_verifier -- no real
    network, see tests/application/test_smtp_verifier.py for the
    verifier's own deterministic tests)
"""

import json
import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from core.domain.models.email_account import VerificationStatus
from core.infrastructure.email.smtp_verifier import VerificationResult, VerificationErrorCode

SECRET_PASSWORD = "hunter2-app-password-do-not-leak"

# Every key that must never appear anywhere in a response body.
_FORBIDDEN_RESPONSE_KEYS = {
    "encrypted_credential", "credential", "password", "app_password",
    "access_token", "refresh_token", "plaintext",
}


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


def _register_and_login(client, email):
    r = client.post(
        "/api/v2/register",
        json={"email": email, "password": "TestPass123!", "first_name": "Sender"},
    )
    assert r.status_code == 200, r.text
    r2 = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r2.status_code == 200, r2.text
    token = r2.json()["access_token"]
    me = client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"}).json()
    return token, me["organization_id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _base_payload(**overrides):
    payload = {
        "provider": "smtp",
        "email_address": "sender@example.com",
        "display_name": "Sales Outreach",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "security_mode": "starttls",
        "credential_type": "smtp_password",
        "credential": SECRET_PASSWORD,
    }
    payload.update(overrides)
    return payload


def _assert_response_never_leaks_secret(response_json):
    """Recursively checks an entire response body: no forbidden key name,
    and the literal secret string never appears anywhere as a value."""
    text = json.dumps(response_json)
    assert SECRET_PASSWORD not in text

    def _walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in _FORBIDDEN_RESPONSE_KEYS, f"forbidden key '{key}' present in response"
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(response_json)


# --------------------------------------------------------------------------
# Response security
# --------------------------------------------------------------------------

class TestResponseNeverLeaksCredential:
    def test_create_response_has_no_credential(self, client):
        token, org_id = _register_and_login(client, "sec_create@example.com")
        r = client.post(f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload())
        assert r.status_code == 201
        _assert_response_never_leaks_secret(r.json())

    def test_list_response_has_no_credential(self, client):
        token, org_id = _register_and_login(client, "sec_list@example.com")
        client.post(f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload())

        r = client.get(f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token))
        assert r.status_code == 200
        _assert_response_never_leaks_secret(r.json())

    def test_get_single_response_has_no_credential(self, client):
        token, org_id = _register_and_login(client, "sec_get@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()

        r = client.get(f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}", headers=_auth(token))
        assert r.status_code == 200
        _assert_response_never_leaks_secret(r.json())

    def test_patch_response_has_no_credential(self, client):
        token, org_id = _register_and_login(client, "sec_patch@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()

        r = client.patch(
            f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}",
            headers=_auth(token),
            json={"credential": "a-brand-new-secret-value"},
        )
        assert r.status_code == 200
        _assert_response_never_leaks_secret(r.json())
        assert "a-brand-new-secret-value" not in json.dumps(r.json())

    def test_verify_response_has_no_credential(self, client):
        token, org_id = _register_and_login(client, "sec_verify@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()

        with patch("api.endpoints.email_accounts.verify_smtp_mailbox") as mock_verify:
            mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED)
            r = client.post(
                f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}/verify", headers=_auth(token)
            )
        assert r.status_code == 200
        _assert_response_never_leaks_secret(r.json())

    def test_delete_response_has_no_credential(self, client):
        token, org_id = _register_and_login(client, "sec_delete@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()

        r = client.delete(f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}", headers=_auth(token))
        assert r.status_code == 200
        _assert_response_never_leaks_secret(r.json())

    def test_validation_error_response_has_no_credential(self, client):
        """A 422 (e.g. bad smtp_port) must not echo the credential the
        client submitted alongside the invalid field back in the error
        body."""
        token, org_id = _register_and_login(client, "sec_error@example.com")
        r = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts",
            headers=_auth(token),
            json=_base_payload(smtp_port=999999),
        )
        assert r.status_code == 422
        assert SECRET_PASSWORD not in r.text

    def test_response_schema_has_no_credential_field_at_all(self):
        """Structural guarantee, not just a per-response check: the
        response Pydantic model itself has no field that could ever carry
        a credential."""
        from core.domain.schemas.email_account import EmailAccount as EmailAccountSchema

        field_names = set(EmailAccountSchema.model_fields.keys())
        assert not (field_names & _FORBIDDEN_RESPONSE_KEYS)


class TestSecretNeverLogged:
    def test_verification_attempt_does_not_log_the_secret(self, client, caplog):
        token, org_id = _register_and_login(client, "sec_log@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()

        with caplog.at_level(logging.DEBUG):
            with patch("api.endpoints.email_accounts.verify_smtp_mailbox") as mock_verify:
                mock_verify.return_value = VerificationResult(
                    status=VerificationStatus.FAILED, error_code=VerificationErrorCode.AUTH_FAILED
                )
                client.post(
                    f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}/verify", headers=_auth(token)
                )

        for record in caplog.records:
            assert SECRET_PASSWORD not in record.getMessage()

    def test_creation_does_not_log_the_secret(self, client, caplog):
        token, org_id = _register_and_login(client, "sec_log_create@example.com")
        with caplog.at_level(logging.DEBUG):
            client.post(f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload())

        for record in caplog.records:
            assert SECRET_PASSWORD not in record.getMessage()


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------

class TestTenancy:
    def test_organization_b_cannot_list_organization_a_accounts(self, client):
        token_a, org_a = _register_and_login(client, "tenancy_list_a@example.com")
        token_b, org_b = _register_and_login(client, "tenancy_list_b@example.com")
        client.post(f"/api/v2/organizations/{org_a}/email-accounts", headers=_auth(token_a), json=_base_payload())

        r = client.get(f"/api/v2/organizations/{org_a}/email-accounts", headers=_auth(token_b))
        assert r.status_code == 403

    def test_organization_b_cannot_get_organization_a_account(self, client):
        token_a, org_a = _register_and_login(client, "tenancy_get_a@example.com")
        token_b, org_b = _register_and_login(client, "tenancy_get_b@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_a}/email-accounts", headers=_auth(token_a), json=_base_payload()
        ).json()

        # Even if B somehow knew A's account id and tried B's own org_id
        # in the path (the only org_id B is authorized for), the
        # organization-scoped query must not find A's row under B's id.
        r = client.get(f"/api/v2/organizations/{org_b}/email-accounts/{created['id']}", headers=_auth(token_b))
        assert r.status_code == 404

        # And B is flatly forbidden from org_a's path regardless of id.
        r2 = client.get(f"/api/v2/organizations/{org_a}/email-accounts/{created['id']}", headers=_auth(token_b))
        assert r2.status_code == 403

    def test_organization_b_cannot_patch_organization_a_account(self, client):
        token_a, org_a = _register_and_login(client, "tenancy_patch_a@example.com")
        token_b, org_b = _register_and_login(client, "tenancy_patch_b@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_a}/email-accounts", headers=_auth(token_a), json=_base_payload()
        ).json()

        r = client.patch(
            f"/api/v2/organizations/{org_a}/email-accounts/{created['id']}",
            headers=_auth(token_b),
            json={"display_name": "Hijacked"},
        )
        assert r.status_code == 403

        # Confirm it was never actually changed.
        reread = client.get(
            f"/api/v2/organizations/{org_a}/email-accounts/{created['id']}", headers=_auth(token_a)
        ).json()
        assert reread["display_name"] == "Sales Outreach"

    def test_organization_b_cannot_delete_organization_a_account(self, client):
        token_a, org_a = _register_and_login(client, "tenancy_delete_a@example.com")
        token_b, org_b = _register_and_login(client, "tenancy_delete_b@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_a}/email-accounts", headers=_auth(token_a), json=_base_payload()
        ).json()

        r = client.delete(f"/api/v2/organizations/{org_a}/email-accounts/{created['id']}", headers=_auth(token_b))
        assert r.status_code == 403

        reread = client.get(
            f"/api/v2/organizations/{org_a}/email-accounts/{created['id']}", headers=_auth(token_a)
        ).json()
        assert reread["is_active"] is True

    def test_organization_b_cannot_verify_organization_a_account(self, client):
        token_a, org_a = _register_and_login(client, "tenancy_verify_a@example.com")
        token_b, org_b = _register_and_login(client, "tenancy_verify_b@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_a}/email-accounts", headers=_auth(token_a), json=_base_payload()
        ).json()

        with patch("api.endpoints.email_accounts.verify_smtp_mailbox") as mock_verify:
            r = client.post(
                f"/api/v2/organizations/{org_a}/email-accounts/{created['id']}/verify", headers=_auth(token_b)
            )
        assert r.status_code == 403
        mock_verify.assert_not_called()  # never even attempted

    def test_endpoints_require_auth(self, client):
        assert client.get("/api/v2/organizations/1/email-accounts").status_code == 403
        assert client.post("/api/v2/organizations/1/email-accounts", json=_base_payload()).status_code == 403


# --------------------------------------------------------------------------
# Create / update credential semantics
# --------------------------------------------------------------------------

class TestCredentialSemantics:
    def test_omitting_credential_on_update_preserves_existing_one(self, client):
        token, org_id = _register_and_login(client, "cred_preserve@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()

        with patch("api.endpoints.email_accounts.verify_smtp_mailbox") as mock_verify:
            mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED)
            client.post(f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}/verify", headers=_auth(token))

        # Unrelated metadata-only update -- no `credential` key at all.
        r = client.patch(
            f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}",
            headers=_auth(token),
            json={"display_name": "Renamed"},
        )
        assert r.status_code == 200
        assert r.json()["display_name"] == "Renamed"
        # Verification status must be untouched -- metadata-only changes
        # don't invalidate a working, previously-verified connection.
        assert r.json()["verification_status"] == VerificationStatus.VERIFIED

    def test_supplying_new_credential_invalidates_verification(self, client):
        token, org_id = _register_and_login(client, "cred_invalidate@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()

        with patch("api.endpoints.email_accounts.verify_smtp_mailbox") as mock_verify:
            mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED)
            client.post(f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}/verify", headers=_auth(token))

        r = client.patch(
            f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}",
            headers=_auth(token),
            json={"credential": "a-new-app-password"},
        )
        assert r.status_code == 200
        assert r.json()["verification_status"] == VerificationStatus.UNVERIFIED
        assert r.json()["verified_at"] is None

    def test_changing_smtp_host_invalidates_verification(self, client):
        token, org_id = _register_and_login(client, "cred_invalidate_host@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()
        with patch("api.endpoints.email_accounts.verify_smtp_mailbox") as mock_verify:
            mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED)
            client.post(f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}/verify", headers=_auth(token))

        r = client.patch(
            f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}",
            headers=_auth(token),
            json={"smtp_host": "smtp.newhost.example.com"},
        )
        assert r.json()["verification_status"] == VerificationStatus.UNVERIFIED

    def test_create_rejects_oauth_token_credential_type(self, client):
        token, org_id = _register_and_login(client, "cred_oauth@example.com")
        r = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts",
            headers=_auth(token),
            json=_base_payload(credential_type="oauth_token"),
        )
        assert r.status_code == 422

    def test_create_rejects_invalid_smtp_port(self, client):
        token, org_id = _register_and_login(client, "cred_badport@example.com")
        r = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts",
            headers=_auth(token),
            json=_base_payload(smtp_port=0),
        )
        assert r.status_code == 422

    def test_username_defaults_to_email_address_when_omitted(self, client):
        token, org_id = _register_and_login(client, "cred_username_default@example.com")
        payload = _base_payload()
        payload.pop("display_name")
        r = client.post(f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=payload)
        assert r.status_code == 201
        assert r.json()["username"] == payload["email_address"]


# --------------------------------------------------------------------------
# Verification status transitions
# --------------------------------------------------------------------------

class TestVerificationTransitions:
    def test_new_account_starts_unverified(self, client):
        token, org_id = _register_and_login(client, "verif_default@example.com")
        r = client.post(f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload())
        assert r.json()["verification_status"] == VerificationStatus.UNVERIFIED
        assert r.json()["verified_at"] is None

    def test_successful_verification_sets_verified_and_timestamp(self, client):
        token, org_id = _register_and_login(client, "verif_success@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()

        with patch("api.endpoints.email_accounts.verify_smtp_mailbox") as mock_verify:
            mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED)
            r = client.post(
                f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}/verify", headers=_auth(token)
            )

        assert r.json()["verification_status"] == VerificationStatus.VERIFIED
        assert r.json()["verified_at"] is not None
        assert r.json()["verification_error_code"] is None

    def test_failed_verification_sets_failed_with_error_code_and_no_timestamp(self, client):
        token, org_id = _register_and_login(client, "verif_fail@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()

        with patch("api.endpoints.email_accounts.verify_smtp_mailbox") as mock_verify:
            mock_verify.return_value = VerificationResult(
                status=VerificationStatus.FAILED, error_code=VerificationErrorCode.AUTH_FAILED
            )
            r = client.post(
                f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}/verify", headers=_auth(token)
            )

        assert r.json()["verification_status"] == VerificationStatus.FAILED
        assert r.json()["verification_error_code"] == VerificationErrorCode.AUTH_FAILED
        assert r.json()["verified_at"] is None

    def test_disabled_account_is_rejected_deterministically_without_network_call(self, client):
        token, org_id = _register_and_login(client, "verif_disabled@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()
        client.delete(f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}", headers=_auth(token))

        with patch("api.endpoints.email_accounts.verify_smtp_mailbox") as mock_verify:
            r = client.post(
                f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}/verify", headers=_auth(token)
            )

        assert r.json()["verification_status"] == VerificationStatus.DISABLED
        mock_verify.assert_not_called()

    def test_verifying_account_with_no_credential_set_fails_without_network_call(self, client):
        token, org_id = _register_and_login(client, "verif_no_cred@example.com")
        payload = _base_payload()
        payload.pop("credential")
        created = client.post(f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=payload).json()

        with patch("api.endpoints.email_accounts.verify_smtp_mailbox") as mock_verify:
            r = client.post(
                f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}/verify", headers=_auth(token)
            )

        assert r.json()["verification_status"] == VerificationStatus.FAILED
        mock_verify.assert_not_called()

    def test_repeated_verification_is_deterministic(self, client):
        token, org_id = _register_and_login(client, "verif_repeat@example.com")
        created = client.post(
            f"/api/v2/organizations/{org_id}/email-accounts", headers=_auth(token), json=_base_payload()
        ).json()

        with patch("api.endpoints.email_accounts.verify_smtp_mailbox") as mock_verify:
            mock_verify.return_value = VerificationResult(
                status=VerificationStatus.FAILED, error_code=VerificationErrorCode.TIMEOUT
            )
            first = client.post(
                f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}/verify", headers=_auth(token)
            ).json()
            second = client.post(
                f"/api/v2/organizations/{org_id}/email-accounts/{created['id']}/verify", headers=_auth(token)
            ).json()

        assert first["verification_status"] == second["verification_status"]
        assert first["verification_error_code"] == second["verification_error_code"]
        assert mock_verify.call_count == 2  # one real attempt per call, no caching/retry

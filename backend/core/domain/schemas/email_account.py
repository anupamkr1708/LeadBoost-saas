"""
Pydantic schemas for EmailAccount (P1.3).

SECURITY: `EmailAccount` (the response schema) has NO field that could
ever carry a credential -- not `encrypted_credential`, not a masked
placeholder, nothing. This is deliberate: rather than trusting every
endpoint to remember to `.exclude()` a sensitive field, the field simply
doesn't exist on the type the API is declared to return, so FastAPI's own
response-model filtering (and any test asserting the response shape)
makes the omission structural, not conventional. `EmailAccountCreate`/
`EmailAccountUpdate` accept a *plaintext* `credential` field, because
that's the one and only place plaintext should ever appear on the wire
(POST/PATCH from the client, immediately encrypted in the CRUD layer --
see core/infrastructure/database/crud.py -- and never referenced again).
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from core.domain.models.email_account import CredentialType, SecurityMode


class SecurityModeEnum(str, Enum):
    starttls = SecurityMode.STARTTLS
    tls = SecurityMode.TLS


class CredentialTypeEnum(str, Enum):
    smtp_password = CredentialType.SMTP_PASSWORD
    app_password = CredentialType.APP_PASSWORD
    oauth_token = CredentialType.OAUTH_TOKEN


def _reject_unimplemented_credential_type(value: CredentialTypeEnum) -> CredentialTypeEnum:
    if value.value not in CredentialType.IMPLEMENTED:
        raise ValueError(
            f"credential_type '{value.value}' is not implemented in this phase "
            f"(P1.3 implements: {', '.join(CredentialType.IMPLEMENTED)}). OAuth-based "
            f"mailbox connection is a future phase, not silently accepted here."
        )
    return value


class EmailAccountBase(BaseModel):
    provider: str = "smtp"
    email_address: EmailStr
    display_name: Optional[str] = None
    smtp_host: str = Field(min_length=1)
    smtp_port: int = Field(gt=0, le=65535)
    security_mode: SecurityModeEnum = SecurityModeEnum.starttls
    # SMTP auth username -- if omitted at creation, the CRUD layer uses
    # email_address (the overwhelmingly common case: username == the
    # mailbox's own address). Kept distinct because some providers/shared
    # mailboxes authenticate under a different username than the address
    # mail appears to come from.
    username: Optional[str] = None


class EmailAccountCreate(EmailAccountBase):
    credential_type: CredentialTypeEnum = CredentialTypeEnum.smtp_password
    # Plaintext, write-only. Optional at creation (an account can be
    # created with connection metadata and have its credential set in a
    # follow-up PATCH), but verification cannot succeed without one.
    credential: Optional[str] = Field(default=None, min_length=1)

    _validate_credential_type = field_validator("credential_type")(_reject_unimplemented_credential_type)


class EmailAccountUpdate(BaseModel):
    """All fields optional -- exclude_unset in the CRUD layer means an
    ordinary metadata edit (e.g. just display_name) never has to resend
    smtp_host/port/credential, and specifically never has to resend the
    credential just because some other field changed (see the P1.3
    brief's "do not make the frontend resend the secret for every
    ordinary edit")."""

    display_name: Optional[str] = None
    smtp_host: Optional[str] = Field(default=None, min_length=1)
    smtp_port: Optional[int] = Field(default=None, gt=0, le=65535)
    security_mode: Optional[SecurityModeEnum] = None
    username: Optional[str] = None
    is_active: Optional[bool] = None
    credential_type: Optional[CredentialTypeEnum] = None
    # Omitted (the default) => existing encrypted_credential is preserved
    # untouched. A real string => replaced (re-encrypted) and previous
    # verification is invalidated -- see crud.update_email_account.
    credential: Optional[str] = Field(default=None, min_length=1)

    @field_validator("credential_type")
    @classmethod
    def _validate_credential_type(cls, value):
        if value is None:
            return value
        return _reject_unimplemented_credential_type(value)


class EmailAccount(BaseModel):
    """Response shape. Deliberately has no credential-shaped field at
    all -- see this module's docstring."""

    id: int
    organization_id: int
    provider: str
    email_address: str
    display_name: Optional[str] = None
    smtp_host: str
    smtp_port: int
    security_mode: str
    username: Optional[str] = None
    is_active: bool
    credential_type: str
    verification_status: str
    verified_at: Optional[datetime] = None
    verification_error_code: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class EmailAccountVerifyResult(BaseModel):
    """Response of POST .../verify -- the safe VerificationResult
    (core/infrastructure/email/smtp_verifier.py) plus the account's
    updated persisted state, so the frontend can update its view from one
    response without a second GET."""

    verification_status: str
    verification_error_code: Optional[str] = None
    verified_at: Optional[datetime] = None

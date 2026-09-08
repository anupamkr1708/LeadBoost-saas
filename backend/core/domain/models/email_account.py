"""
EmailAccount model (P1.3).

An organization-scoped sender mailbox: connection metadata plus an
*encrypted* credential, and a verification status. This is the secure
foundation P1.4 (real outreach sending) will build on -- P1.3 itself does
not send any email.

    Organization
        1
        |
        *
    EmailAccount

Ownership is the organization, not the individual user, matching the
Organization "Company Profile" / "Qualification Settings" pattern already
established in P1.2 (core/domain/models/organization.py,
core/domain/models/qualification_settings.py) rather than the User
"Sender Profile" pattern (job_title/signature) -- a mailbox is a shared
organizational resource multiple users' outreach can be sent from, not a
personal attribute of one user.

SECURITY: `encrypted_credential` is Fernet ciphertext (see
core/infrastructure/security/credential_crypto.py), never plaintext. It is
intentionally excluded from every Pydantic response schema (see
core/domain/schemas/email_account.py) -- there is no "safe" serialization
of this column, so schemas simply never reference it, rather than relying
on every call site to remember to redact it.
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint, Index, CheckConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from core.infrastructure.database import Base


class CredentialType:
    """Plain string constants, not a DB-level enum type -- keeps adding a
    future type (e.g. an OAUTH_TOKEN implementation in a later phase) an
    additive, zero-migration change, consistent with how
    core.domain.models.lead.Lead.qualification_label is a validated
    String, not a Postgres ENUM. Application-level validation (see
    core/domain/schemas/email_account.py) is what actually constrains
    which values are accepted -- not a DB CHECK -- so a new type can be
    supported by relaxing that validation alone, no ALTER TYPE required.

    Only SMTP_PASSWORD is implemented in P1.3. APP_PASSWORD is included
    because operationally it's the exact same generic-SMTP flow (a
    provider-issued secret used as a plain SMTP password -- e.g. a Gmail
    "app password" -- authenticated identically to SMTP_PASSWORD, just
    named distinctly so the UI/audit trail can tell a raw account
    password apart from a scoped app-specific one). OAUTH_TOKEN is a
    named placeholder for a real OAuth implementation later -- it is
    listed so the column is never "permanently locked to gmail_password",
    per the P1.3 brief, but no OAuth flow exists yet, so the schema
    rejects it as NOT_IMPLEMENTED (see email_account.py) rather than
    silently accepting a value it cannot actually use to authenticate.
    """

    SMTP_PASSWORD = "smtp_password"
    APP_PASSWORD = "app_password"
    OAUTH_TOKEN = "oauth_token"

    IMPLEMENTED = (SMTP_PASSWORD, APP_PASSWORD)
    ALL = (SMTP_PASSWORD, APP_PASSWORD, OAUTH_TOKEN)


class VerificationStatus:
    """Explicit, deterministic status set -- see
    core/infrastructure/email/smtp_verifier.py for the exact protocol
    outcomes that map to each one. No PENDING state: unlike an
    async/queued verification, this system performs verification
    synchronously within the request (bounded timeout, single attempt --
    see the verifier's docstring), so there is no window where "a
    verification is in flight" needs its own persisted state. A
    newly-created, never-verified account simply has verified_at=None and
    verification_status=UNVERIFIED, which is enough to distinguish "never
    checked" from every failure/success outcome the brief asks for.
    """

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    REQUIRES_REAUTH = "requires_reauth"
    DISABLED = "disabled"

    ALL = (UNVERIFIED, VERIFIED, FAILED, REQUIRES_REAUTH, DISABLED)


class SecurityMode:
    """Explicit, not inferred from port number -- see the P1.3 brief's
    "do not silently downgrade security" / "do not guess" requirements.
    Port 587 conventionally means STARTTLS and 465 conventionally means
    implicit TLS, but "conventionally" is exactly the kind of heuristic
    the brief prohibits; the caller states which one a given server
    actually requires."""

    STARTTLS = "starttls"
    TLS = "tls"  # implicit TLS (connect already inside a TLS session)

    ALL = (STARTTLS, TLS)


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)

    # --- Safe metadata (returned to the frontend as-is) ---
    provider = Column(String, nullable=False, default="smtp")
    email_address = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    smtp_host = Column(String, nullable=False)
    smtp_port = Column(Integer, nullable=False)
    security_mode = Column(String, nullable=False, default=SecurityMode.STARTTLS)
    username = Column(String, nullable=True)  # SMTP auth username, if it differs from email_address
    is_active = Column(Boolean, nullable=False, default=True)  # soft delete/disable, same pattern as Lead.is_active

    # --- Credential (NEVER returned to the frontend) ---
    credential_type = Column(String, nullable=False, default=CredentialType.SMTP_PASSWORD)
    # Fernet ciphertext (base64 text), or NULL if no credential has been
    # set yet (e.g. an account created but not yet configured with a
    # secret). See core/infrastructure/security/credential_crypto.py.
    encrypted_credential = Column(String, nullable=True)

    # --- Verification state ---
    verification_status = Column(String, nullable=False, default=VerificationStatus.UNVERIFIED)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    # Safe, bounded error classification only -- never the raw SMTP
    # response/exception text. See smtp_verifier.py's VerificationResult.
    verification_error_code = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        # Scoped to the organization, not global -- the same email address
        # can legitimately be a sender mailbox for two different
        # organizations (e.g. a shared agency inbox, or simple coincidence).
        UniqueConstraint("organization_id", "email_address", name="uq_email_accounts_org_email"),
        # organization_id already gets a btree index from index=True above
        # (every list/ownership-check query filters on it); this second
        # index supports the one additional real access pattern -- listing
        # only the currently-usable accounts for an organization (P1.4's
        # sender-selection query, and this phase's own list endpoint's
        # common case of hiding disabled accounts).
        Index("ix_email_accounts_org_active", "organization_id", "is_active"),
        # A valid TCP port is 1-65535 -- a structural fact, not a business
        # rule that might change, so it belongs in the database, not only
        # in the Pydantic schema (core/domain/schemas/email_account.py
        # already validates this too, at the point of user input; the
        # CHECK is the "true invariant" backstop the P1.3 brief asks for).
        # Deliberately NOT doing the same for credential_type/
        # verification_status/security_mode: those are intentionally left
        # as unconstrained strings (see CredentialType's docstring above)
        # so a future value is an additive, zero-migration change --
        # a CHECK listing today's exact allowed values would defeat that.
        CheckConstraint("smtp_port > 0 AND smtp_port <= 65535", name="ck_email_accounts_smtp_port_range"),
    )

    organization = relationship("Organization", back_populates="email_accounts")

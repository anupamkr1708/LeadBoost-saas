"""
Mailer contract boundary (L1).

Everything LeadBoost sends to the separate Mailer service (a different
repository/deployment -- see CONTRACT.md in this package for the wire
contract) lives in this package. Nothing outside it should construct a
Mailer request payload directly.

  client.py          transport + the generated-outreach handoff (NO credential)
  mailbox_client.py  mailbox provisioning (the only credential-bearing calls)
"""

from core.infrastructure.mailing_agent.client import (
    DispatchErrorCode,
    DispatchResult,
    MailerResponse,
    is_configured,
    submit_generated_outreach,
)

__all__ = [
    "DispatchErrorCode",
    "DispatchResult",
    "MailerResponse",
    "is_configured",
    "submit_generated_outreach",
]

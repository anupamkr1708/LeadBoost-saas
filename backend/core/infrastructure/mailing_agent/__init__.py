"""
Mailing Agent contract boundary (P1.4).

Everything LeadBoost sends to the separate Mailing Agent service (a
different repository/deployment -- see CONTRACT.md in this package for
the full wire contract) lives in this package. Nothing outside
`client.py` should construct a Mailing Agent request payload directly.
"""

from core.infrastructure.mailing_agent.client import (
    DispatchErrorCode,
    DispatchResult,
    dispatch_outreach_action,
    is_configured,
)

__all__ = [
    "DispatchErrorCode",
    "DispatchResult",
    "dispatch_outreach_action",
    "is_configured",
]

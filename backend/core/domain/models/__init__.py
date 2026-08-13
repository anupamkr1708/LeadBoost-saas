"""
Central model registry.

Every SQLAlchemy model module must be imported here, and nowhere else
conditionally. Relationships declared with a string class name (e.g.
`relationship("APIKey")` in organization.py/user.py) are only resolved
when SQLAlchemy's `configure_mappers()` runs, and it can only see classes
that have already been imported into Python's module registry at that
moment.

Before this file existed (it was empty), whether that worked depended
entirely on which *other*, unrelated module happened to import
`core.domain.models.api_key` first -- e.g. importing the FastAPI app (via
crud.py, which imports every model) incidentally registered everything,
so a full pytest run usually passed; but running a single test in
isolation, a different test order, or a test file that never imports
crud.py before touching the ORM, raised:

    sqlalchemy.exc.InvalidRequestError: When initializing mapper
    Mapper[User(users)], expression 'APIKey' failed to locate a name
    ('APIKey'). ...

`core/infrastructure/database/__init__.py` imports this module
immediately after `Base` is defined, so anything importing
`core.infrastructure.database` -- which is effectively every entry point
into this codebase -- registers every model, unconditionally, regardless
of import order.
"""

from core.domain.models.organization import Organization
from core.domain.models.user import User
from core.domain.models.api_key import APIKey
from core.domain.models.lead import Lead, LeadEnrichmentLog, ScrapingLog, AIDecisionLog
from core.domain.models.subscription import Plan
from core.domain.models.billing import Subscription, UsageRecord, Invoice

__all__ = [
    "Organization",
    "User",
    "APIKey",
    "Lead",
    "LeadEnrichmentLog",
    "ScrapingLog",
    "AIDecisionLog",
    "Plan",
    "Subscription",
    "UsageRecord",
    "Invoice",
]

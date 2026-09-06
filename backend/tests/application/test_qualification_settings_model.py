"""
P1.2: OrganizationQualificationSettings -- model/CRUD-level tests.

These exercise core.infrastructure.database.crud.get_or_create_qualification_settings
and update_qualification_settings directly against the ORM/DB layer (no
HTTP), complementing tests/application/test_qualification_settings_api.py's
end-to-end API coverage. See core/domain/models/qualification_settings.py
for the full design rationale this is testing against.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from core.domain.models.qualification_settings import (
    OrganizationQualificationSettings,
    DEFAULT_QUALIFICATION_THRESHOLD,
)
from core.domain.schemas.qualification_settings import QualificationSettingsUpdate
from core.infrastructure.database.crud import (
    get_or_create_qualification_settings,
    update_qualification_settings,
)


def test_get_or_create_creates_default_row_when_none_exists(db_session, sample_org):
    """A brand-new organization (or, equivalently, any pre-P1.2 organization
    that predates this table) has no settings row until first read."""
    assert (
        db_session.query(OrganizationQualificationSettings)
        .filter_by(organization_id=sample_org.id)
        .first()
        is None
    )

    settings = get_or_create_qualification_settings(db_session, sample_org.id)

    assert settings.qualification_threshold == DEFAULT_QUALIFICATION_THRESHOLD
    assert settings.organization_id == sample_org.id
    # Actually persisted, not just an in-memory default -- a second read
    # (even a fresh query, not the same Python object) sees the same row.
    persisted = (
        db_session.query(OrganizationQualificationSettings)
        .filter_by(organization_id=sample_org.id)
        .one()
    )
    assert persisted.id == settings.id


def test_get_or_create_is_idempotent(db_session, sample_org):
    """Calling get_or_create twice must not create two rows (would violate
    the unique constraint on organization_id, but also just conceptually
    wrong -- one settings row per organization)."""
    first = get_or_create_qualification_settings(db_session, sample_org.id)
    second = get_or_create_qualification_settings(db_session, sample_org.id)

    assert first.id == second.id
    count = (
        db_session.query(OrganizationQualificationSettings)
        .filter_by(organization_id=sample_org.id)
        .count()
    )
    assert count == 1


def test_update_changes_threshold(db_session, sample_org):
    updated = update_qualification_settings(
        db_session, sample_org.id, QualificationSettingsUpdate(qualification_threshold=75.0)
    )
    assert updated.qualification_threshold == 75.0

    reread = get_or_create_qualification_settings(db_session, sample_org.id)
    assert reread.qualification_threshold == 75.0


def test_update_on_organization_with_no_existing_row_creates_one(db_session, sample_org):
    """update_qualification_settings must work even before any row exists --
    it should not require a prior get_or_create call."""
    assert (
        db_session.query(OrganizationQualificationSettings)
        .filter_by(organization_id=sample_org.id)
        .first()
        is None
    )

    updated = update_qualification_settings(
        db_session, sample_org.id, QualificationSettingsUpdate(qualification_threshold=90.0)
    )
    assert updated.qualification_threshold == 90.0


def test_partial_update_with_no_fields_set_is_a_noop(db_session, sample_org):
    """QualificationSettingsUpdate() with nothing set (exclude_unset=True in
    the CRUD layer) must not blow away the existing value with a Pydantic
    default/None."""
    update_qualification_settings(
        db_session, sample_org.id, QualificationSettingsUpdate(qualification_threshold=42.0)
    )

    result = update_qualification_settings(db_session, sample_org.id, QualificationSettingsUpdate())
    assert result.qualification_threshold == 42.0


@pytest.mark.parametrize("threshold", [0.0, 100.0, 50.5])
def test_valid_threshold_values_accepted_by_schema(threshold):
    schema = QualificationSettingsUpdate(qualification_threshold=threshold)
    assert schema.qualification_threshold == threshold


@pytest.mark.parametrize("threshold", [-0.01, 100.01, -50.0, 1000.0])
def test_invalid_threshold_values_rejected_by_schema(threshold):
    """Out-of-[0,100]-range thresholds must be rejected at the Pydantic
    layer before ever reaching the database -- see
    core/domain/schemas/qualification_settings.py's Field(ge=0.0, le=100.0)."""
    with pytest.raises(Exception):
        QualificationSettingsUpdate(qualification_threshold=threshold)


def test_organization_ownership_is_one_to_one(db_session, sample_org):
    """The organization_id column is a unique FK -- a second settings row
    for the same organization must be rejected at the database level, not
    just prevented by application code remembering to check first."""
    get_or_create_qualification_settings(db_session, sample_org.id)

    duplicate = OrganizationQualificationSettings(
        organization_id=sample_org.id, qualification_threshold=10.0
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_default_threshold_matches_legacy_warm_lead_cutoff():
    """Documents *why* 60.0 was chosen: it's not an arbitrary number, it's
    the existing "Warm Lead" cutoff in
    core.domain.services.scoring.LeadScoringService._classify_lead, chosen
    so that an organization that never touches this setting sees behavior
    consistent with what the dashboard's (buggy) qualified-KPI logic was
    already trying to express before P1.2."""
    from core.domain.services.scoring import LeadScoringService

    service = LeadScoringService()
    assert DEFAULT_QUALIFICATION_THRESHOLD == 60.0
    assert service._classify_lead(60.0) == "Warm Lead"
    assert service._classify_lead(59.99) == "Cold Lead"

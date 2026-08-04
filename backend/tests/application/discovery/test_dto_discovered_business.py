"""
Unit tests for the Sprint 4 addition to application.discovery.dto:
DiscoveredBusiness.identity.
"""

from application.discovery.dto import BusinessCandidate, DiscoveredBusiness, WebsiteResolution


def _discovered_business(identity=None):
    return DiscoveredBusiness(
        candidate=BusinessCandidate(name="Nike"),
        resolution=WebsiteResolution(website="https://nike.com", resolved_via="overpass", validated=True),
        identity=identity,
    )


def test_identity_defaults_to_none():
    business = DiscoveredBusiness(candidate=BusinessCandidate(name="Nike"), resolution=WebsiteResolution())
    assert business.identity is None


def test_identity_accepts_an_arbitrary_object_without_validation_error():
    class FakeVerifiedDigitalIdentity:
        def __init__(self):
            self.selected_website = "https://nike.com"

    business = _discovered_business(identity=FakeVerifiedDigitalIdentity())
    assert business.identity.selected_website == "https://nike.com"


def test_identity_field_does_not_break_construction_without_it():
    """Every pre-Sprint-4 construction site (positional or keyword,
    omitting `identity` entirely) must keep working unchanged."""
    business = DiscoveredBusiness(
        candidate=BusinessCandidate(name="Nike"),
        resolution=WebsiteResolution(website="https://nike.com", resolved_via="overpass", validated=True),
    )
    assert business.identity is None
    assert business.candidate.name == "Nike"


def test_identity_field_is_mutable_after_construction():
    business = _discovered_business(identity=None)
    business.identity = "anything"
    assert business.identity == "anything"


def test_discovered_business_still_has_all_original_fields():
    business = _discovered_business()
    assert business.is_duplicate is False
    assert business.duplicate_key is None
    assert business.rank_score == 0.0

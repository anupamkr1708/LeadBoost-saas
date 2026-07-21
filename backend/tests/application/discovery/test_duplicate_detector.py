from application.discovery.dto import BusinessCandidate, DiscoveredBusiness, WebsiteResolution
from application.discovery.duplicate_detector import DuplicateDetector, normalize_domain


def _business(name, website=None, phone=None, validated=True) -> DiscoveredBusiness:
    return DiscoveredBusiness(
        candidate=BusinessCandidate(name=name, phone=phone),
        resolution=WebsiteResolution(website=website, validated=validated, resolved_via="overpass" if website else "none"),
    )


def test_normalize_domain_strips_scheme_www_and_path():
    assert normalize_domain("https://www.nike.com/") == "nike.com"
    assert normalize_domain("http://nike.com/shoes") == "nike.com"
    assert normalize_domain(None) is None


def test_detects_duplicate_by_domain():
    detector = DuplicateDetector()
    businesses = [
        _business("Nike Store", website="https://www.nike.com"),
        _business("Nike Store Downtown", website="https://nike.com/downtown"),
    ]
    detector.dedup(businesses)

    assert businesses[0].is_duplicate is False
    assert businesses[1].is_duplicate is True


def test_detects_duplicate_by_name_and_phone_when_no_website():
    detector = DuplicateDetector()
    businesses = [
        _business("Acme Dental Clinic", phone="+91-20-1234-5678", website=None, validated=False),
        _business("Acme Dental Clinic", phone="912012345678", website=None, validated=False),
    ]
    detector.dedup(businesses)

    assert businesses[0].is_duplicate is False
    assert businesses[1].is_duplicate is True


def test_different_businesses_are_not_marked_duplicate():
    detector = DuplicateDetector()
    businesses = [
        _business("Nike Store", website="https://nike.com"),
        _business("Adidas Store", website="https://adidas.com"),
    ]
    detector.dedup(businesses)

    assert businesses[0].is_duplicate is False
    assert businesses[1].is_duplicate is False


def test_businesses_with_no_website_and_no_phone_are_never_flagged_duplicate():
    detector = DuplicateDetector()
    businesses = [
        _business("Mystery Business A", website=None, phone=None, validated=False),
        _business("Mystery Business A", website=None, phone=None, validated=False),
    ]
    detector.dedup(businesses)

    # No stable identifying key available -- can't safely dedupe, so
    # neither is flagged (avoids incorrectly discarding distinct listings).
    assert businesses[0].is_duplicate is False
    assert businesses[1].is_duplicate is False

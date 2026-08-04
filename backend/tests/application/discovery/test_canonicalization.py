"""
Unit tests for application.discovery.canonicalization (Sprint 3).

Everything in canonicalization.py is a pure string transformation (no
I/O), so these run against plain string inputs -- same approach
test_evidence.py/test_identity.py already use for their own pure layers.
"""

from application.discovery.canonicalization import (
    canonicalize,
    normalize_domain,
    registrable_domain,
    root_label,
    root_label_match,
    same_registrable_domain,
)


# -- normalize_domain ---------------------------------------------------------


def test_normalize_domain_strips_www():
    assert normalize_domain("https://www.nike.com/shoes") == "nike.com"


def test_normalize_domain_strips_default_https_port():
    assert normalize_domain("https://nike.com:443/") == "nike.com"


def test_normalize_domain_strips_default_http_port():
    assert normalize_domain("http://nike.com:80/") == "nike.com"


def test_normalize_domain_keeps_non_default_port():
    assert normalize_domain("http://nike.com:8080/") == "nike.com:8080"


def test_normalize_domain_lowercases():
    assert normalize_domain("https://NIKE.COM/") == "nike.com"


def test_normalize_domain_bare_domain_without_scheme():
    assert normalize_domain("nike.com") == "nike.com"


def test_normalize_domain_punycode_normalizes_idn():
    assert normalize_domain("café.fr") == normalize_domain("xn--caf-dma.fr")


def test_normalize_domain_empty_input_returns_empty_string():
    assert normalize_domain("") == ""
    assert normalize_domain(None) == ""


# -- registrable_domain / root_label -------------------------------------------


def test_registrable_domain_collapses_subdomains():
    assert registrable_domain("shop.company.com") == "company.com"
    assert registrable_domain("support.company.com") == "company.com"
    assert registrable_domain("www.company.com") == "company.com"
    assert registrable_domain("company.com") == "company.com"


def test_registrable_domain_handles_compound_cctld_suffix():
    assert registrable_domain("company.co.uk") == "company.co.uk"
    assert registrable_domain("shop.company.co.uk") == "company.co.uk"


def test_registrable_domain_handles_simple_tld():
    assert registrable_domain("company.ai") == "company.ai"
    assert registrable_domain("company.org") == "company.org"


def test_root_label_is_leftmost_label_of_registrable_domain():
    assert root_label("company.com") == "company"
    assert root_label("company.co.uk") == "company"
    assert root_label("shop.company.com") == "company"


# -- same_registrable_domain / root_label_match --------------------------------


def test_same_registrable_domain_true_for_subdomains():
    assert same_registrable_domain("shop.company.com", "support.company.com") is True
    assert same_registrable_domain("nike.com", "https://www.nike.com/about") is True


def test_same_registrable_domain_false_for_different_tlds():
    assert same_registrable_domain("company.com", "company.ai") is False


def test_root_label_match_true_across_different_suffixes():
    assert root_label_match("company.com", "company.co.uk") is True
    assert root_label_match("company.com", "company.ai") is True


def test_root_label_match_false_when_same_registrable_domain():
    # Already the same organization beyond doubt -- root_label_match is
    # reserved for the WEAKER cross-suffix signal only.
    assert root_label_match("shop.company.com", "company.com") is False


def test_root_label_match_false_for_unrelated_domains():
    assert root_label_match("nike.com", "adidas.com") is False


def test_root_label_match_false_for_short_coincidental_overlap():
    # Different root labels entirely -- no coincidental match.
    assert root_label_match("nike.com", "nikeshoes.net") is False


# -- canonicalize ---------------------------------------------------------------


def test_canonicalize_returns_none_for_empty_input():
    assert canonicalize("") is None
    assert canonicalize(None) is None


def test_canonicalize_populates_subdomain_when_present():
    result = canonicalize("shop.company.com")
    assert result.subdomain == "shop"
    assert result.registrable_domain == "company.com"


def test_canonicalize_subdomain_is_none_for_bare_registrable_domain():
    result = canonicalize("company.com")
    assert result.subdomain is None


def test_canonicalize_flags_idn_domains():
    result = canonicalize("café.fr")
    assert result.is_idn is True
    assert result.normalized_domain.startswith("xn--")


def test_canonicalize_does_not_flag_ascii_domains_as_idn():
    result = canonicalize("nike.com")
    assert result.is_idn is False


def test_canonicalize_is_idempotent():
    once = canonicalize("https://www.Company.COM:443/path")
    twice = canonicalize(once.normalized_domain)
    assert once.normalized_domain == twice.normalized_domain
    assert once.registrable_domain == twice.registrable_domain


def test_canonicalize_to_dict_has_expected_keys():
    result = canonicalize("shop.company.co.uk").to_dict()
    assert set(result.keys()) == {
        "original", "normalized_domain", "registrable_domain", "subdomain", "root_label", "suffix", "is_idn",
    }

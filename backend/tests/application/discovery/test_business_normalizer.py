from application.discovery.business_normalizer import normalize_overpass_element


def test_normalizes_a_full_node_element():
    element = {
        "type": "node",
        "lat": 19.0760,
        "lon": 72.8777,
        "tags": {
            "name": "Acme Shoes",
            "shop": "shoes",
            "phone": "+91 22 1234 5678",
            "website": "acmeshoes.example.com",
            "addr:housenumber": "12",
            "addr:street": "MG Road",
            "addr:city": "Mumbai",
        },
    }
    candidate = normalize_overpass_element(element, category="shoe stores")

    assert candidate is not None
    assert candidate.name == "Acme Shoes"
    assert candidate.category == "shoe stores"
    assert candidate.phone == "+91 22 1234 5678"
    assert candidate.website == "https://acmeshoes.example.com"
    assert candidate.address == "12, MG Road, Mumbai"
    assert candidate.latitude == 19.0760
    assert candidate.longitude == 72.8777
    assert candidate.source == "overpass"


def test_returns_none_for_unnamed_element():
    element = {"type": "node", "lat": 1.0, "lon": 2.0, "tags": {"shop": "shoes"}}
    assert normalize_overpass_element(element, category="shoe stores") is None


def test_returns_none_for_element_with_no_tags():
    element = {"type": "node", "lat": 1.0, "lon": 2.0}
    assert normalize_overpass_element(element, category="shoe stores") is None


def test_uses_center_coordinates_for_way_elements():
    element = {
        "type": "way",
        "center": {"lat": 18.5, "lon": 73.8},
        "tags": {"name": "Some Dentist Clinic"},
    }
    candidate = normalize_overpass_element(element, category="dentists")
    assert candidate.latitude == 18.5
    assert candidate.longitude == 73.8


def test_prefers_addr_full_when_present():
    element = {
        "tags": {
            "name": "Full Address Co",
            "addr:full": "1 Full Address Lane, Some City",
            "addr:street": "Should Not Be Used",
        }
    }
    candidate = normalize_overpass_element(element, category="hotels")
    assert candidate.address == "1 Full Address Lane, Some City"


def test_falls_back_to_contact_website_tag():
    element = {"tags": {"name": "Contact Tag Co", "contact:website": "example.org"}}
    candidate = normalize_overpass_element(element, category="hotels")
    assert candidate.website == "https://example.org"


def test_missing_optional_fields_are_none():
    element = {"tags": {"name": "Bare Business"}}
    candidate = normalize_overpass_element(element, category="hotels")
    assert candidate.phone is None
    assert candidate.website is None
    assert candidate.address is None

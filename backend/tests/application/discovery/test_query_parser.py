import pytest

from application.discovery.exceptions import QueryParseError
from application.discovery.query_parser import QueryParser


@pytest.fixture()
def parser():
    return QueryParser()


@pytest.mark.parametrize(
    "query,expected_category,expected_location,expected_limit,expected_modifier",
    [
        ("Top shoe stores in Mumbai", "shoe stores", "Mumbai", 20, "top"),
        ("Top 20 shoe stores in Mumbai", "shoe stores", "Mumbai", 20, "top"),
        ("Top 5 dentists in Pune", "dentists", "Pune", 5, "top"),
        ("Dentists in Pune", "Dentists", "Pune", 20, None),
        ("Hotels in Goa", "Hotels", "Goa", 20, None),
        ("Restaurants in Jaipur", "Restaurants", "Jaipur", 20, None),
        ("Real estate agencies in Bangalore", "Real estate agencies", "Bangalore", 20, None),
        ("Accounting firms in Chennai", "Accounting firms", "Chennai", 20, None),
    ],
)
def test_parse_examples(parser, query, expected_category, expected_location, expected_limit, expected_modifier):
    result = parser.parse(query)
    assert result.category == expected_category
    assert result.location == expected_location
    assert result.limit == expected_limit
    assert result.modifier == expected_modifier
    assert result.raw_query == query


def test_limit_override_wins_over_parsed_limit(parser):
    result = parser.parse("Top 20 shoe stores in Mumbai", limit_override=3)
    assert result.limit == 3


def test_limit_is_clamped_to_max(parser):
    result = parser.parse("Top 9999 shoe stores in Mumbai")
    assert result.limit <= 100


def test_limit_is_clamped_to_min(parser):
    result = parser.parse("Top 0 shoe stores in Mumbai")
    assert result.limit >= 1


def test_empty_query_raises():
    with pytest.raises(QueryParseError):
        QueryParser().parse("")


def test_whitespace_only_query_raises():
    with pytest.raises(QueryParseError):
        QueryParser().parse("   ")


def test_unparseable_query_raises():
    with pytest.raises(QueryParseError):
        QueryParser().parse("gibberish with no location marker")


def test_extra_whitespace_is_normalized(parser):
    result = parser.parse("  top   Bakeries   in   Delhi  ")
    assert result.category == "Bakeries"
    assert result.location == "Delhi"


def test_leading_article_is_stripped(parser):
    result = parser.parse("The dentists in Pune")
    assert result.category == "dentists"

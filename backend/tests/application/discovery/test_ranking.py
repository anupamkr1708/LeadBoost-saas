from application.discovery.dto import BusinessCandidate, DiscoveredBusiness, WebsiteResolution
from application.discovery.ranking import rank_businesses, score_business


def _business(name, category=None, address=None, website=None, validated=False, rating=None, review_count=None, phone=None):
    return DiscoveredBusiness(
        candidate=BusinessCandidate(
            name=name, category=category, address=address, phone=phone, rating=rating, review_count=review_count
        ),
        resolution=WebsiteResolution(website=website, validated=validated, resolved_via="overpass" if validated else "none"),
    )


def test_business_with_website_scores_higher_than_without():
    with_site = _business("A", website="https://a.com", validated=True)
    without_site = _business("B", website=None, validated=False)

    assert score_business(with_site, "shoe stores", "Mumbai") > score_business(without_site, "shoe stores", "Mumbai")


def test_category_match_increases_score():
    matching = _business("A", category="shoe stores")
    non_matching = _business("B", category="dentist")

    assert score_business(matching, "shoe stores", "Mumbai") > score_business(non_matching, "shoe stores", "Mumbai")


def test_location_match_increases_score():
    matching = _business("A", address="123 Main St, Mumbai")
    non_matching = _business("B", address="123 Main St, Delhi")

    assert score_business(matching, "shoe stores", "Mumbai") > score_business(non_matching, "shoe stores", "Mumbai")


def test_higher_rating_increases_score():
    high = _business("A", rating=4.8)
    low = _business("B", rating=2.0)

    assert score_business(high, "shoe stores", "Mumbai") > score_business(low, "shoe stores", "Mumbai")


def test_more_reviews_increases_score():
    many = _business("A", review_count=500)
    few = _business("B", review_count=1)

    assert score_business(many, "shoe stores", "Mumbai") > score_business(few, "shoe stores", "Mumbai")


def test_contact_completeness_increases_score():
    complete = _business("A", website="https://a.com", validated=True, phone="123", address="Somewhere")
    sparse = _business("B", website=None, validated=False)

    assert score_business(complete, "shoe stores", "Mumbai") > score_business(sparse, "shoe stores", "Mumbai")


def test_rank_businesses_sorts_best_first():
    strong = _business(
        "Strong Co", category="shoe stores", address="Mumbai", website="https://strong.com",
        validated=True, rating=4.9, review_count=1000, phone="123",
    )
    weak = _business("Weak Co", website=None, validated=False)

    ranked = rank_businesses([weak, strong], "shoe stores", "Mumbai")

    assert ranked[0].candidate.name == "Strong Co"
    assert ranked[0].rank_score > ranked[1].rank_score


def test_rank_businesses_sets_rank_score_on_every_item():
    businesses = [_business("A"), _business("B")]
    ranked = rank_businesses(businesses, "shoe stores", "Mumbai")
    assert all(b.rank_score >= 0 for b in ranked)

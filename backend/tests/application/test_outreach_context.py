"""L1: the business context handed to the Mailer comes only from real, existing fields."""

from types import SimpleNamespace

import pytest

from application.services.outreach_context import ABOUT_MAX_CHARS, build_recipient_facts, build_value_proposition


def _org(description):
    return SimpleNamespace(description=description)


def _lead(**over):
    base = dict(industry=None, about_text=None, founded_year=None, employees=None, revenue_band=None)
    base.update(over)
    return SimpleNamespace(**base)


class TestValueProposition:
    def test_comes_from_the_organization_description_normalized(self):
        assert build_value_proposition(_org("  We help teams\n ship   reliable software. ")) == \
            "We help teams ship reliable software."

    @pytest.mark.parametrize("raw", [None, "", "   ", "\n\t"])
    def test_unset_is_none(self, raw):
        assert build_value_proposition(_org(raw)) is None

    def test_missing_organization_is_none(self):
        assert build_value_proposition(None) is None

    @pytest.mark.parametrize("seeded", [
        "Organization for jane@example.com",
        "  Organization for jane.doe+test@sub.example.co.uk ",
    ])
    def test_the_registration_placeholder_is_not_a_value_proposition(self, seeded):
        assert build_value_proposition(_org(seeded)) is None

    @pytest.mark.parametrize("real", [
        "Organization for sales teams that need reliable tooling.",   # prose that merely starts the same way
        "We build Organization for Teams software",
    ])
    def test_real_descriptions_that_resemble_the_placeholder_are_kept(self, real):
        assert build_value_proposition(_org(real)) == real

    def test_icp_and_industry_are_never_used_as_the_offer(self):
        org = SimpleNamespace(description=None, icp_description="CTOs at 50-person SaaS", industry="Software")
        assert build_value_proposition(org) is None


class TestRecipientFacts:
    def test_only_real_lead_fields_in_a_stable_order(self):
        facts = build_recipient_facts(_lead(industry="Software", about_text="Builds tools.", founded_year=2015,
                                            employees="11-50", revenue_band="$1M-$10M"))
        assert facts == ["Industry: Software", "About: Builds tools.", "Founded: 2015",
                         "Employee band: 11-50, est. revenue $1M-$10M"]

    def test_missing_fields_are_skipped_not_invented(self):
        assert build_recipient_facts(_lead()) == []
        assert build_recipient_facts(_lead(employees="1-10")) == ["Employee band: 1-10"]
        assert build_recipient_facts(_lead(revenue_band="$1M")) == []     # revenue alone is not a fact we state

    def test_about_text_is_whitespace_normalized_and_capped(self):
        facts = build_recipient_facts(_lead(about_text="x " * 1000))
        assert len(facts) == 1 and len(facts[0]) <= ABOUT_MAX_CHARS and facts[0].endswith("...")

    def test_no_internal_intelligence_can_leak_in(self):
        lead = _lead(industry="Software", qualification_reasoning="SECRET-REASONING", qualification_score=97,
                     decision_confidence=0.91, outreach_message="legacy text")
        blob = " ".join(build_recipient_facts(lead))
        for banned in ("SECRET-REASONING", "97", "0.91", "legacy text"):
            assert banned not in blob

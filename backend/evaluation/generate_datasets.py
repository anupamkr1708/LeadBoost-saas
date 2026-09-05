"""
One-off dataset generator (not part of the shipped eval runner).

Builds evaluation/datasets/*.jsonl by constructing hand-designed,
synthetic (no real customer data) input contexts and capturing the REAL,
deterministic output of the actual production code (LeadScoringService,
CompanyIntelligenceAgent's heuristic path, DecisionAgent's rule-based
path, MessagingAgent's template path -- GROQ_API_KEY is unset, so every
one of these runs its real, deterministic fallback, not a mock) as the
golden baseline in expected_properties, via evaluation/runners.py -- the
exact same functions run_eval.py (the shipped harness) uses to re-check
against this baseline on every run. Using one shared module for both is
essential: if generation and evaluation constructed their inputs
differently, a passing eval run would prove nothing.

This is the standard "golden file" regression-testing technique, not a
shortcut: capturing today's verified-correct, deterministic output as
tomorrow's regression baseline is exactly what a regression dataset is
for. Run once to (re)generate the committed JSONL files.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.runners import run_company_intelligence, run_decision, run_messaging, run_qualification

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"


CI_SCENARIOS = [
    dict(case_id="ci_001", tags=["retail", "rich_evidence", "tier_1_city"],
         enriched_data={"technologies": ["Shopify", "Google Analytics"],
                         "business_type": {"value": "E-commerce", "confidence": 0.9, "source": "json_ld"},
                         "operating_regions": ["IN-KA"], "offerings": {"products": ["apparel"]},
                         "primary_contact": {"name": "Priya Sharma"}, "founded_year": 2018, "address": "Bengaluru, KA"},
         forbidden=["WordPress", "Magento", "employee count of 500"]),
    dict(case_id="ci_002", tags=["saas", "rich_evidence", "tier_1_city"],
         enriched_data={"technologies": ["React", "AWS", "Stripe"],
                         "business_type": {"value": "SaaS", "confidence": 0.95, "source": "json_ld"},
                         "operating_regions": ["IN-MH"], "offerings": {"services": ["subscription analytics"]},
                         "primary_contact": {"name": "Rahul Mehta"}, "founded_year": 2020, "address": "Mumbai, MH"},
         forbidden=["Shopify", "recently acquired", "funding round"]),
    dict(case_id="ci_003", tags=["healthcare", "sparse_evidence", "tier_2_city"],
         enriched_data={"technologies": [], "business_type": {"value": "Healthcare", "confidence": 0.6, "source": "meta_tag"}},
         forbidden=["Epic Systems", "HIPAA certified", "500+ employees"]),
    dict(case_id="ci_004", tags=["education", "sparse_evidence", "tier_2_city"],
         enriched_data={}, forbidden=["technology stack", "Moodle", "Canvas LMS"]),
    dict(case_id="ci_005", tags=["fintech", "rich_evidence", "tier_1_city"],
         enriched_data={"technologies": ["Razorpay", "Kubernetes"],
                         "business_type": {"value": "Fintech", "confidence": 0.85, "source": "json_ld"},
                         "operating_regions": ["IN-DL"], "offerings": {"services": ["payments"]},
                         "primary_contact": {"name": "Anjali Rao"}, "founded_year": 2019, "address": "New Delhi, DL"},
         forbidden=["RBI licensed", "unicorn", "IPO"]),
    dict(case_id="ci_006", tags=["manufacturing", "sparse_evidence", "tier_3_city"],
         enriched_data={"technologies": ["SAP"]}, forbidden=["ISO certified", "export revenue"]),
    dict(case_id="ci_007", tags=["hospitality", "no_evidence"],
         enriched_data={}, forbidden=["star rating", "chain hotel", "franchise"]),
    dict(case_id="ci_008", tags=["logistics", "moderate_evidence", "tier_2_city"],
         enriched_data={"technologies": ["Google Maps API"],
                         "business_type": {"value": "Logistics", "confidence": 0.7, "source": "description"},
                         "founded_year": 2015},
         forbidden=["fleet size", "warehouse count"]),
    dict(case_id="ci_009", tags=["retail", "moderate_evidence", "tier_2_city"],
         enriched_data={"technologies": ["WooCommerce"],
                         "business_type": {"value": "Retail", "confidence": 0.75, "source": "json_ld"},
                         "address": "Pune, MH"},
         forbidden=["annual revenue", "store count"]),
    dict(case_id="ci_010", tags=["saas", "sparse_evidence", "tier_1_city"],
         enriched_data={"technologies": ["Django"]},
         forbidden=["Series A", "monthly recurring revenue", "customer count"]),
    dict(case_id="ci_011", tags=["consulting", "moderate_evidence", "tier_1_city"],
         enriched_data={"business_type": {"value": "Consulting", "confidence": 0.8, "source": "meta_tag"},
                         "primary_contact": {"name": "Vikram Nair"}, "founded_year": 2012},
         forbidden=["Big 4", "Fortune 500 clients"]),
    dict(case_id="ci_012", tags=["manufacturing", "rich_evidence", "tier_2_city"],
         enriched_data={"technologies": ["Salesforce", "SAP", "Oracle"],
                         "business_type": {"value": "Manufacturing", "confidence": 0.88, "source": "json_ld"},
                         "operating_regions": ["IN-GJ"], "offerings": {"products": ["auto parts"]},
                         "primary_contact": {"name": "Suresh Patel"}, "founded_year": 2005, "address": "Ahmedabad, GJ"},
         forbidden=["export to Europe", "patent portfolio"]),
    dict(case_id="ci_013", tags=["education", "moderate_evidence", "tier_3_city"],
         enriched_data={"technologies": ["Zoom"],
                         "business_type": {"value": "Education", "confidence": 0.65, "source": "description"},
                         "founded_year": 2017},
         forbidden=["accreditation", "student count"]),
    dict(case_id="ci_014", tags=["healthcare", "rich_evidence", "tier_1_city"],
         enriched_data={"technologies": ["Practo integration"],
                         "business_type": {"value": "Healthcare", "confidence": 0.9, "source": "json_ld"},
                         "operating_regions": ["IN-KA"], "offerings": {"services": ["diagnostics"]},
                         "primary_contact": {"name": "Dr. Meera Iyer"}, "founded_year": 2016, "address": "Bengaluru, KA"},
         forbidden=["NABL accredited", "bed count"]),
    dict(case_id="ci_015", tags=["fintech", "sparse_evidence", "tier_2_city"],
         enriched_data={"technologies": ["Plaid"]},
         forbidden=["assets under management", "loan book size"]),
]


def build_company_intelligence_cases():
    cases = []
    for s in CI_SCENARIOS:
        input_context = {
            "lead_id": 1, "organization_id": 1, "website": "https://example-co.example.com",
            "enriched_data": s.get("enriched_data", {}), "scraped_data": s.get("scraped_data", {}),
        }
        output = run_company_intelligence(input_context)
        cases.append({
            "case_id": s["case_id"], "agent": "company_intelligence", "input_context": input_context,
            "evidence": output["evidence"],
            "expected_properties": {
                "source": "heuristic",
                "technology_signals": output["technology_signals"],
                "website_quality": output["website_quality"],
                "icp_alignment_score": output["icp_alignment_score"],
                "confidence_min": 0.0,
            },
            "must_not_claim": s["forbidden"], "tags": s["tags"],
        })
    return cases


Q_SCENARIOS = [
    dict(case_id="q_001", tags=["hot", "rich_evidence"],
         lead=dict(employees="51-200", email_confidence=1.0, scrape_confidence=1.0,
                   enrichment_confidence=1.0, linkedin_url="https://linkedin.com/company/x"), icp=1.0),
    dict(case_id="q_002", tags=["hot", "rich_evidence"],
         lead=dict(employees="11-50", email_confidence=0.9, scrape_confidence=0.95,
                   enrichment_confidence=0.9, linkedin_url="https://linkedin.com/company/y"), icp=1.0),
    dict(case_id="q_003", tags=["warm", "moderate_evidence"],
         lead=dict(employees="51-200", email_confidence=1.0, scrape_confidence=0.5,
                   enrichment_confidence=1.0, linkedin_url=None), icp=1.0),
    dict(case_id="q_004", tags=["warm", "moderate_evidence"],
         lead=dict(employees="201-500", email_confidence=0.8, scrape_confidence=0.7,
                   enrichment_confidence=0.0, linkedin_url="https://linkedin.com/company/z"), icp=0.71),
    dict(case_id="q_005", tags=["cold", "sparse_evidence"],
         lead=dict(employees="1-10", email_confidence=0.0, phone="+911234567890",
                   scrape_confidence=0.65, enrichment_confidence=0.65, linkedin_url=None), icp=0.43),
    dict(case_id="q_006", tags=["cold", "sparse_evidence"],
         lead=dict(employees="500+", email_confidence=0.0, scrape_confidence=0.61,
                   enrichment_confidence=0.0, linkedin_url=None), icp=0.29),
    dict(case_id="q_007", tags=["disqualified", "no_evidence"], lead=dict(), icp=0.0),
    dict(case_id="q_008", tags=["disqualified", "sparse_evidence"],
         lead=dict(employees=None, email_confidence=0.0, scrape_confidence=0.0,
                   enrichment_confidence=0.0, linkedin_url=None), icp=0.14),
    dict(case_id="q_009", tags=["hot", "rich_evidence"],
         lead=dict(employees="201-500", email_confidence=1.0, scrape_confidence=1.0,
                   enrichment_confidence=1.0, linkedin_url="https://linkedin.com/company/a"), icp=0.86),
    dict(case_id="q_010", tags=["warm", "moderate_evidence"],
         lead=dict(employees="1-10", email_confidence=0.9, scrape_confidence=0.9,
                   enrichment_confidence=0.9, linkedin_url="https://linkedin.com/company/b"), icp=0.71),
    dict(case_id="q_011", tags=["cold", "moderate_evidence"],
         lead=dict(employees="500+", email_confidence=0.0, phone="+911234567890",
                   scrape_confidence=0.7, enrichment_confidence=0.7, linkedin_url=None), icp=0.29),
    dict(case_id="q_012", tags=["disqualified", "sparse_evidence"],
         lead=dict(employees="1-10", email_confidence=0.0, scrape_confidence=0.0,
                   enrichment_confidence=0.0, linkedin_url=None), icp=0.0),
    dict(case_id="q_013", tags=["warm", "rich_evidence"],
         lead=dict(employees="11-50", email_confidence=0.7, scrape_confidence=0.7,
                   enrichment_confidence=0.65, linkedin_url="https://linkedin.com/company/c"), icp=0.71),
    dict(case_id="q_014", tags=["hot", "rich_evidence"],
         lead=dict(employees="51-200", email_confidence=0.95, scrape_confidence=0.9,
                   enrichment_confidence=0.95, linkedin_url="https://linkedin.com/company/d"), icp=1.0),
    dict(case_id="q_015", tags=["cold", "sparse_evidence"],
         lead=dict(employees="1-10", email_confidence=0.0, scrape_confidence=0.6,
                   enrichment_confidence=0.6, linkedin_url=None), icp=0.29),
]


def build_qualification_cases():
    cases = []
    for s in Q_SCENARIOS:
        input_context = {"lead": dict(s["lead"]), "icp_alignment_score": s["icp"]}
        output = run_qualification(input_context)
        cases.append({
            "case_id": s["case_id"], "agent": "qualification", "input_context": input_context,
            "evidence": [],
            "expected_properties": {
                "qualification_label": output["qualification_label"],
                "total_score_min": max(0.0, output["total_score"] - 0.5),
                "total_score_max": output["total_score"] + 0.5,
            },
            "must_not_claim": [], "tags": s["tags"],
        })
    return cases


D_SCENARIOS = [
    dict(case_id="d_001", tags=["hot", "aligned"], score=88.0, qualification_label="Hot Lead",
         scrape_confidence=0.9, enrichment_confidence=0.9, icp=0.9),
    dict(case_id="d_002", tags=["hot", "aligned"], score=95.0, qualification_label="Hot Lead",
         scrape_confidence=1.0, enrichment_confidence=1.0, icp=1.0),
    dict(case_id="d_003", tags=["warm", "aligned"], score=70.0, qualification_label="Warm Lead",
         scrape_confidence=0.7, enrichment_confidence=0.6, icp=0.6),
    dict(case_id="d_004", tags=["warm", "aligned"], score=65.0, qualification_label="Warm Lead",
         scrape_confidence=0.65, enrichment_confidence=0.5, icp=0.5),
    dict(case_id="d_005", tags=["cold", "aligned"], score=45.0, qualification_label="Cold Lead",
         scrape_confidence=0.5, enrichment_confidence=0.4, icp=0.3),
    dict(case_id="d_006", tags=["cold", "aligned"], score=50.0, qualification_label="Cold Lead",
         scrape_confidence=0.4, enrichment_confidence=0.4, icp=0.2),
    dict(case_id="d_007", tags=["disqualified", "aligned"], score=10.0, qualification_label="Disqualified",
         scrape_confidence=0.1, enrichment_confidence=0.1, icp=0.0),
    dict(case_id="d_008", tags=["disqualified", "aligned"], score=0.0, qualification_label="Disqualified",
         scrape_confidence=0.0, enrichment_confidence=0.0, icp=0.0),
    dict(case_id="d_009", tags=["hot", "high_confidence"], score=90.0, qualification_label="Hot Lead",
         scrape_confidence=0.95, enrichment_confidence=0.9, icp=0.85),
    dict(case_id="d_010", tags=["warm", "moderate_confidence"], score=68.0, qualification_label="Warm Lead",
         scrape_confidence=0.6, enrichment_confidence=0.55, icp=0.55),
    dict(case_id="d_011", tags=["cold", "low_confidence"], score=42.0, qualification_label="Cold Lead",
         scrape_confidence=0.3, enrichment_confidence=0.3, icp=0.25),
    dict(case_id="d_012", tags=["disqualified", "low_confidence"], score=15.0, qualification_label="Disqualified",
         scrape_confidence=0.15, enrichment_confidence=0.1, icp=0.05),
    dict(case_id="d_013", tags=["hot", "aligned"], score=100.0, qualification_label="Hot Lead",
         scrape_confidence=1.0, enrichment_confidence=1.0, icp=1.0),
    dict(case_id="d_014", tags=["warm", "aligned"], score=75.0, qualification_label="Warm Lead",
         scrape_confidence=0.75, enrichment_confidence=0.7, icp=0.65),
    dict(case_id="d_015", tags=["cold", "aligned"], score=55.0, qualification_label="Cold Lead",
         scrape_confidence=0.55, enrichment_confidence=0.5, icp=0.35),
]


def build_decision_cases():
    cases = []
    for s in D_SCENARIOS:
        input_context = {
            "score": s["score"], "qualification_label": s["qualification_label"],
            "scrape_confidence": s["scrape_confidence"], "enrichment_confidence": s["enrichment_confidence"],
            "icp_alignment_score": s["icp"],
        }
        output = run_decision(input_context)
        cases.append({
            "case_id": s["case_id"], "agent": "decision", "input_context": input_context,
            "evidence": output["evidence"],
            "expected_properties": {
                "qualification": output["qualification"],
                "source": "rule_based",
                "recommended_action": output["recommended_action"],
            },
            "must_not_claim": ["guaranteed conversion", "verbal commitment", "signed contract"],
            "tags": s["tags"],
        })
    return cases


M_SCENARIOS = [
    dict(case_id="m_001", tags=["software", "has_contact"], company_name="Acme Robotics", industry="software", contact_name="Priya"),
    dict(case_id="m_002", tags=["consulting", "has_contact"], company_name="Nair Advisory", industry="consulting", contact_name="Vikram"),
    dict(case_id="m_003", tags=["ecommerce", "has_contact"], company_name="ShopKart", industry="ecommerce", contact_name="Anjali"),
    dict(case_id="m_004", tags=["software", "no_contact"], company_name="Beta Systems", industry="software", contact_name=None),
    dict(case_id="m_005", tags=["unknown_industry", "has_contact"], company_name="Delta Traders", industry=None, contact_name="Suresh"),
    dict(case_id="m_006", tags=["unknown_industry", "no_contact"], company_name=None, industry=None, contact_name=None),
    dict(case_id="m_007", tags=["consulting", "no_contact"], company_name="Iyer Consulting", industry="consulting", contact_name=None),
    dict(case_id="m_008", tags=["ecommerce", "no_contact"], company_name="QuickCart", industry="ecommerce", contact_name=None),
    dict(case_id="m_009", tags=["software", "has_contact"], company_name="Gamma Cloud", industry="software", contact_name="Meera"),
    dict(case_id="m_010", tags=["unknown_industry", "has_contact"], company_name="Patel Traders", industry="retail", contact_name="Suresh"),
    dict(case_id="m_011", tags=["consulting", "has_contact"], company_name="Rao Partners", industry="consulting", contact_name="Rao"),
    dict(case_id="m_012", tags=["ecommerce", "has_contact"], company_name="Fresh Basket", industry="ecommerce", contact_name="Kavya"),
    dict(case_id="m_013", tags=["software", "no_contact"], company_name="Zeta Labs", industry="software", contact_name=None),
    dict(case_id="m_014", tags=["unknown_industry", "no_contact"], company_name="Sharma Traders", industry="manufacturing", contact_name=None),
    dict(case_id="m_015", tags=["consulting", "no_contact"], company_name="Mehta Group", industry="consulting", contact_name=None),
]


def build_messaging_cases():
    cases = []
    for s in M_SCENARIOS:
        input_context = {"company_name": s["company_name"], "industry": s["industry"], "contact_name": s["contact_name"]}
        output = run_messaging(input_context)
        cases.append({
            "case_id": s["case_id"], "agent": "messaging", "input_context": input_context,
            "evidence": [],
            "expected_properties": {"source": "template", "message_nonempty": True},
            "must_not_claim": ["discount code", "limited time offer", "click here now", "wire transfer"],
            "tags": s["tags"],
        })
    return cases


def main():
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    datasets = {
        "company_intelligence.jsonl": build_company_intelligence_cases(),
        "qualification.jsonl": build_qualification_cases(),
        "decision.jsonl": build_decision_cases(),
        "messaging.jsonl": build_messaging_cases(),
    }
    total = 0
    for filename, cases in datasets.items():
        path = DATASETS_DIR / filename
        with open(path, "w") as f:
            for case in cases:
                f.write(json.dumps(case) + "\n")
        print(f"Wrote {len(cases)} cases to {path}")
        total += len(cases)
    print(f"Total: {total} cases")


if __name__ == "__main__":
    main()

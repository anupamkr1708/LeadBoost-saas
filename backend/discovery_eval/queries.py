"""
Discovery Evaluation Benchmark — Query Dataset.

~100 deterministic, real-world business-discovery queries used as the
permanent regression benchmark for the Discovery pipeline. Fixed order,
fixed IDs -- re-running the benchmark always evaluates the exact same
queries in the exact same order, so two runs are comparable.

Every query string is designed to be parseable by
`application.discovery.query_parser.QueryParser` (see that module's own
docstring for the exact patterns it accepts: "<category> in <location>",
"top N <category> in <location>", "<category> <location>" when
<location> is in the KNOWN_LOCATIONS gazetteer, "... for <purpose>"
trailing qualifiers, etc.). A query that fails to parse is not a bug in
this dataset -- QueryParser rejecting a genuinely malformed query is
itself a valid, recorded outcome (see metrics.QueryRecord.error) and
part of what this benchmark measures.

`domain` and `difficulty` are metadata for THIS evaluation framework's
own reporting/grouping only -- they are never passed to Discovery, which
only ever sees `query` (a plain string), exactly like a real user's
search box input.

difficulty values:
  standard        -- a plain "<category> in <city>" query, low ambiguity
  ambiguous_name   -- a short/common business name with no strong,
                      unique brand signal (e.g. "Regal", "Classic
                      Furniture") -- the hard cases the brief calls out
  alias_city        -- uses a colloquial/former city name
                      (Bombay/Madras/Bangalore/Gurgaon/...) instead of
                      the current official name
  misspelling        -- a plausible real-world typo in the category term
  no_preposition      -- relies on QueryParser's gazetteer-assisted split
                      rather than an "in"/"near"/"around" preposition
  purpose_qualifier    -- has a trailing "... for <purpose>" clause
  qualified_count      -- uses a "top N" / "best N" / bare "N" prefix
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class QueryCase:
    id: str
    query: str
    domain: str
    difficulty: str = "standard"


BENCHMARK_QUERIES = [
    # ---------------------------------------------------------------
    # Retail
    # ---------------------------------------------------------------
    QueryCase("retail-01", "shoe stores in Mumbai", "Retail"),
    QueryCase("retail-02", "sports shops in Delhi", "Retail"),
    QueryCase("retail-03", "electronics stores in Bengaluru", "Retail"),
    QueryCase("retail-04", "bookstores in Pune", "Retail"),
    QueryCase("retail-05", "furniture stores in Hyderabad", "Retail"),
    QueryCase("retail-06", "top 10 mobile phone shops in Chennai", "Retail", "qualified_count"),

    # ---------------------------------------------------------------
    # Healthcare
    # ---------------------------------------------------------------
    QueryCase("health-01", "eye hospitals in Patna", "Healthcare"),
    QueryCase("health-02", "dentists in Jaipur", "Healthcare"),
    QueryCase("health-03", "cardiology hospitals in Chennai", "Healthcare"),
    QueryCase("health-04", "physiotherapy clinics in Kochi", "Healthcare"),
    QueryCase("health-05", "diagnostic centres in Lucknow", "Healthcare"),
    QueryCase("health-06", "hospitals in Patna for eye treatment", "Healthcare", "purpose_qualifier"),

    # ---------------------------------------------------------------
    # Food
    # ---------------------------------------------------------------
    QueryCase("food-01", "restaurants in Indore", "Food"),
    QueryCase("food-02", "cafes in Gurgaon", "Food", "alias_city"),
    QueryCase("food-03", "pizza places in Noida", "Food"),
    QueryCase("food-04", "bakeries in Surat", "Food"),
    QueryCase("food-05", "ice cream shops in Ahmedabad", "Food"),
    QueryCase("food-06", "best 15 restaurants near Mumbai Airport", "Food", "qualified_count"),

    # ---------------------------------------------------------------
    # Education
    # ---------------------------------------------------------------
    QueryCase("edu-01", "schools in Mysore", "Education", "alias_city"),
    QueryCase("edu-02", "engineering colleges in Bhopal", "Education"),
    QueryCase("edu-03", "coaching institutes in Kota", "Education"),
    QueryCase("edu-04", "music schools in Kolkata", "Education"),
    QueryCase("edu-05", "language institutes in Delhi", "Education"),
    QueryCase("edu-06", "green valley school Dehradun", "Education", "ambiguous_name"),

    # ---------------------------------------------------------------
    # Technology
    # ---------------------------------------------------------------
    QueryCase("tech-01", "software companies in Bengaluru", "Technology"),
    QueryCase("tech-02", "AI startups in Hyderabad", "Technology"),
    QueryCase("tech-03", "cybersecurity companies in Pune", "Technology"),
    QueryCase("tech-04", "IT consulting firms in Chennai", "Technology"),
    QueryCase("tech-05", "cloud consulting companies in Gurugram", "Technology"),
    QueryCase("tech-06", "pioneer technologies Hyderabad", "Technology", "ambiguous_name"),

    # ---------------------------------------------------------------
    # Professional Services
    # ---------------------------------------------------------------
    QueryCase("prof-01", "chartered accountants in Mumbai", "Professional Services"),
    QueryCase("prof-02", "law firms in Delhi", "Professional Services"),
    QueryCase("prof-03", "architects in Ahmedabad", "Professional Services"),
    QueryCase("prof-04", "digital marketing agencies in Bengaluru", "Professional Services"),
    QueryCase("prof-05", "recruitment agencies in Pune", "Professional Services"),
    QueryCase("prof-06", "united traders Delhi", "Professional Services", "ambiguous_name"),

    # ---------------------------------------------------------------
    # Real Estate
    # ---------------------------------------------------------------
    QueryCase("re-01", "real estate developers in Noida", "Real Estate"),
    QueryCase("re-02", "property consultants in Gurgaon", "Real Estate", "alias_city"),
    QueryCase("re-03", "construction companies in Kochi", "Real Estate"),
    QueryCase("re-04", "interior designers in Jaipur", "Real Estate"),
    QueryCase("re-05", "coworking spaces in Hyderabad", "Real Estate"),

    # ---------------------------------------------------------------
    # Manufacturing / Industrial
    # ---------------------------------------------------------------
    QueryCase("mfg-01", "textile manufacturers in Surat", "Manufacturing"),
    QueryCase("mfg-02", "automobile suppliers in Pune", "Manufacturing"),
    QueryCase("mfg-03", "steel manufacturers in Jamshedpur", "Manufacturing"),
    QueryCase("mfg-04", "chemical companies in Vadodara", "Manufacturing"),
    QueryCase("mfg-05", "plastic manufacturers in Rajkot", "Manufacturing"),
    QueryCase("mfg-06", "industrial equipment suppliers in Faridabad", "Manufacturing"),

    # ---------------------------------------------------------------
    # Hospitality / Travel
    # ---------------------------------------------------------------
    QueryCase("hosp-01", "hotels in Goa", "Hospitality"),
    QueryCase("hosp-02", "resorts in Udaipur", "Hospitality"),
    QueryCase("hosp-03", "travel agencies in Delhi", "Hospitality"),
    QueryCase("hosp-04", "tour operators in Leh", "Hospitality"),
    QueryCase("hosp-05", "homestays in Coorg", "Hospitality"),
    QueryCase("hosp-06", "top 5 hotels in Goa", "Hospitality", "qualified_count"),

    # ---------------------------------------------------------------
    # Government
    # ---------------------------------------------------------------
    QueryCase("gov-01", "passport seva kendra in Lucknow", "Government"),
    QueryCase("gov-02", "municipal corporation offices in Nagpur", "Government"),
    QueryCase("gov-03", "regional transport office in Bhopal", "Government"),
    QueryCase("gov-04", "district collector office in Patna", "Government"),

    # ---------------------------------------------------------------
    # Finance
    # ---------------------------------------------------------------
    QueryCase("fin-01", "banks in Chandigarh", "Finance"),
    QueryCase("fin-02", "insurance agents in Indore", "Finance"),
    QueryCase("fin-03", "investment advisors in Mumbai", "Finance"),
    QueryCase("fin-04", "microfinance companies in Ranchi", "Finance"),

    # ---------------------------------------------------------------
    # NGOs
    # ---------------------------------------------------------------
    QueryCase("ngo-01", "NGOs in Bhubaneswar", "NGO"),
    QueryCase("ngo-02", "child welfare organizations in Kolkata", "NGO"),
    QueryCase("ngo-03", "environmental NGOs in Dehradun", "NGO"),
    QueryCase("ngo-04", "old age homes in Coimbatore", "NGO"),

    # ---------------------------------------------------------------
    # Franchises / Large Brands / Multi-location
    # ---------------------------------------------------------------
    QueryCase("brand-01", "McDonald's outlets in Mumbai", "Franchise/Brand"),
    QueryCase("brand-02", "Domino's Pizza in Bengaluru", "Franchise/Brand"),
    QueryCase("brand-03", "Reliance Trends stores in Pune", "Franchise/Brand"),
    QueryCase("brand-04", "Cafe Coffee Day outlets in Hyderabad", "Franchise/Brand"),
    QueryCase("brand-05", "Big Bazaar stores in Ahmedabad", "Franchise/Brand"),

    # ---------------------------------------------------------------
    # Small local businesses / single-word / generic names
    # ---------------------------------------------------------------
    QueryCase("local-01", "Sharma general store Kanpur", "Small Local", "ambiguous_name"),
    QueryCase("local-02", "Sunrise bakery Nashik", "Small Local", "ambiguous_name"),
    QueryCase("local-03", "Prime cuts butcher Jodhpur", "Small Local", "ambiguous_name"),
    QueryCase("local-04", "Om electricals Amritsar", "Small Local", "ambiguous_name"),
    QueryCase("local-05", "City Xerox Aligarh", "Small Local", "ambiguous_name"),

    # ---------------------------------------------------------------
    # Difficult / Ambiguous business names (from the original brief)
    # ---------------------------------------------------------------
    QueryCase("amb-01", "regal shoes Mumbai", "Ambiguous", "ambiguous_name"),
    QueryCase("amb-02", "apollo Chennai", "Ambiguous", "ambiguous_name"),
    QueryCase("amb-03", "woodland Bangalore", "Ambiguous", "alias_city"),
    QueryCase("amb-04", "classic furniture Jaipur", "Ambiguous", "ambiguous_name"),
    QueryCase("amb-05", "national hospital Patna", "Ambiguous", "ambiguous_name"),
    QueryCase("amb-06", "lotus clinic Lucknow", "Ambiguous", "ambiguous_name"),
    QueryCase("amb-07", "modern bakery Pune", "Ambiguous", "ambiguous_name"),
    QueryCase("amb-08", "star traders Rajkot", "Ambiguous", "ambiguous_name"),
    QueryCase("amb-09", "royal enterprises Surat", "Ambiguous", "ambiguous_name"),

    # ---------------------------------------------------------------
    # Old / colloquial city names (query_parser + locations alias table)
    # ---------------------------------------------------------------
    QueryCase("alias-01", "software companies in Bombay", "Technology", "alias_city"),
    QueryCase("alias-02", "hospitals in Madras", "Healthcare", "alias_city"),
    QueryCase("alias-03", "restaurants in Calcutta", "Food", "alias_city"),
    QueryCase("alias-04", "hotels in Poona", "Hospitality", "alias_city"),
    QueryCase("alias-05", "clinics in Trivandrum", "Healthcare", "alias_city"),
    QueryCase("alias-06", "textile mills in Baroda", "Manufacturing", "alias_city"),
    QueryCase("alias-07", "colleges in Allahabad", "Education", "alias_city"),
    QueryCase("alias-08", "resorts in Pondicherry", "Hospitality", "alias_city"),

    # ---------------------------------------------------------------
    # Misspellings (plausible real-world typos)
    # ---------------------------------------------------------------
    QueryCase("mis-01", "restarants in Indore", "Food", "misspelling"),
    QueryCase("mis-02", "phsyiotherapy clinics in Kochi", "Healthcare", "misspelling"),
    QueryCase("mis-03", "electroncis stores in Bengaluru", "Retail", "misspelling"),
    QueryCase("mis-04", "chartered accountnats in Mumbai", "Professional Services", "misspelling"),
    QueryCase("mis-05", "enginering colleges in Bhopal", "Education", "misspelling"),

    # ---------------------------------------------------------------
    # No-preposition (gazetteer split) phrasing
    # ---------------------------------------------------------------
    QueryCase("nopre-01", "Noida software companies", "Technology", "no_preposition"),
    QueryCase("nopre-02", "Dental clinics Bangalore", "Healthcare", "no_preposition"),
    QueryCase("nopre-03", "Real estate agents Kolkata", "Real Estate", "no_preposition"),
    QueryCase("nopre-04", "Warangal steel traders", "Manufacturing", "no_preposition"),

    # ---------------------------------------------------------------
    # Preposition variants (near / around / close to / located in)
    # ---------------------------------------------------------------
    QueryCase("prep-01", "gyms near Delhi", "Small Local", "standard"),
    QueryCase("prep-02", "coffee shops around Pune", "Food", "standard"),
    QueryCase("prep-03", "law firms located in Chennai", "Professional Services", "standard"),
    QueryCase("prep-04", "pharmacies close to Lucknow", "Healthcare", "standard"),

    # ---------------------------------------------------------------
    # Filler-prefixed conversational phrasing
    # ---------------------------------------------------------------
    QueryCase("filler-01", "I need software companies near Bangalore", "Technology", "alias_city"),
    QueryCase("filler-02", "Find me the best 10 gyms in Delhi", "Small Local", "qualified_count"),
    QueryCase("filler-03", "Show me diagnostic centres in Lucknow", "Healthcare", "standard"),

    # ---------------------------------------------------------------
    # Multi-word category, single-word/short brand names
    # ---------------------------------------------------------------
    QueryCase("word-01", "Wipro Bengaluru", "Technology", "ambiguous_name"),
    QueryCase("word-02", "Infosys Pune", "Technology", "ambiguous_name"),
    QueryCase("word-03", "Titan showroom Chennai", "Retail", "ambiguous_name"),
    QueryCase("word-04", "Bata Kolkata", "Retail", "ambiguous_name"),
    QueryCase("word-05", "small business consulting services in Rajkot", "Professional Services", "standard"),
]

# Deterministic ordering, deterministic IDs -- never shuffled, never
# sampled. Assert once, at import time, that IDs are unique so a future
# edit to this file can't silently create two records that collide in
# query_results.csv.
_ids = [c.id for c in BENCHMARK_QUERIES]
assert len(_ids) == len(set(_ids)), "Duplicate QueryCase id in BENCHMARK_QUERIES"

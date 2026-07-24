"""
Known-location gazetteer.

A small, fixed lookup of major Indian (and a handful of major global)
cities, used for two distinct purposes:

  1. query_parser.py: disambiguating natural-language queries that have
     no preposition at all (e.g. "Software companies Noida" /
     "Noida software companies") -- a deterministic dictionary lookup,
     not an LLM or trained NER model.
  2. providers/overpass_provider.py: retrying with the current official
     OSM area name when a common colloquial/former city name (e.g.
     "Bangalore") returns zero results because OpenStreetMap tags the
     area under its official name ("Bengaluru").

Deliberately NOT exhaustive -- this is a pragmatic, maintainable list
covering cities that come up in real usage, not a full gazetteer service.
Extending it is a one-line dict/set addition, no new dependency.
"""

# Colloquial/former name (lowercase) -> current official OSM area name.
LOCATION_ALIASES = {
    "bangalore": "Bengaluru",
    "bombay": "Mumbai",
    "calcutta": "Kolkata",
    "madras": "Chennai",
    "poona": "Pune",
    "cochin": "Kochi",
    "trivandrum": "Thiruvananthapuram",
    "mysore": "Mysuru",
    "gurgaon": "Gurugram",
    "baroda": "Vadodara",
    "allahabad": "Prayagraj",
    "pondicherry": "Puducherry",
}

# Common trailing landmark/qualifier words that aren't themselves part of
# an OSM area name (e.g. "Mumbai Airport" -> try "Mumbai"). Stripped one
# at a time, longest-first, as a location-normalization retry tier.
_LANDMARK_SUFFIXES = (
    "international airport",
    "airport",
    "railway station",
    "station",
    "downtown",
    "city centre",
    "city center",
)


def strip_landmark_suffix(location: str) -> "str | None":
    """Returns `location` with a trailing landmark word/phrase removed, or
    None if no known suffix was present. Used as a location-normalization
    retry tier, not a general geocoder."""
    lowered = location.lower().strip()
    for suffix in _LANDMARK_SUFFIXES:
        if lowered.endswith(suffix) and lowered != suffix:
            stripped = location[: -(len(suffix))].strip()
            if stripped:
                return stripped
    return None


# Recognized as valid discovery locations by the query parser's
# no-preposition fallback split. Includes every LOCATION_ALIASES key/value
# plus other major cities that regularly show up in queries.
KNOWN_LOCATIONS = {
    "mumbai", "bengaluru", "bangalore", "delhi", "new delhi", "kolkata", "calcutta",
    "chennai", "madras", "hyderabad", "pune", "poona", "ahmedabad", "jaipur", "surat",
    "lucknow", "kanpur", "nagpur", "indore", "thane", "bhopal", "visakhapatnam",
    "patna", "vadodara", "baroda", "ghaziabad", "ludhiana", "agra", "nashik",
    "faridabad", "meerut", "rajkot", "kalyan", "vasai", "varanasi", "srinagar",
    "aurangabad", "dhanbad", "amritsar", "navi mumbai", "allahabad", "prayagraj",
    "ranchi", "howrah", "coimbatore", "jabalpur", "gwalior", "vijayawada", "jodhpur",
    "madurai", "raipur", "kota", "guwahati", "chandigarh", "thiruvananthapuram",
    "trivandrum", "solapur", "hubli", "mysuru", "mysore", "tiruchirappalli",
    "bareilly", "aligarh", "moradabad", "gurugram", "gurgaon", "noida",
    "jalandhar", "bhubaneswar", "salem", "warangal", "dehradun", "kochi", "cochin",
    "puducherry", "pondicherry", "goa", "panaji",
    # A handful of major global cities in case Discovery is used outside India.
    "new york", "london", "san francisco", "austin", "singapore", "dubai", "toronto",
}
KNOWN_LOCATIONS |= set(LOCATION_ALIASES.keys()) | {v.lower() for v in LOCATION_ALIASES.values()}
"""Deterministic field normalization layer for scraped company data.

Why this module exists
-----------------------
scraper.py is a tiered extractor: it pulls candidate values for a field out
of many independent sources (schema.org/JSON-LD, OpenGraph, Twitter cards,
plain HTML, footer text, contact pages...) and merges them opportunistically
as it goes. That's the right design for an extractor -- but it means the
raw output can still contain near-duplicate emails/phones, an unstructured
address blob, and a same-vs-different ambiguity between a brand name and
its full legal name (e.g. "Metro Brands" vs. "Metro Brands Ltd" -- related,
but NOT interchangeable: overwriting one with the other loses information,
and treating two similarly-named-but-different companies, e.g. a store's
own brand vs. its parent's legal name, as the same entity is a correctness
bug, not a formatting one).

This module is the single place that turns that raw, multi-sourced output
into clean, canonical, deduplicated fields, plus a handful of general,
schema-driven signals (what kind of organization this is, where it
operates, what it offers) that the enrichment layer needs but shouldn't
have to re-derive from raw text itself. It is intentionally:

- A pure function library. No network calls, no LLM calls, no state.
  Everything here is unit-testable with a plain dict in, dict out.
- General-purpose. Every rule is based on the SHAPE of the data (a
  schema.org key-naming convention, a URL path pattern, a digit-count
  pattern) rather than a hardcoded company name, domain, industry, or
  country. A new company, TLD, country, or business category needs zero
  code changes here.
- Strictly additive to its caller. `normalize_scraped_fields()` returns a
  new dict meant to be merged in under its own namespace (e.g.
  `data["normalized"] = normalize_scraped_fields(data)`) -- it never
  deletes or renames a field the scraper already produced.
- Silent about business meaning. This module resolves which of several
  raw signals to trust (a JSON-LD address beats a footer-text guess); it
  never decides what *kind* of business those signals describe -- that
  line is drawn at `organization_type`, which relays the site's own
  schema.org self-declaration rather than inferring a category. Actual
  interpretation (industry framing, capability summaries, and so on) is
  the enrichment layer's job, operating only on what this module returns.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

_EMAIL_SHAPE_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Generic obfuscation shape: "<local> (at|[at]|AT|at) <domain> (dot|[dot]|DOT|dot) <domain>...".
# This is a structural pattern (any local-part, any domain), not a list of
# specific known domains -- it recovers an obfuscated address from any site
# using this common low-tech anti-scraper trick, e.g. "jane (at) example
# (dot) com" or "sales [at] some-shop [dot] co [dot] uk".
_AT_TOKEN = r"(?:\(\s*at\s*\)|\[\s*at\s*\]|\bat\b)"
_DOT_TOKEN = r"(?:\(\s*dot\s*\)|\[\s*dot\s*\]|\bdot\b)"
_OBFUSCATED_EMAIL_RE = re.compile(
    rf"([a-zA-Z0-9._%+\-]+)\s*{_AT_TOKEN}\s*"
    rf"([a-zA-Z0-9\-]+(?:\s*{_DOT_TOKEN}\s*[a-zA-Z0-9\-]+)+)",
    re.IGNORECASE,
)
_DOT_TOKEN_RE = re.compile(_DOT_TOKEN, re.IGNORECASE)


def normalize_email(raw: Optional[str]) -> Optional[str]:
    """Lowercase + validate shape. Returns None if raw isn't email-shaped."""
    if not raw:
        return None
    candidate = raw.strip().strip(".,;:").lower()
    return candidate if _EMAIL_SHAPE_RE.match(candidate) else None


def deobfuscate_emails(text: str) -> List[str]:
    """Recovers "name (at) domain (dot) com"-style addresses from free text.

    Structural, not a lookup table: matches on the at/dot *pattern*, so it
    works for any local-part/domain/TLD combination a site happens to use.
    """
    found: List[str] = []
    if not text:
        return found
    for m in _OBFUSCATED_EMAIL_RE.finditer(text):
        local = m.group(1)
        domain_part = _DOT_TOKEN_RE.sub(".", m.group(2))
        domain_part = re.sub(r"\s+", "", domain_part)
        candidate = normalize_email(f"{local}@{domain_part}")
        if candidate:
            found.append(candidate)
    return found


def dedupe_emails(emails: List[str]) -> List[str]:
    """Case-insensitive de-dup that preserves first-seen order."""
    seen: Set[str] = set()
    out: List[str] = []
    for raw in emails:
        normalized = normalize_email(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


# ---------------------------------------------------------------------------
# Phones
# ---------------------------------------------------------------------------

# General international phone shape: an optional leading "+", 7-15 digits
# in total (the ITU E.164 range, not a per-country rule), with any mix of
# spaces/dashes/dots/parentheses as separators. Deliberately does not
# hardcode a country's specific grouping (e.g. US 3-3-4) -- that would
# silently reject the majority of the world's phone formats.
_PHONE_CANDIDATE_RE = re.compile(r"(?<!\d)(\+?\d[\d\-.\s()]{5,17}\d)(?!\d)")
_REPEATED_DIGIT_RUN_RE = re.compile(r"(\d)\1{4,}")  # same digit 5+ times in a row


def _digits_only(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """General E.164-shaped normalization: keep a leading '+' (or convert a
    leading international-dialing '00' to '+'), strip everything else to
    digits. No per-country area-code/grouping logic -- that would need a
    hardcoded country table; this only standardizes representation so the
    same number written two different ways compares equal.

    Also rejects two shapes that are almost never a real phone number and
    are cheap to rule out generically on any site: a bare "digits.digits"
    decimal (a version number or ratio the source text-scan matched), and
    a run of five or more identical digits.
    """
    if not raw:
        return None
    text = raw.strip()
    if re.fullmatch(r"\d+\.\d+", text):
        return None
    has_plus = text.startswith("+")
    digits = _digits_only(text)
    if has_plus and digits.startswith("00"):
        digits = digits.lstrip("0") or digits  # rare malformed "+00..."
    elif not has_plus and digits.startswith("00") and len(digits) > 10:
        digits = digits[2:]
        has_plus = True
    if not (7 <= len(digits) <= 15):
        return None
    if _REPEATED_DIGIT_RUN_RE.search(digits):
        return None
    return f"+{digits}" if has_plus else digits


def extract_all_phones(text: str) -> List[str]:
    """Finds every phone-shaped substring in free text (not just the
    first), generically, across any international format."""
    if not text:
        return []
    out = []
    for m in _PHONE_CANDIDATE_RE.finditer(text):
        normalized = normalize_phone(m.group(1))
        if normalized:
            out.append(normalized)
    return out


def _strip_trunk_prefix(digits: str) -> str:
    """Many countries' numbering plans use a leading '0' for domestic/trunk
    dialing that is dropped when the same number is written in full
    international form with a country code (e.g. "07977 311647" <->
    "+44 7977 311647"). Applied uniformly regardless of which country code
    is involved -- not a per-country lookup table."""
    return digits[1:] if digits.startswith("0") and len(digits) > 8 else digits


def dedupe_phones(phones: List[str]) -> List[str]:
    """De-dupes by normalized-digit form so '+91 797 731 1647' and
    '0797-7311647' (missing country code) collapse together when one is a
    suffix of the other, keeping the more complete (longer / with country
    code) representation as canonical."""
    normalized = [normalize_phone(p) for p in phones]
    normalized = [p for p in normalized if p]
    normalized.sort(key=len, reverse=True)  # fuller form first
    kept: List[str] = []
    for candidate in normalized:
        c_core = _strip_trunk_prefix(candidate.lstrip("+"))
        if any(
            _strip_trunk_prefix(k.lstrip("+")).endswith(c_core)
            or c_core.endswith(_strip_trunk_prefix(k.lstrip("+")))
            for k in kept
        ):
            continue
        kept.append(candidate)
    return kept


# ---------------------------------------------------------------------------
# Address -- assembled generically from whatever address_*-shaped keys are
# present, regardless of exactly which prefix the source JSON-LD block used
# (schema.org allows PostalAddress to be nested under many different parent
# properties: address, location.address, etc; scraper.py's flattener turns
# all of these into <prefix>_streetAddress, <prefix>_addressLocality, ...).
# ---------------------------------------------------------------------------

_ADDRESS_COMPONENT_SUFFIXES: Dict[str, Tuple[str, ...]] = {
    "street": ("streetaddress", "street_address", "street"),
    "city": ("addresslocality", "address_locality", "city", "locality"),
    "region": ("addressregion", "address_region", "region", "state"),
    "postal_code": ("postalcode", "postal_code", "zip", "zipcode"),
    "country": ("addresscountry", "address_country", "country"),
}


def _clean_text(value: Any) -> Optional[str]:
    """Decodes HTML entities (e.g. an address authored/escaped in a CMS as
    '...LBS Marg &amp; CST Road...') and strips whitespace. Generic: applied
    to any scraped string on its way into a normalized field, regardless of
    which field it is."""
    if not isinstance(value, str):
        return None
    cleaned = html.unescape(value).strip()
    return cleaned or None


def _find_component(raw: Dict[str, Any], suffixes: Tuple[str, ...]) -> Optional[str]:
    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip():
            continue
        key_lower = key.lower().replace("-", "")
        if any(key_lower == s or key_lower.endswith("_" + s) or key_lower.endswith(s) for s in suffixes):
            return _clean_text(value)
    return None


_LOCATION_BLOCK_PREFIX_RE = re.compile(r"^location_(\d+)_")


def _group_address_keys_by_block(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Groups raw's flattened keys into address 'blocks' so components are
    only ever combined with other components from the SAME underlying
    schema.org PostalAddress -- never mixed across two different offices.
    A site with a `location[]` array of many offices flattens to
    `location_0_address_streetAddress`, `location_1_address_addressRegion`,
    etc.; searching for each component independently across the whole
    flattened dict (as a single global scan would) can pick components from
    different indices for the same "address" -- e.g. street/city/country
    from location_0 but region from location_1, producing an address that
    describes no real place. Returns candidate blocks in priority order:
    the organization's own top-level/unindexed address first (if any), then
    each location_N_ block in source order. Purely structural -- works for
    any site's flattened JSON-LD, nothing site-specific."""
    top_level: Dict[str, Any] = {}
    indexed: Dict[int, Dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip():
            continue
        m = _LOCATION_BLOCK_PREFIX_RE.match(key)
        if m:
            indexed.setdefault(int(m.group(1)), {})[key[m.end():]] = value
        else:
            top_level[key] = value
    blocks = [top_level] if top_level else []
    blocks.extend(indexed[idx] for idx in sorted(indexed))
    return blocks


def normalize_address(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Builds a structured {street, city, region, postal_code, country,
    formatted} address from the flattened address_*-shaped keys in `raw`
    (the whole scraped-data dict, not just an address sub-object -- the
    component keys can be top-level after flattening). Components are
    gathered one block at a time (see `_group_address_keys_by_block`) so a
    multi-location site's offices never get cross-contaminated with each
    other. Returns None if no component is found anywhere."""
    components: Dict[str, Any] = {}
    for block in _group_address_keys_by_block(raw):
        components = {
            name: _find_component(block, suffixes) for name, suffixes in _ADDRESS_COMPONENT_SUFFIXES.items()
        }
        if any(components.values()):
            break
    if not any(components.values()):
        existing = raw.get("address")
        if isinstance(existing, str) and existing.strip():
            return {"formatted": _clean_text(existing)}
        if isinstance(existing, dict):
            components = {
                name: _find_component(existing, suffixes)
                for name, suffixes in _ADDRESS_COMPONENT_SUFFIXES.items()
            }
    if not any(components.values()):
        return None
    formatted = ", ".join(
        str(components[part])
        for part in ("street", "city", "region", "postal_code", "country")
        if components.get(part)
    )
    components["formatted"] = formatted
    return components


# ---------------------------------------------------------------------------
# Organization type -- relays the site's OWN structured self-declaration of
# what kind of organization it is, rather than guessing a category from
# page-text keywords. This is the piece that lets the enrichment layer stay
# fully general: schema.org's Organization/LocalBusiness vocabulary already
# has a specific type for a hospital, a restaurant, a hotel, an NGO, a law
# firm, a software product, a real-estate agency, and so on, and gets more
# specific over time as schema.org evolves -- so there is no hardcoded
# per-industry table to maintain here, ever.
# ---------------------------------------------------------------------------

# Schema.org types that describe something OTHER than the organization
# itself (a product, a person, a place entry inside the org's own location
# list, a page, an article, ...) and must never be reported as the
# business's own category, even though a block of one of these types can
# coincidentally carry an address/telephone (e.g. a Person bio, or a Place
# inside `location[]`). Deliberately short: this is an EXCLUDE-list for the
# few types that commonly collide with organization-shaped data, not an
# ALLOW-list of every organization subtype (an allow-list doesn't scale --
# see the module docstring).
_NON_ORG_JSONLD_TYPES = {
    "product", "softwareapplication", "webapplication", "mobileapplication",
    "person", "place", "postaladdress", "contactpoint", "offer", "offercatalog",
    "faqpage", "question", "answer", "breadcrumblist", "itemlist",
    "website", "webpage", "article", "newsarticle", "blogposting",
    "review", "aggregaterating", "event", "imageobject", "videoobject",
    "searchaction", "service",
}

# Organization types too generic to be informative as a *category* on their
# own (virtually every company's schema declares "Organization" somewhere).
# Still valid evidence that a block IS the organization -- just not worth
# surfacing over a more specific subtype declared elsewhere in the same
# document.
_GENERIC_ORG_TYPES = {"organization", "localbusiness", "corporation"}


def _humanize_schema_type(type_name: str) -> str:
    """"OnlineStore" -> "Online Store", "MedicalBusiness" -> "Medical
    Business", "NGO" -> "NGO". Pure string-shape transform (PascalCase ->
    words), not a lookup table, so it works for any current or future
    schema.org type name with zero maintenance."""
    if not type_name:
        return type_name
    if type_name.isupper():
        return type_name  # acronym-like type (e.g. "NGO"); leave as-is
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", type_name)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return spaced


def extract_organization_type(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Surfaces the site's own declared organization type from schema.org
    JSON-LD -- e.g. "Hospital", "Restaurant", "Software Application",
    "Legal Service", "Real Estate Agent". A more specific subtype is
    preferred over a generic one ("Organization") when a document declares
    both across different blocks.

    Returns None when no organization-shaped JSON-LD block is present at
    all. That's intentional: this function reports what the site itself
    declared and nothing more. Guessing a category from ambiguous text
    when the site made no structured declaration is exactly the brittle
    behavior this replaces -- leave that gap for a human reviewer or an
    LLM fallback tier to resolve, not a keyword table.
    """
    blocks = raw.get("jsonld")
    if not isinstance(blocks, list):
        return None

    specific_types: List[str] = []
    generic_seen = False

    for block in blocks:
        if not isinstance(block, dict):
            continue
        raw_type = block.get("@type")
        if isinstance(raw_type, list):
            type_names = [str(t) for t in raw_type]
        elif isinstance(raw_type, str):
            type_names = [raw_type]
        else:
            continue
        types_lower = {t.lower() for t in type_names}
        if types_lower & _NON_ORG_JSONLD_TYPES:
            continue
        for name in type_names:
            if name.lower() in _GENERIC_ORG_TYPES:
                generic_seen = True
            else:
                specific_types.append(name)

    if specific_types:
        seen: Set[str] = set()
        deduped: List[str] = []
        for t in specific_types:
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(t)
        primary = deduped[0]
        return {
            "value": _humanize_schema_type(primary),
            "schema_type": primary,
            "alternate_types": [_humanize_schema_type(t) for t in deduped[1:]],
            "confidence": 0.9,
            "source": "schema.org (self-declared)",
        }

    if generic_seen:
        return {
            "value": "Organization",
            "schema_type": "Organization",
            "alternate_types": [],
            "confidence": 0.4,
            "source": "schema.org (generic; no specific subtype declared)",
        }

    return None


# ---------------------------------------------------------------------------
# Operating regions -- pure aggregation of location evidence the scraper
# already found (top-level address plus any Organization.location[]
# entries); no new inference, works identically for a single-location shop
# or a 30-office multinational.
# ---------------------------------------------------------------------------

def extract_operating_regions(raw: Dict[str, Any]) -> List[str]:
    regions: List[str] = []
    seen: Set[str] = set()

    def _add(country: Any, region: Any) -> None:
        label = ", ".join(str(p) for p in (region, country) if p)
        if label and label.lower() not in seen:
            seen.add(label.lower())
            regions.append(label)

    _add(raw.get("country"), raw.get("state"))
    for loc in raw.get("locations") or []:
        if isinstance(loc, dict):
            _add(loc.get("country"), loc.get("state"))

    return regions


# ---------------------------------------------------------------------------
# Products & services -- schema-shape-driven, so a hospital's list of
# services is picked up by the exact same code path as a SaaS product
# catalog or a restaurant's menu offerings. `products` mostly relays
# scraper.py's own Product/SoftwareApplication extraction; Service-typed
# blocks and named offers (makesOffer / hasOfferCatalog) are not already
# surfaced by the scraper, so they're pulled out here.
# ---------------------------------------------------------------------------

def _offer_item_names(offer: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    item = offer.get("itemOffered")
    if isinstance(item, dict) and item.get("name"):
        names.append(str(item["name"]))
    elif offer.get("name"):
        names.append(str(offer["name"]))
    for entry in offer.get("itemListElement") or []:
        if not isinstance(entry, dict):
            continue
        nested = entry.get("itemOffered") or entry
        if isinstance(nested, dict) and nested.get("name"):
            names.append(str(nested["name"]))
    return names


def _dedupe_strings(items: List[str], limit: int = 20) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
        if len(out) >= limit:
            break
    return out


def extract_products_and_services(raw: Dict[str, Any]) -> Dict[str, List[str]]:
    products = list(raw.get("products") or [])
    services: List[str] = []

    for block in raw.get("jsonld") or []:
        if not isinstance(block, dict):
            continue
        raw_type = block.get("@type")
        if isinstance(raw_type, list):
            types_lower = {str(t).lower() for t in raw_type}
        elif isinstance(raw_type, str):
            types_lower = {raw_type.lower()}
        else:
            types_lower = set()

        if "service" in types_lower and block.get("name"):
            services.append(str(block["name"]))

        for offer_key in ("makesOffer", "hasOfferCatalog"):
            offers = block.get(offer_key)
            if not offers:
                continue
            offer_list = offers if isinstance(offers, list) else [offers]
            for offer in offer_list:
                if isinstance(offer, dict):
                    services.extend(_offer_item_names(offer))

    return {
        "products": _dedupe_strings(products),
        "services": _dedupe_strings(services),
    }


# ---------------------------------------------------------------------------
# Company naming -- brand vs. legal name, kept distinct rather than merged
# ---------------------------------------------------------------------------

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|llp|plc|corp|corporation|"
    r"co|company|gmbh|sarl|srl|s\.a|bv|nv|pty|pvt|private)\.?\b",
    re.IGNORECASE,
)


def has_legal_suffix(name: Optional[str]) -> bool:
    return bool(name and _LEGAL_SUFFIX_RE.search(name))


def split_brand_and_legal_name(
    company_name: Optional[str], legal_name: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """Returns (brand_name, legal_name) without ever silently overwriting a
    shorter brand name with a longer legal one or vice-versa.

    General rule (no hardcoded suffix-to-brand mapping): if one string is
    the other with a legal-entity suffix appended (e.g. "Acme" / "Acme
    Ltd"), they're the same organization -- keep the shorter as the brand
    name and the longer as the legal name. If neither contains the other,
    they are NOT assumed to be the same entity (e.g. a subsidiary's own
    brand vs. its parent's legal name) -- both are preserved as given,
    un-merged, so a downstream consumer can't accidentally conflate two
    distinct organizations.
    """
    if not company_name and not legal_name:
        return None, None
    if not legal_name:
        return company_name, (company_name if has_legal_suffix(company_name) else None)
    if not company_name:
        return (legal_name if not has_legal_suffix(legal_name) else None), legal_name

    a = company_name.strip()
    b = legal_name.strip()
    if a.lower() == b.lower():
        return a, b
    a_stripped = _LEGAL_SUFFIX_RE.sub("", a).strip(" ,.")
    if a_stripped and (a_stripped.lower() == b.lower() or b.lower().startswith(a_stripped.lower())):
        return a_stripped, b
    # Genuinely different strings with no containment relationship: don't
    # guess which one is "more correct". Surface both, unmodified.
    return a, b


# ---------------------------------------------------------------------------
# Social profiles -- filter out share-widget links that aren't the
# company's own profile (a common raw-scrape false positive: a "Share on
# Facebook" button on literally any page links to facebook.com, but isn't
# the company's Facebook page).
# ---------------------------------------------------------------------------

_SHARE_WIDGET_PATH_MARKERS = (
    "sharer", "share.php", "intent/tweet", "share?", "sharearticle",
    "pin/create", "pinit", "/share/", "shareoffer",
)


def is_share_widget_url(url: Optional[str]) -> bool:
    """General structural check (URL path shape), not a per-platform
    lookup table: share-intent links follow a small set of well-known path
    conventions across platforms regardless of which company's page they
    point away from."""
    if not url:
        return False
    lowered = url.lower()
    return any(marker in lowered for marker in _SHARE_WIDGET_PATH_MARKERS)


def filter_social_profiles(profiles: Dict[str, Any]) -> Dict[str, str]:
    """Drops share-widget URLs and empty values. Each known platform field
    is already a unique dict key by construction, so there's nothing to
    de-duplicate across fields -- only to filter."""
    return {name: url for name, url in profiles.items() if url and not is_share_widget_url(url)}


# ---------------------------------------------------------------------------
# Technologies
# ---------------------------------------------------------------------------

def dedupe_technologies(technologies: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for tech in technologies or []:
        key = (tech or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(tech.strip())
    return out


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

@dataclass
class NormalizedFields:
    brand_name: Optional[str] = None
    legal_name: Optional[str] = None
    organization_type: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    founded_year: Optional[int] = None
    employee_count: Optional[str] = None
    emails: List[Dict[str, Any]] = field(default_factory=list)
    phones: List[str] = field(default_factory=list)
    address: Optional[Dict[str, Any]] = None
    operating_regions: List[str] = field(default_factory=list)
    products: List[str] = field(default_factory=list)
    services: List[str] = field(default_factory=list)
    social_profiles: Dict[str, str] = field(default_factory=dict)
    technologies: List[str] = field(default_factory=list)
    text_excerpt: Optional[str] = None
    field_confidence: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "brand_name": self.brand_name,
            "legal_name": self.legal_name,
            "organization_type": self.organization_type,
            "description": self.description,
            "founded_year": self.founded_year,
            "employee_count": self.employee_count,
            "emails": self.emails,
            "phones": self.phones,
            "address": self.address,
            "operating_regions": self.operating_regions,
            "products": self.products,
            "services": self.services,
            "social_profiles": self.social_profiles,
            "technologies": self.technologies,
            "text_excerpt": self.text_excerpt,
            "field_confidence": self.field_confidence,
        }


# Category fields already produced by scraper.py's `_EMAIL_PREFIX_CATEGORIES`
# / `_classify_contact_point`, listed here only as known field names so
# already-categorized emails can be labeled without re-deriving the
# category logic that lives in scraper.py.
_KNOWN_EMAIL_CATEGORY_FIELDS = (
    "sales_email", "support_email", "press_email", "privacy_email",
    "careers_email", "billing_email", "contact_email",
)
_KNOWN_SOCIAL_FIELDS = (
    "linkedin_url", "twitter_url", "facebook_url", "instagram_url",
    "youtube_url", "github_url", "crunchbase_url", "glassdoor_url",
    "pinterest_url", "tiktok_url",
)


def normalize_scraped_fields(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Single entry point: takes the scraper's full raw output dict and
    returns a new, canonical/deduplicated view of it. Additive only -- the
    caller should merge this in under its own key (e.g.
    `data["normalized"] = normalize_scraped_fields(data)`); nothing here
    mutates or removes a field from `raw`."""
    if not raw:
        return NormalizedFields().to_dict()

    incoming_confidence = dict(raw.get("field_confidence") or {})

    brand_name, legal_name = split_brand_and_legal_name(
        _clean_text(raw.get("company_name") or raw.get("org_name")),
        _clean_text(raw.get("legal_name")),
    )

    emails: List[Dict[str, Any]] = []
    generic_email = normalize_email(raw.get("email"))
    if generic_email:
        emails.append({
            "email": generic_email,
            "category": "general",
            "confidence": incoming_confidence.get("email", 0.7),
        })
    for field_name in _KNOWN_EMAIL_CATEGORY_FIELDS:
        normalized = normalize_email(raw.get(field_name))
        if normalized:
            emails.append({
                "email": normalized,
                "category": field_name.replace("_email", ""),
                "confidence": 0.75,
            })
    # Recover anything obfuscated in the visible text content that the
    # plain-text/mailto extraction wouldn't have caught.
    for deobfuscated in deobfuscate_emails(str(raw.get("text_content") or "")):
        if deobfuscated not in {e["email"] for e in emails}:
            emails.append({"email": deobfuscated, "category": "general", "confidence": 0.5})

    seen_emails: Set[str] = set()
    deduped_emails: List[Dict[str, Any]] = []
    for entry in emails:
        if entry["email"] in seen_emails:
            continue
        seen_emails.add(entry["email"])
        deduped_emails.append(entry)

    phone_candidates: List[str] = []
    for key in ("phone", "sales_phone", "support_phone", "press_phone", "fax"):
        if raw.get(key):
            phone_candidates.append(str(raw[key]))
    phone_candidates.extend(extract_all_phones(str(raw.get("text_content") or "")[:20000]))
    phones = dedupe_phones(phone_candidates)

    address = normalize_address(raw)
    organization_type = extract_organization_type(raw)
    operating_regions = extract_operating_regions(raw)
    products_and_services = extract_products_and_services(raw)

    description = _clean_text(raw.get("description") or raw.get("website_description"))
    founded_year = raw.get("founded_year") or raw.get("founding_year")
    employee_count = raw.get("employee_count")
    text_excerpt = None if raw.get("text_content_thin") else (str(raw.get("text_content") or "")[:5000] or None)

    social_raw = {k: raw.get(k) for k in _KNOWN_SOCIAL_FIELDS if raw.get(k)}
    social_profiles = filter_social_profiles(social_raw)

    technologies = dedupe_technologies(raw.get("technologies") or [])

    field_confidence: Dict[str, float] = dict(incoming_confidence)
    if brand_name:
        field_confidence["brand_name"] = incoming_confidence.get("company_name", 0.6)
    if legal_name:
        field_confidence["legal_name"] = incoming_confidence.get("legal_name", 0.9)
    if organization_type:
        field_confidence["organization_type"] = organization_type["confidence"]
    if address:
        field_confidence.setdefault("address", incoming_confidence.get("address", 0.5))

    return NormalizedFields(
        brand_name=brand_name,
        legal_name=legal_name,
        organization_type=organization_type,
        description=description,
        founded_year=founded_year,
        employee_count=employee_count,
        emails=deduped_emails,
        phones=phones,
        address=address,
        operating_regions=operating_regions,
        products=products_and_services["products"],
        services=products_and_services["services"],
        social_profiles=social_profiles,
        technologies=technologies,
        text_excerpt=text_excerpt,
        field_confidence=field_confidence,
    ).to_dict()
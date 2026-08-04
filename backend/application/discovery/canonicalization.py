"""
Domain Canonicalization (Sprint 3).

A single, reusable place to answer "are these two domain strings actually
the same website, or the same organization's infrastructure, or just
coincidentally similar-looking text?" -- the structural groundwork every
other Sprint 3 module (organization.py, competition.py, false_positive.py)
builds on, per the brief's "Build reusable canonicalization."

What this module normalizes, per the brief:
  - www                    -- `normalize_domain` strips a leading "www."
  - default ports           -- `normalize_domain` strips ":80"/":443"
  - trailing slash           -- `normalize_domain` operates on the netloc
                                 only, so a trailing path slash never
                                 leaks in to begin with
  - punycode                 -- `normalize_domain` always returns the
                                 ASCII/punycode form via the stdlib "idna"
                                 codec, so "café.fr" and "xn--caf-dma.fr"
                                 canonicalize identically
  - redirect targets          -- callers compare `canonicalize(original)`
                                 against `canonicalize(final_url)`, e.g.
                                 false_positive.redirect_domain_mismatch_signal
  - subdomains                 -- `CanonicalDomain.subdomain` /
                                 `registrable_domain` split the
                                 subdomain from the registrable (eTLD+1)
                                 domain
  - country domains            -- `_COMPOUND_SUFFIXES` below lets
                                 `registrable_domain` compute eTLD+1
                                 correctly for two-label ccTLD suffixes
                                 (co.uk, com.au, ...) instead of naively
                                 assuming the last two labels are always
                                 the registrable domain
  - duplicate representations  -- `normalize_domain` is the single
                                 function every module in this package
                                 uses to decide "is this the same string",
                                 so two differently-cased/ported/prefixed
                                 spellings of one domain always collapse
                                 to one canonical key

Never hardcodes a company name: `_COMPOUND_SUFFIXES` is generic ccTLD
*structure* knowledge (which two-label suffixes are public suffixes at
all, e.g. "co.uk"), not a list of specific businesses or domains -- the
same category of static reference data as an HTTP status-code table or a
MIME-type table, and entirely distinct in kind from
website_validator.REJECTED_DOMAINS (a curated list of specific companies'
domains to reject). This table works identically for any domain under
any of these suffixes; it is never consulted to single out one business.

Deterministic and pure: every function below is a plain string
transformation with no I/O, no randomness, and no dependency on anything
that isn't already sitting in the string passed in -- cheap enough to
call on every candidate in a pool without a caching layer, and safe to
call repeatedly (idempotent: `canonicalize(canonicalize(x).normalized_domain)
== canonicalize(x)`).
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

# Generic, structural ccTLD knowledge -- see module docstring for why this
# is not the same kind of thing as website_validator.REJECTED_DOMAINS.
# Deliberately NOT exhaustive (matching locations.py's own "pragmatic, not
# a full gazetteer" stance) -- covers the compound suffixes that come up
# in real usage; extending it is a one-line addition, no new dependency.
_COMPOUND_SUFFIXES = frozenset(
    {
        "co.uk", "org.uk", "ac.uk", "gov.uk", "net.uk", "sch.uk", "ltd.uk", "plc.uk",
        "co.in", "net.in", "org.in", "gen.in", "firm.in", "ind.in", "ac.in", "gov.in", "res.in",
        "com.au", "net.au", "org.au", "edu.au", "gov.au", "id.au",
        "co.nz", "net.nz", "org.nz", "govt.nz",
        "co.za", "org.za", "net.za", "gov.za", "web.za",
        "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp", "gr.jp",
        "com.br", "net.br", "org.br", "gov.br",
        "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn",
        "com.sg", "net.sg", "org.sg", "edu.sg", "gov.sg",
        "co.id", "or.id", "web.id", "ac.id", "go.id",
        "co.kr", "or.kr", "ne.kr", "go.kr",
        "com.mx", "org.mx", "net.mx", "gob.mx",
        "com.hk", "org.hk", "net.hk", "edu.hk", "gov.hk",
        "com.tw", "org.tw", "net.tw", "gov.tw", "edu.tw",
        "co.il", "org.il", "net.il", "gov.il",
        "com.ar", "org.ar", "net.ar", "gob.ar",
        "com.pk", "org.pk", "net.pk", "gov.pk", "edu.pk",
        "com.bd", "net.bd", "org.bd", "gov.bd",
        "com.my", "org.my", "net.my", "gov.my", "edu.my",
        "co.th", "or.th", "in.th", "ac.th", "go.th",
        "com.ph", "org.ph", "net.ph", "gov.ph", "edu.ph",
        "com.vn", "org.vn", "net.vn", "gov.vn", "edu.vn",
        "com.tr", "org.tr", "net.tr", "gov.tr", "edu.tr",
        "com.sa", "org.sa", "net.sa", "gov.sa", "edu.sa",
        "com.ng", "org.ng", "net.ng", "gov.ng", "edu.ng",
        "co.ke", "or.ke", "go.ke", "ac.ke",
        "com.eg", "org.eg", "net.eg", "gov.eg", "edu.eg",
    }
)

_DEFAULT_PORTS = {"80", "443"}


def _strip_default_port(netloc: str) -> str:
    if ":" not in netloc:
        return netloc
    host, _, port = netloc.rpartition(":")
    return host if port in _DEFAULT_PORTS else netloc


def _to_ascii(domain: str) -> str:
    """Punycode-normalizes `domain` via the stdlib "idna" codec. Falls
    back to the input unchanged (rather than raising) for strings the
    codec can't handle -- e.g. an already-ASCII domain occasionally trips
    IDNA2003's length/label rules, and a malformed candidate URL should
    degrade gracefully here rather than blow up canonicalization for the
    whole pool."""
    if not domain:
        return domain
    try:
        if domain.isascii():
            return domain.lower()
        return domain.encode("idna").decode("ascii").lower()
    except Exception:
        return domain.lower()


def normalize_domain(url_or_domain: str) -> str:
    """www / default-port / case / punycode normalization -- deliberately
    does NOT split off the registrable domain (see `registrable_domain`
    for that); this is the "same duplicate representation" collapse the
    brief asks for, independent of the eTLD+1 question."""
    if not url_or_domain:
        return ""
    text = url_or_domain.strip()
    if "://" not in text:
        text = f"//{text}"
    try:
        netloc = urlparse(text).netloc.lower()
    except Exception:
        return ""
    netloc = _strip_default_port(netloc)
    if netloc.startswith("www."):
        netloc = netloc[4:]
    netloc = netloc.rstrip(".")
    return _to_ascii(netloc)


def _suffix_and_registrable(domain: str) -> "tuple[str, str]":
    labels = [l for l in domain.split(".") if l]
    if not labels:
        return "", ""
    if len(labels) >= 3 and ".".join(labels[-2:]) in _COMPOUND_SUFFIXES:
        return ".".join(labels[-2:]), ".".join(labels[-3:])
    if len(labels) >= 2:
        return labels[-1], ".".join(labels[-2:])
    return "", labels[0]


def registrable_domain(url_or_domain: str) -> str:
    """The eTLD+1 -- the domain's "logical owner" unit, ignoring any
    deeper subdomain labels. `shop.company.com` / `support.company.com` /
    `www.company.com` / `company.com` all reduce to `company.com`;
    `company.co.uk` reduces to `company.co.uk` (not the naive last-two
    -labels `co.uk`), thanks to `_COMPOUND_SUFFIXES`."""
    normalized = normalize_domain(url_or_domain) if "://" in (url_or_domain or "") or "/" in (url_or_domain or "") else _to_ascii((url_or_domain or "").strip().rstrip("."))
    _, registrable = _suffix_and_registrable(normalized)
    return registrable


def root_label(url_or_domain: str) -> str:
    """The registrable domain's own leftmost label -- e.g. "company" out
    of both "company.com" and "company.co.uk". Used ONLY as a weak,
    cross-suffix signal (see organization.py's Tier-2 relationship
    resolution) -- never as a basis for a hard identity merge, since two
    unrelated businesses can share a common short root label by pure
    coincidence."""
    registrable = registrable_domain(url_or_domain)
    return registrable.split(".")[0] if registrable else ""


@dataclass(frozen=True)
class CanonicalDomain:
    """The complete, structural canonicalization of one domain/URL.
    Every field is a deterministic string transformation of `original` --
    nothing here is fetched, guessed, or business-specific."""

    original: str
    normalized_domain: str
    registrable_domain: str
    subdomain: Optional[str]
    root_label: str
    suffix: str
    is_idn: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original": self.original,
            "normalized_domain": self.normalized_domain,
            "registrable_domain": self.registrable_domain,
            "subdomain": self.subdomain,
            "root_label": self.root_label,
            "suffix": self.suffix,
            "is_idn": self.is_idn,
        }


def canonicalize(url_or_domain: str) -> Optional[CanonicalDomain]:
    """Builds a complete CanonicalDomain, or None for empty/unparsable
    input (never fabricates a canonicalization for nothing) -- same
    "never fabricate" discipline evidence.py/features.py already use for
    absent data."""
    if not url_or_domain or not url_or_domain.strip():
        return None
    raw = url_or_domain.strip()
    is_idn = not raw.isascii()
    normalized = normalize_domain(raw)
    if not normalized:
        return None
    suffix, registrable = _suffix_and_registrable(normalized)
    subdomain = None
    if registrable and normalized != registrable and normalized.endswith("." + registrable):
        subdomain = normalized[: -(len(registrable) + 1)] or None
    return CanonicalDomain(
        original=raw,
        normalized_domain=normalized,
        registrable_domain=registrable,
        subdomain=subdomain,
        root_label=registrable.split(".")[0] if registrable else "",
        suffix=suffix,
        is_idn=is_idn,
    )


def same_registrable_domain(a: str, b: str) -> bool:
    """True when two domains/URLs are the same organization's
    infrastructure beyond any doubt -- identical eTLD+1, regardless of
    subdomain/www/case/port differences."""
    ra, rb = registrable_domain(a), registrable_domain(b)
    return bool(ra) and ra == rb


def root_label_match(a: str, b: str) -> bool:
    """The weaker, cross-suffix signal: same registrable-domain root
    label, but a genuinely different suffix (so NOT already covered by
    `same_registrable_domain`) -- e.g. company.com vs company.co.uk.
    Callers must treat this as evidence for a *relationship*, never as
    proof the two are the same identity -- see organization.py."""
    if same_registrable_domain(a, b):
        return False
    la, lb = root_label(a), root_label(b)
    return bool(la) and la == lb

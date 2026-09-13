"""
JSON-LD extraction, filtered by §2.3 before anything reaches an adapter.

Why this is core and not per-adapter: §2.3 forbids reading `RealEstateAgent`
and `Person` blocks at all, and P0 §5 found agency phone and email in the
site chrome of every page -- which in JSON-LD means an `Organization` node
sitting right beside the `Product` node we want. Enforcing the type filter
once beats trusting eight adapters to remember it.

§13.6 item 3 upgraded JSON-LD from preference to requirement for Group B:
price is present in JSON-LD on 4/4 pages but in scrapeable visible text on
only 3/6. A DOM-first adapter there silently loses half its prices.

Parsing is stdlib-only (regex + json). The <script> payload is JSON, not
markup -- pulling in a DOM parser to reach it would be ceremony.
"""
from __future__ import annotations

import json
import re

# Node types an adapter may read. Allowlist, for the same reason §2.2 is an
# allowlist: a denylist fails the first time a platform emits a type nobody
# anticipated, and here that type would carry a broker's phone number.
ALLOWED_TYPES = frozenset({
    "product", "individualproduct", "offer", "aggregateoffer",
    "residence", "apartment", "house", "singlefamilyresidence",
    "accommodation", "place", "postaladdress", "geocoordinates",
    "propertyvalue", "quantitativevalue", "itemlist", "listitem",
    # A LISTING is the advertisement for a property. It is not the agent.
    # This was wrongly forbidden until 2026-08-28 -- see the note below.
    "realestatelisting",
})

# Explicitly named so the reason is greppable, though the allowlist above
# already excludes them.
#
# `realestatelisting` used to be here and should never have been: it was
# conflated with `RealEstateAgent`. An agent is a person or a business; a
# LISTING is the advertisement for a property, which is precisely what this
# pipeline collects. The mistake was silent and costly -- one platform emits
# its description and `datePosted` on a node typed
# `["RealEstateListing", "Apartment"]`, and the whole node was being dropped
# because one of its two types was forbidden.
FORBIDDEN_TYPES = frozenset({
    "realestateagent", "person", "organization", "localbusiness",
    "contactpoint", "employee",
})

# Keys stripped wherever they appear, whatever the node type. Belt and braces:
# a `Product` may legitimately carry `seller` -> Organization -> telephone.
CONTACT_KEYS = frozenset({
    "telephone", "phone", "faxnumber", "email", "contactpoint", "contactpoints",
    "author", "agent", "seller", "broker", "provider", "publisher", "brand",
    "vendor", "employee", "founder", "member", "sameas", "sponsor",
    "potentialaction", "creator", "owner", "landlord", "realestateagent",
})

_SCRIPT_RE = re.compile(
    r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.I | re.S,
)


def _types_of(node: dict) -> set[str]:
    raw = node.get("@type") or node.get("type") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(t).strip().lower() for t in raw}


def _strip_contact(value):
    """Recursively drop contact-bearing keys. Never-fetch's little brother."""
    if isinstance(value, dict):
        return {
            k: _strip_contact(v)
            for k, v in value.items()
            if k.strip().lower() not in CONTACT_KEYS
        }
    if isinstance(value, list):
        return [_strip_contact(v) for v in value]
    return value


def _flatten(node, out: list[dict]) -> None:
    """Walk @graph / arrays / nested nodes, collecting every typed object."""
    if isinstance(node, list):
        for item in node:
            _flatten(item, out)
        return
    if not isinstance(node, dict):
        return
    if "@graph" in node:
        _flatten(node["@graph"], out)
    if _types_of(node):
        out.append(node)
    for key, value in node.items():
        if key in ("@graph",):
            continue
        if isinstance(value, (dict, list)):
            _flatten(value, out)


def extract_blocks(html: str) -> list[dict]:
    """
    Every JSON-LD node in the page whose @type is allowed, contact keys removed.

    Malformed blocks are skipped rather than raised on: a broken analytics
    snippet in the page footer is not a reason to lose the listing.
    """
    if not html:
        return []

    nodes: list[dict] = []
    for match in _SCRIPT_RE.finditer(html):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            nodes_in_block: list[dict] = []
            _flatten(json.loads(raw), nodes_in_block)
        except (json.JSONDecodeError, ValueError):
            continue
        nodes.extend(nodes_in_block)

    kept = []
    for node in nodes:
        types = _types_of(node)
        if types & FORBIDDEN_TYPES:
            continue
        if not (types & ALLOWED_TYPES):
            continue
        kept.append(_strip_contact(node))
    return kept


def first_of_type(blocks, *types: str) -> dict | None:
    """First block matching any of `types`, in the order the types are given."""
    wanted = [t.lower() for t in types]
    for want in wanted:
        for block in blocks:
            if want in _types_of(block):
                return block
    return None


def find_raw(blocks, key: str, types: tuple[str, ...] = ()):
    """
    First block's value for `key`, WITHOUT flattening lists.

    `find()` collapses a list to its first element, which is right for scalars
    reached through a nested path and wrong for genuinely list-valued fields
    such as `amenityFeature`. Use this one for those.
    """
    candidates = blocks
    if types:
        wanted = {t.lower() for t in types}
        candidates = [b for b in blocks if _types_of(b) & wanted]
    for block in candidates:
        value = block.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def find(blocks, *path: str, types: tuple[str, ...] = ()):
    """
    Read a dotted path out of the first block that has it.

    `find(blocks, "offers", "price")` handles both `offers` as a dict and as
    a list, which the two Microsistec groups disagree about (§13.2).
    """
    candidates = blocks
    if types:
        candidates = [b for b in blocks if _types_of(b) & {t.lower() for t in types}]

    for block in candidates:
        node = block
        for key in path:
            if isinstance(node, list):
                node = node[0] if node else None
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
            if node is None:
                break
        if isinstance(node, list):
            node = node[0] if node else None
        if node not in (None, "", []):
            return node
    return None

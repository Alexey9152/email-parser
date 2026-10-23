"""Extract social links from map-provider payloads.

Google Maps scrapers and OSM exporters use different field names for the same
Facebook page (``facebook``, ``facebookUrl``, ``social_links``, ...).  Keeping
the search here avoids duplicating fragile key lists in each provider.
"""

from __future__ import annotations

from typing import Any

from ..extractors import clean_facebook_url

_SOCIAL_KEYS = {
    "social", "socials", "sociallinks", "social_links", "socialmedia",
    "social_media", "links", "contact", "contacts", "about", "details",
}
_MAX_DEPTH = 5


def _facebook_from_value(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.lower()
        if "facebook.com" in lowered or "fb.me" in lowered or "fb.com" in lowered:
            return clean_facebook_url(value)
        return ""
    if isinstance(value, dict):
        # Social-link lists are often ``[{"name": "Facebook", "url": ...}]``.
        for key in ("url", "link", "href", "value"):
            item = value.get(key)
            if isinstance(item, str):
                found = _facebook_from_value(item)
                if found:
                    return found
        return _search(value, 0)
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _facebook_from_value(item)
            if found:
                return found
    return ""


def _search(value: dict[str, Any], depth: int) -> str:
    if depth > _MAX_DEPTH:
        return ""
    # Prefer explicit Facebook-ish fields.
    for key, item in value.items():
        lowered = str(key).lower()
        if "facebook" in lowered or lowered in {"fb", "fburl", "fb_url"}:
            found = _facebook_from_value(item)
            if found:
                return found
    # Then look inside known social/contact containers only.  This deliberately
    # ignores review text, which may mention somebody else's page.
    for key, item in value.items():
        if str(key).lower() in _SOCIAL_KEYS:
            if isinstance(item, dict):
                found = _search(item, depth + 1)
            else:
                found = _facebook_from_value(item)
            if found:
                return found
    return ""


def facebook_from_payload(payload: Any) -> str:
    """Return the first Facebook profile URL found in a provider item."""
    return _facebook_from_value(payload)

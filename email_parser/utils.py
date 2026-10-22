"""Small helpers: URL normalisation, domain parsing, text cleanup."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import tldextract

# Bundled public-suffix snapshot: no network call at import time.
_extract_domain = tldextract.TLDExtract(suffix_list_urls=())


def clean_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def normalize_url(url: str) -> str:
    """Return an absolute http(s) URL, or '' when the input is unusable."""
    url = clean_whitespace(url)
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    # Google sometimes ships tracking wrappers as the "website" field.
    if "google.com/url" in parts.netloc + parts.path:
        return ""
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        pass  # keep www: some sites redirect poorly without it
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def registrable_domain(url: str) -> str:
    """example.co.uk for https://menu.example.co.uk/contacts."""
    url = normalize_url(url)
    if not url:
        return ""
    parts = urlsplit(url)
    ext = _extract_domain(parts.netloc)
    if not ext.domain or not ext.suffix:
        return parts.netloc.lower()
    return f"{ext.domain}.{ext.suffix}".lower()


def same_site(a: str, b: str) -> bool:
    da, db = registrable_domain(a), registrable_domain(b)
    return bool(da) and da == db


def city_from_query(query: str) -> str:
    """'restaurants in Ocean City, MD' -> 'Ocean City, MD'."""
    match = re.search(r"\b(?:in|near|around)\s+(.+)$", query or "", re.IGNORECASE)
    if match:
        return clean_whitespace(match.group(1))
    parts = [p.strip() for p in (query or "").split(",") if p.strip()]
    return parts[-1] if len(parts) > 1 else ""


def unique(items) -> list:
    seen: set = set()
    out = []
    for item in items:
        key = item if isinstance(item, (str, int, float, tuple)) else repr(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def slugify(value: str, max_length: int = 60) -> str:
    value = re.sub(r"[^\w\s-]", "", (value or "").lower(), flags=re.UNICODE)
    value = re.sub(r"[\s_-]+", "-", value).strip("-")
    return (value[:max_length].strip("-") or "output")


def ensure_parent(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

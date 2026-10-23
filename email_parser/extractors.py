"""E-mail / link extraction from raw HTML.

Everything here is pure (no I/O) so it can be unit-tested cheaply.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .utils import clean_whitespace, normalize_url, registrable_domain, unique

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Assets and third-party widgets that love to leak fake addresses.
JUNK_EMAIL_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
    ".css", ".js", ".json", ".woff", ".woff2", ".ttf",
)
JUNK_DOMAINS = (
    "sentry.io", "sentry-next.wixpress.com", "wixpress.com", "wix.com",
    "example.com", "example.org", "example.net", "domain.com",
    "yourdomain.com", "email.com", "test.com", "site.com", "localhost",
    "sentry.wixpress.com", "godaddy.com", "squarespace.com",
)
JUNK_LOCAL_PARTS = {
    "yourname", "your-name", "your_name", "your.email", "your-email",
    "name", "username", "user", "email", "e-mail", "test", "example",
    "someone", "firstname", "lastname", "john.doe", "johndoe",
}
HEX_LOCAL_RE = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)

CONTACT_KEYWORDS = (
    "contact", "contact-us", "contactus", "contacts", "about", "about-us",
    "reach", "reach-us", "kontakt", "impressum", "support", "team",
)
SOCIAL_HOSTS = {
    "facebook": ("facebook.com", "fb.com", "fb.me"),
    "instagram": ("instagram.com",),
    "twitter": ("twitter.com", "x.com"),
}

_OBFUSCATION = (
    (re.compile(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", re.IGNORECASE), "."),
)
_ESCAPED_AT = re.compile(r"\\u0040|%40", re.IGNORECASE)
_ESCAPED_DOT = re.compile(r"\\u002e|%2e", re.IGNORECASE)
# JSON/JS escapes such as \u003e leak into page text and turn into junk locals.
_JSON_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
JUNK_LOCAL_PREFIX_RE = re.compile(r"^u00[0-9a-f]{2}", re.IGNORECASE)


@dataclass(slots=True)
class Link:
    url: str
    text: str = ""


def _decode_json_escape(match: re.Match[str]) -> str:
    try:
        return chr(int(match.group(1), 16))
    except ValueError:  # pragma: no cover - regex guarantees hex
        return match.group(0)


def deobfuscate(text: str) -> str:
    for pattern, replacement in _OBFUSCATION:
        text = pattern.sub(replacement, text)
    text = _ESCAPED_AT.sub("@", text)
    text = _ESCAPED_DOT.sub(".", text)
    text = _JSON_ESCAPE.sub(_decode_json_escape, text)
    return text


def normalize_email(raw: str) -> str:
    """Clean one candidate address; returns '' if it looks like noise."""
    value = html_lib.unescape(raw or "")
    value = value.strip().strip(".,;:!?'\"<>()[]{}")
    if value.lower().startswith("mailto:"):
        value = value[7:]
    value = unquote(value)
    if "?" in value:
        value = value.split("?", 1)[0]
    if "#" in value:
        value = value.split("#", 1)[0]
    value = value.strip().strip(".,;:!?'\"<>()[]{}").lower()
    value = value.replace(" ", "")
    if not value or "%" in value:
        return ""
    if not EMAIL_RE.fullmatch(value):
        return ""
    if len(value) > 254:
        return ""

    local, _, domain = value.rpartition("@")
    if not local or not domain or "." not in domain:
        return ""
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in value:
        return ""
    if domain.endswith(JUNK_EMAIL_SUFFIXES):
        return ""
    if any(domain == d or domain.endswith("." + d) for d in JUNK_DOMAINS):
        return ""
    if local in JUNK_LOCAL_PARTS:
        return ""
    if HEX_LOCAL_RE.match(local):
        return ""
    if JUNK_LOCAL_PREFIX_RE.match(local):
        return ""
    # Domains made only of digits (e.g. 123456@2x.png leftovers) are never real.
    if re.fullmatch(r"[\d.]+", domain):
        return ""
    return value


def extract_emails(*texts: str) -> list[str]:
    """Grab every plausible address from an arbitrary blob of text."""
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in EMAIL_RE.findall(deobfuscate(text)):
            email = normalize_email(match)
            if email:
                found.append(email)
    return unique(found)


def soup_from_html(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - lxml missing/broken
        return BeautifulSoup(html, "html.parser")


def visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return clean_whitespace(soup.get_text(" "))


def collect_links(soup: BeautifulSoup, base_url: str) -> list[Link]:
    links: list[Link] = []
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.lower().startswith(("javascript:", "tel:", "sms:")):
            continue
        absolute = urljoin(base_url, href)
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        links.append(Link(url=absolute, text=clean_whitespace(anchor.get_text(" "))))
    return links


def mailto_addresses(soup: BeautifulSoup) -> list[str]:
    emails: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            emails.extend(extract_emails(href, anchor.get_text(" ")))
    # data-* attributes are a common obfuscation trick
    for tag in soup.find_all(attrs={"data-email": True}):
        emails.extend(extract_emails(tag.get("data-email")))
    return unique(emails)


def _json_emails(node, depth: int = 0) -> list[str]:
    """Walk JSON-LD looking for ``"email": "..."`` (very common on restaurant sites)."""
    if depth > 8:
        return []
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in {"email", "e-mail", "mail"}:
                if isinstance(value, str):
                    found.extend(extract_emails(value))
                elif isinstance(value, list):
                    found.extend(extract_emails(*[str(v) for v in value]))
            else:
                found.extend(_json_emails(value, depth + 1))
    elif isinstance(node, list):
        for item in node[:50]:
            found.extend(_json_emails(item, depth + 1))
    return found


def structured_emails(soup: BeautifulSoup) -> list[str]:
    """E-mails advertised in JSON-LD, microdata or og:email meta tags."""
    found: list[str] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            found.extend(_json_emails(json.loads(raw)))
        except (ValueError, TypeError):
            found.extend(extract_emails(raw))

    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or "").lower()
        if key in {"og:email", "email", "contact:email"} and meta.get("content"):
            found.extend(extract_emails(meta["content"]))

    for tag in soup.select('[itemprop="email"]'):
        found.extend(extract_emails(tag.get("content") or tag.get_text(" ")))
    return unique(found)


def script_emails(soup: BeautifulSoup, limit: int = 300_000) -> list[str]:
    """Inline JS often holds addresses that never reach the rendered markup."""
    chunks: list[str] = []
    total = 0
    for script in soup.find_all("script"):
        raw = script.string or script.get_text() or ""
        if not raw:
            continue
        chunks.append(raw[: max(0, limit - total)])
        total += len(raw)
        if total >= limit:
            break
    return extract_emails(*chunks)


def emails_from_page(html: str) -> tuple[list[str], BeautifulSoup]:
    """mailto + JSON-LD + inline scripts + visible text, junk filtered."""
    soup = soup_from_html(html)
    emails = (
        mailto_addresses(soup)
        + structured_emails(soup)
        + script_emails(soup)
        + extract_emails(visible_text(soup))
    )
    return unique(emails), soup


def contact_links(links: list[Link], base_url: str, limit: int = 3) -> list[Link]:
    base_domain = registrable_domain(base_url)
    scored: list[tuple[int, Link]] = []
    for link in links:
        if registrable_domain(link.url) != base_domain:
            continue
        path = urlsplit(link.url).path.lower().strip("/")
        text = link.text.lower()
        if not path and not text:
            continue
        score = 0
        for keyword in CONTACT_KEYWORDS:
            if keyword in path:
                score = max(score, 3)
            elif keyword in text:
                score = max(score, 1)
        if score and link.url.rstrip("/") != base_url.rstrip("/"):
            scored.append((score, link))
    scored.sort(key=lambda item: -item[0])
    return unique([link for _, link in scored])[:limit]


def _is_social_page(host: str, path: str) -> bool:
    # Compare whole segments: a venue slug like /trattoria must not be mistaken
    # for the tracking endpoint /tr, and /photography for /photo.
    normalized = "/" + path.strip("/").lower()
    if normalized in {
        "/sharer", "/dialog", "/plugins", "/login", "/help", "/policies",
        "/legal", "/tr", "/watch", "/watchparty", "/recover", "/privacy",
        "/terms", "/share", "/photo", "/photos", "/business", "/ads",
        "/ad_campaign", "/marketing", "/messages", "/permalink", "/search",
    }:
        return False
    blocked_prefixes = (
        "/sharer/", "/dialog/", "/plugins/", "/login/", "/help/",
        "/policies/", "/legal/", "/share/", "/hashtag/", "/events/",
        "/groups/", "/posts/", "/photos/", "/photo/", "/watch/", "/reel/",
        "/reels/", "/videos/", "/business/", "/ads/", "/marketing/",
        "/messages/", "/permalink/",
    )
    if any(normalized.startswith(prefix) for prefix in blocked_prefixes):
        return False
    return bool(path.strip("/"))


# Facebook pages for "facebook", "pages", "help" etc. are never a venue.
FB_GENERIC_SLUGS = frozenset(
    {
        "facebook", "pages", "people", "public", "help", "business", "login",
        "share", "sharer", "plugins", "dialog", "groups", "events", "watch",
        "reel", "marketplace", "gaming", "privacy", "policies", "terms",
        "legal", "about", "home", "photo", "photos", "video", "videos",
        "story", "permalink", "profile", "search", "hashtag", "ads",
    }
)


def _title_tokens(title: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9']{3,}", (title or "").lower())
        if token not in {"the", "and", "for", "inc", "llc", "ltd", "restaurant", "cafe", "bar"}
    ]


def _url_hint_score(url: str, title: str) -> int:
    """How many title words appear in the Facebook/Instagram slug."""
    tokens = _title_tokens(title)
    if not tokens:
        return 0
    slug = urlsplit(url).path.strip("/").split("/")[0].lower()
    return sum(1 for token in tokens if token in slug)


def social_links(links: list[Link], title: str = "") -> dict[str, str]:
    """Best plausible Facebook / Instagram / Twitter profile URL.

    When several candidates exist, the one whose slug matches the venue name
    wins (footer widgets often link to the agency's own page).
    """
    candidates: dict[str, list[tuple[int, int, str]]] = {}
    for index, link in enumerate(links):
        parts = urlsplit(link.url)
        host = parts.netloc.lower().removeprefix("www.")
        path = parts.path.lower()
        for network, hosts in SOCIAL_HOSTS.items():
            if not any(host == h or host.endswith("." + h) for h in hosts):
                continue
            if not _is_social_page(host, path):
                continue
            candidate = (
                clean_facebook_url(link.url)
                if network == "facebook"
                else f"https://{parts.netloc}{parts.path}"
            )
            if not candidate:
                continue
            candidates.setdefault(network, []).append(
                (_url_hint_score(candidate, title), index, candidate)
            )

    found: dict[str, str] = {}
    for network, items in candidates.items():
        items.sort(key=lambda item: (-item[0], item[1]))
        found[network] = items[0][2]
    return found


FB_ENDPOINT_PATHS = {
    "profile.php", "permalink.php", "photo.php", "story.php", "l.php",
    "watch", "reel", "share", "sharer", "login", "groups", "events",
    "hashtag", "marketplace", "gaming", "dialog", "plugins",
}
FB_LEGACY_PREFIXES = {"pages", "people", "public"}


def clean_facebook_url(url: str) -> str:
    """Accept fb.com / m.facebook.com and return a canonical *profile* URL.

    Search results often point to a post or to a profile sub-tab; the pipeline
    opens these URLs in a browser, so a post link is reduced to its page slug
    and endpoint links without an id are rejected.
    """
    candidate = normalize_url(url)
    if not candidate:
        return ""
    parts = urlsplit(candidate)
    host = parts.netloc.lower()
    if not any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS["facebook"]):
        return ""
    path = parts.path.rstrip("/")
    if not path or not _is_social_page(host, path):
        return ""

    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return ""
    first = segments[0].lower()
    query = parse_qs(parts.query)

    if first.endswith(".php") or first in FB_ENDPOINT_PATHS:
        page_id = (query.get("id") or [""])[0].strip()
        if first == "profile.php" and page_id.isdigit():
            return f"https://www.facebook.com/profile.php?id={page_id}"
        return ""

    if first == "pg" and len(segments) >= 2:
        # /pg/Name is a legacy profile link; canonicalise it to /Name.
        slug = segments[1]
        if re.fullmatch(r"[A-Za-z0-9._-]+", slug) and slug.lower() not in FB_GENERIC_SLUGS:
            return f"https://www.facebook.com/{slug}"
        return ""

    if first in FB_LEGACY_PREFIXES:
        # /pages/Name/123 and /people/Name/123 are valid multi-segment URLs.
        return f"https://www.facebook.com{path}" if len(segments) >= 3 else ""

    slug = segments[0]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", slug):
        return ""
    if slug.lower() in FB_GENERIC_SLUGS:
        return ""
    # Drop sub-tabs like /posts/..., /photos/..., /reviews or /e.
    return f"https://www.facebook.com/{slug}"


# Facebook links can hide in inline JS / JSON-LD, not only in <a href>.
_FACEBOOK_URL_RE = re.compile(
    r"(?:https?:)?//(?:[a-z0-9-]+\.)*(?:facebook\.com|fb\.com|fb\.me)/[^\s\"'<>\\]+",
    re.IGNORECASE,
)


def facebook_from_html(html: str, title: str = "") -> str:
    """Find the venue's Facebook page anywhere in a raw HTML document.

    Covers anchors, JSON-LD ``sameAs``, escaped ``https:\\/\\/...`` strings and
    ``fb.me`` short links.  Profile/post/sharer/plugin URLs are rejected or
    reduced to the page profile by :func:`clean_facebook_url`.
    """
    if not html:
        return ""
    text = html_lib.unescape(deobfuscate(html)).replace("\\/", "/")
    candidates: list[tuple[int, int, str]] = []
    for index, match in enumerate(_FACEBOOK_URL_RE.finditer(text)):
        candidate = match.group(0).rstrip(").,;:!?'\"")
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        elif not candidate.lower().startswith(("http://", "https://")):
            candidate = "https://" + candidate
        cleaned = clean_facebook_url(candidate)
        if cleaned:
            candidates.append((_url_hint_score(cleaned, title), index, cleaned))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]

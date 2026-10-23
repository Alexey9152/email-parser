"""Detect chain restaurants and separate their corporate inboxes.

Large chains do not accept job applications by e-mail at a local restaurant:
their corporate sites use application forms, and the address published on the
brand page is a customer-service inbox.  Keeping those venues in the report is
still useful (the website is the application route), but their generic
corporate addresses must not be mistaken for a manager's e-mail.

A location-specific address (``store123@brand.com``, or a franchise's own
``gmail``) is never discarded.
"""

from __future__ import annotations

import re

from .utils import registrable_domain

# Canonical name -> normalized aliases.  Keep this list focused on brands that
# are commonly present in a city's Google Maps/OSM results.
_RAW_ALIASES: dict[str, tuple[str, ...]] = {
    "Dunkin'": ("dunkin", "dunkin donuts", "dunkin' donuts"),
    "Subway": ("subway",),
    "McDonald's": ("mcdonalds", "mcdonald's"),
    "Starbucks": ("starbucks", "starbucks coffee"),
    "7-Eleven": ("7 eleven", "7-eleven", "seven eleven"),
    "Burger King": ("burger king",),
    "Wendy's": ("wendys", "wendy's"),
    "Taco Bell": ("taco bell",),
    "KFC": ("kfc", "kentucky fried chicken"),
    "Popeyes": ("popeyes", "popeyes louisiana kitchen"),
    "Domino's": ("dominos", "domino's"),
    "Pizza Hut": ("pizza hut",),
    "Papa John's": ("papa johns", "papa john's"),
    "Little Caesars": ("little caesars",),
    "Chipotle": ("chipotle", "chipotle mexican grill"),
    "Panera Bread": ("panera", "panera bread"),
    "Chick-fil-A": ("chick fil a", "chick-fil-a"),
    "Sonic": ("sonic", "sonic drive in"),
    "Arby's": ("arbys", "arby's"),
    "Dairy Queen": ("dairy queen", "dq"),
    "Baskin-Robbins": ("baskin robbins", "baskin-robbins"),
    "Cold Stone Creamery": ("cold stone", "cold stone creamery"),
    "Smoothie King": ("smoothie king",),
    "Tropical Smoothie Cafe": ("tropical smoothie", "tropical smoothie cafe"),
    "Wawa": ("wawa",),
    "Sheetz": ("sheetz",),
    "Circle K": ("circle k",),
    "Five Guys": ("five guys", "five guys burgers and fries"),
    "Jimmy John's": ("jimmy johns", "jimmy john's"),
    "Jersey Mike's": ("jersey mikes", "jersey mike's"),
    "Firehouse Subs": ("firehouse subs",),
    "Potbelly": ("potbelly", "potbelly sandwich shop"),
    "Qdoba": ("qdoba", "qdoba mexican eats"),
    "Moe's Southwest Grill": ("moes southwest grill", "moe's southwest grill"),
    "Auntie Anne's": ("auntie annes", "auntie anne's"),
    "Cinnabon": ("cinnabon",),
    "Krispy Kreme": ("krispy kreme",),
    "Tim Hortons": ("tim hortons",),
    "IHOP": ("ihop", "international house of pancakes"),
    "Denny's": ("dennys", "denny's"),
    "Applebee's": ("applebees", "applebee's"),
    "Chili's": ("chilis", "chili's"),
    "TGI Fridays": ("tgi fridays", "tgi friday's", "t.g.i. fridays"),
    "Olive Garden": ("olive garden",),
    "Red Lobster": ("red lobster",),
    "Outback Steakhouse": ("outback steakhouse",),
    "Texas Roadhouse": ("texas roadhouse",),
    "Buffalo Wild Wings": ("buffalo wild wings",),
    "Hooters": ("hooters",),
    "P.F. Chang's": ("pf changs", "p.f. chang's"),
    "The Cheesecake Factory": ("cheesecake factory",),
    "Ruth's Chris Steak House": ("ruths chris", "ruth's chris"),
    "Morton's The Steakhouse": ("mortons", "morton's"),
}


def _normalize(text: str) -> str:
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


CHAIN_ALIASES: dict[str, tuple[str, ...]] = {
    canonical: tuple(_normalize(alias) for alias in aliases)
    for canonical, aliases in _RAW_ALIASES.items()
}

# Corporate domains are used only to recognise generic corporate mailboxes.
# A franchise/local domain is left untouched.
CHAIN_EMAIL_DOMAINS: dict[str, set[str]] = {
    "Dunkin'": {"dunkindonuts.com", "dunkin.com"},
    "Subway": {"subway.com"},
    "McDonald's": {"mcdonalds.com"},
    "Starbucks": {"starbucks.com"},
    "7-Eleven": {"7-eleven.com", "7eleven.com"},
    "Burger King": {"burgerking.com", "bk.com"},
    "Wendy's": {"wendys.com"},
    "Taco Bell": {"tacobell.com"},
    "KFC": {"kfc.com"},
    "Popeyes": {"popeyes.com"},
    "Domino's": {"dominos.com"},
    "Pizza Hut": {"pizzahut.com"},
    "Papa John's": {"papajohns.com"},
    "Little Caesars": {"littlecaesars.com"},
    "Chipotle": {"chipotle.com"},
    "Panera Bread": {"panerabread.com"},
    "Chick-fil-A": {"chick-fil-a.com"},
    "Sonic": {"sonicdrivein.com"},
    "Arby's": {"arbys.com"},
    "Dairy Queen": {"dairyqueen.com"},
    "Baskin-Robbins": {"baskinrobbins.com"},
    "Cold Stone Creamery": {"coldstonecreamery.com"},
    "Smoothie King": {"smoothieking.com"},
    "Tropical Smoothie Cafe": {"tropicalsmoothie.com"},
    "Wawa": {"wawa.com"},
    "Sheetz": {"sheetz.com"},
    "Circle K": {"circlek.com"},
    "Five Guys": {"fiveguys.com"},
    "Jimmy John's": {"jimmyjohns.com"},
    "Jersey Mike's": {"jerseymikes.com"},
    "Firehouse Subs": {"firehousesubs.com"},
    "Potbelly": {"potbelly.com"},
    "Qdoba": {"qdoba.com"},
    "Moe's Southwest Grill": {"moes.com"},
    "Auntie Anne's": {"auntieannes.com"},
    "Cinnabon": {"cinnabon.com"},
    "Krispy Kreme": {"krispykreme.com"},
    "Tim Hortons": {"timhortons.com"},
    "IHOP": {"ihop.com"},
    "Denny's": {"dennys.com"},
    "Applebee's": {"applebees.com"},
    "Chili's": {"chilis.com"},
    "TGI Fridays": {"tgifridays.com"},
    "Olive Garden": {"olivegarden.com"},
    "Red Lobster": {"redlobster.com"},
    "Outback Steakhouse": {"outback.com"},
    "Texas Roadhouse": {"texasroadhouse.com"},
    "Buffalo Wild Wings": {"buffalowildwings.com"},
    "Hooters": {"hooters.com"},
    "P.F. Chang's": {"pfchangs.com"},
    "The Cheesecake Factory": {"thecheesecakefactory.com"},
    "Ruth's Chris Steak House": {"ruthschris.com"},
    "Morton's The Steakhouse": {"mortons.com"},
}

# Parent-company domains also host generic corporate inboxes, but they must not
# identify a brand from a URL alone (a parent owns several chains).
CHAIN_CORPORATE_EXTRA: dict[str, set[str]] = {
    "Dunkin'": {"inspirebrands.com"},
    "Burger King": {"rbi.com", "restaurantbrands.com"},
    "Taco Bell": {"yum.com", "yumbrands.com"},
    "KFC": {"yum.com", "yumbrands.com"},
    "Popeyes": {"rbi.com", "restaurantbrands.com"},
    "Sonic": {"inspirebrands.com"},
    "Arby's": {"inspirebrands.com"},
    "Baskin-Robbins": {"inspirebrands.com"},
    "Jimmy John's": {"inspirebrands.com"},
    "Firehouse Subs": {"rbi.com", "restaurantbrands.com"},
    "Auntie Anne's": {"focusbrands.com"},
    "Cinnabon": {"focusbrands.com"},
    "Tim Hortons": {"rbi.com", "restaurantbrands.com"},
    "IHOP": {"dinebrands.com"},
    "Applebee's": {"dinebrands.com"},
    "Chili's": {"brinker.com"},
    "Olive Garden": {"darden.com"},
    "Buffalo Wild Wings": {"inspirebrands.com"},
}

# Domains that identify a chain even when the title is abbreviated.
CHAIN_DOMAINS: dict[str, str] = {
    domain: canonical
    for canonical, domains in CHAIN_EMAIL_DOMAINS.items()
    for domain in domains
}

GENERIC_CORPORATE_LOCALS = frozenset(
    {
        "info", "contact", "contactus", "hello", "team", "support", "customer",
        "customers", "customercare", "customerservice", "customersupport",
        "supportteam", "care", "service", "services", "feedback", "help",
        "careers", "career", "jobs", "job", "employment", "recruiting",
        "recruitment", "hr", "humanresources", "talent", "staffing",
        "marketing", "press", "media", "communications", "pr", "corporate",
        "communications", "franchise", "franchising", "franchisee",
        "legal", "privacy", "billing", "accounts", "accounting", "admin",
        "office", "sales", "orders", "order", "noreply", "no-reply",
        "donotreply", "do-not-reply", "webmaster", "postmaster",
    }
)

_STORE_SPECIFIC = re.compile(
    r"(?:store|location|branch|franchise|shop|unit|restaurant)\s*\d+|\d{2,}",
    re.IGNORECASE,
)


def detect_chain(title: str, website: str = "") -> str:
    """Return the canonical chain name, or an empty string for an independent venue."""
    normalized = _normalize(title)
    if not normalized:
        return ""
    # Domain first: an OSM node may only say "Dunkin" or "Store #123".
    domain = registrable_domain(website)
    if domain:
        for root, canonical in CHAIN_DOMAINS.items():
            if domain == root or domain.endswith("." + root):
                return canonical
    candidates = [normalized]
    if normalized.startswith("the "):
        candidates.append(normalized[4:])
    for canonical, aliases in CHAIN_ALIASES.items():
        for alias in aliases:
            if not alias:
                continue
            if any(
                candidate == alias or candidate.startswith(alias + " ")
                for candidate in candidates
            ):
                return canonical
    return ""


def is_generic_chain_email(email: str, chain: str) -> bool:
    """True for a brand's corporate inbox, False for a store-specific address."""
    if not email or "@" not in email or not chain:
        return False
    local, _, domain = email.lower().partition("@")
    corporate = CHAIN_EMAIL_DOMAINS.get(chain, set()) | CHAIN_CORPORATE_EXTRA.get(
        chain, set()
    )
    if not any(domain == root or domain.endswith("." + root) for root in corporate):
        # A franchise's own domain or a consumer mailbox is location-specific.
        return False
    if _STORE_SPECIFIC.search(local):
        return False
    compact = re.sub(r"[^a-z0-9]+", "", local)
    return not compact or compact in GENERIC_CORPORATE_LOCALS


def filter_chain_emails(emails: list[str], chain: str) -> list[str]:
    """Drop generic corporate addresses from a chain row, keep local ones."""
    if not chain:
        return list(emails)
    return [email for email in emails if not is_generic_chain_email(email, chain)]

"""Pipeline: Maps -> filter -> website crawl -> Facebook fallback -> export."""

from __future__ import annotations

import dataclasses
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .exporter import (
    load_restaurants,
    save_restaurants_json,
    save_results_csv,
    save_results_json,
)
from .extractors import clean_facebook_url
from .facebook import SocialEmailFinder
from .guess import EmailGuesser
from .http_client import HttpClient
from .maps import build_provider
from .models import (
    STATUS_FB,
    STATUS_GUESS,
    STATUS_JS,
    STATUS_NOT_FOUND,
    STATUS_SEARCH,
    STATUS_SITE,
    ContactResult,
    Restaurant,
)
from .serp import build_web_search
from .utils import city_from_query, normalize_url, registrable_domain, unique
from .website import WebsiteCrawler, WebsiteResult

log = logging.getLogger(__name__)

# Booking/aggregator domains are never the venue's own site.
AGGREGATORS = (
    "facebook.com", "instagram.com", "yelp.com", "tripadvisor.com",
    "doordash.com", "ubereats.com", "grubhub.com", "opentable.com",
    "seamless.com", "zomato.com", "menuism.com", "allmenus.com",
    "restaurantji.com", "mapquest.com", "yellowpages.com", "google.com",
)


@dataclass(slots=True)
class RunStats:
    raw: int = 0
    filtered: int = 0
    with_email: int = 0
    from_site: int = 0
    from_facebook: int = 0
    from_search: int = 0
    from_js: int = 0
    from_guess: int = 0
    not_found: int = 0
    errors: int = 0
    duration: float = 0.0

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------- maps stage
def collect_restaurants(
    settings: Settings, query: str, limit: int, provider_name: str | None = None
) -> list[Restaurant]:
    provider = build_provider(settings, provider_name)
    log.info("Provider: %s | query: %r | limit: %d", provider.name, query, limit)
    started = time.time()
    items = provider.search(query, limit)
    log.info("Maps returned %d venues in %.1fs", len(items), time.time() - started)
    return items


def is_own_website(url: str) -> bool:
    domain = registrable_domain(url)
    if not domain:
        return False
    return not any(domain == agg or domain.endswith("." + agg) for agg in AGGREGATORS)


def filter_restaurants(items: list[Restaurant], limit: int | None = None) -> list[Restaurant]:
    """Drop duplicates and venues without a usable website (TODO 1.2.3)."""
    cleaned: list[Restaurant] = []
    seen_domains: set[str] = set()
    seen_titles: set[str] = set()

    for item in items:
        website = normalize_url(item.website)
        if not website or not is_own_website(website):
            continue
        domain = registrable_domain(website)
        title_key = " ".join((item.title or "").lower().split())
        if not title_key or domain in seen_domains or title_key in seen_titles:
            continue
        seen_domains.add(domain)
        seen_titles.add(title_key)

        item.website = website
        item.google_maps_url = normalize_url(item.google_maps_url) or item.google_maps_url
        cleaned.append(item)
        if limit and len(cleaned) >= limit:
            break
    return cleaned


# -------------------------------------------------------------- emails stage
def _empty_result(restaurant: Restaurant) -> ContactResult:
    return ContactResult(
        title=restaurant.title,
        website=restaurant.website,
        phone=restaurant.phone,
        address=restaurant.address,
        google_maps_url=restaurant.google_maps_url,
    )


def enrich_restaurants(
    settings: Settings,
    restaurants: list[Restaurant],
    *,
    query: str = "",
    use_search: bool = True,
    search_limit: int = 30,
    js_fallback: bool = False,
    facebook_browser: bool = False,
    guess: bool = False,
    workers: int | None = None,
) -> tuple[list[ContactResult], RunStats]:
    stats = RunStats(filtered=len(restaurants))
    started = time.time()
    results: dict[str, ContactResult] = {}
    site_results: dict[str, WebsiteResult] = {}
    workers = max(1, workers or settings.http_workers)

    with HttpClient(timeout=settings.http_timeout) as http:
        crawler = WebsiteCrawler(http)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(crawler.analyze, restaurant.website): restaurant
                for restaurant in restaurants
            }
            for done, future in enumerate(as_completed(futures), start=1):
                restaurant = futures[future]
                row = _empty_result(restaurant)
                try:
                    site = future.result()
                except Exception as exc:  # noqa: BLE001 - one bad site must not stop the run
                    stats.errors += 1
                    row.note = f"site error: {exc}"[:200]
                    log.debug("crawl failed %s: %s", restaurant.website, exc)
                    results[restaurant.website] = row
                    continue

                site_results[restaurant.website] = site
                if site.emails:
                    row.emails = site.emails
                    row.status = STATUS_SITE
                    row.sources = ["website"]
                    stats.from_site += 1
                if site.facebook_url:
                    row.facebook_url = clean_facebook_url(site.facebook_url)
                if site.blocked:
                    row.note = "website blocks bots"
                if site.error and not row.emails:
                    row.note = site.error

                log.info(
                    "[%d/%d] %-45s %s",
                    done,
                    len(restaurants),
                    restaurant.title[:45],
                    row.primary_email or "-",
                )
                results[restaurant.website] = row

    # ---- Search fallback (2.2): Facebook snippets, then the open web -----
    pending = [r for r in restaurants if not results[r.website].emails]
    if use_search and pending:
        city = city_from_query(query)
        with HttpClient(timeout=settings.http_timeout) as search_http:
            finder = SocialEmailFinder(build_web_search(settings, search_http))
            if not finder.enabled:
                log.info("No search backend available - Facebook/web lookup skipped")
            else:
                budget = max(0, search_limit)
                log.info(
                    "Search fallback for %d venues without e-mail (query budget %d)",
                    len(pending),
                    budget,
                )
                for restaurant in pending:
                    if budget <= 0 or not finder.enabled:
                        break
                    row = results[restaurant.website]
                    found = finder.find(
                        restaurant.title, city, row.facebook_url, max_queries=min(3, budget)
                    )
                    budget -= len(found.queries)
                    if found.facebook_url and not row.facebook_url:
                        row.facebook_url = found.facebook_url
                    if found.emails:
                        row.emails = found.emails
                        if found.source == "facebook":
                            row.status = STATUS_FB
                            row.sources = [f"facebook ({found.backend} snippet)"]
                            stats.from_facebook += 1
                        else:
                            row.status = STATUS_SEARCH
                            row.sources = [f"web search ({found.backend})"]
                            stats.from_search += 1
                        log.info(
                            "  %s hit: %s -> %s",
                            found.source,
                            restaurant.title[:40],
                            row.primary_email,
                        )
                    elif found.error:
                        log.debug("search miss %s: %s", restaurant.title, found.error)
                if budget <= 0:
                    log.info("Search query budget exhausted, remaining venues stay without e-mail")
    # ---- JS render fallback (2.3) ----------------------------------------
    js_queue = [
        r for r in restaurants
        if not results[r.website].emails and js_fallback
    ]
    if js_queue:
        from .playwright_site import PlaywrightSiteRenderer

        log.info("JS fallback for %d venues", len(js_queue))
        try:
            with PlaywrightSiteRenderer(headless=settings.headless) as renderer:
                for restaurant in js_queue:
                    row = results[restaurant.website]
                    rendered = renderer.analyze(restaurant.website)
                    if rendered.emails:
                        row.emails = rendered.emails
                        row.status = STATUS_JS
                        row.sources = ["website (js render)"]
                        stats.from_js += 1
                    if rendered.facebook_url and not row.facebook_url:
                        row.facebook_url = clean_facebook_url(rendered.facebook_url)
                    if rendered.blocked and not row.emails:
                        row.note = "website blocks bots"
        except Exception as exc:  # noqa: BLE001 - playwright missing / browser crash
            log.warning("JS fallback unavailable: %s", exc)

    # ---- Facebook page render (2.2.3, optional) --------------------------
    if facebook_browser:
        fb_queue = [
            r for r in restaurants
            if not results[r.website].emails and results[r.website].facebook_url
        ]
        if fb_queue:
            from .playwright_site import PlaywrightSiteRenderer

            log.info("Facebook page render for %d venues", len(fb_queue))
            try:
                with PlaywrightSiteRenderer(headless=settings.headless) as renderer:
                    for restaurant in fb_queue:
                        row = results[restaurant.website]
                        rendered = renderer.analyze(row.facebook_url, follow_contact=False)
                        if rendered.emails:
                            row.emails = rendered.emails
                            row.status = STATUS_FB
                            row.sources = ["facebook (page render)"]
            except Exception as exc:  # noqa: BLE001
                log.warning("Facebook browser fallback unavailable: %s", exc)

    # ---- MX-validated pattern guessing (optional) ------------------------
    if guess:
        guesser = EmailGuesser()
        guessed = 0
        for restaurant in restaurants:
            row = results[restaurant.website]
            if row.emails:
                continue
            attempt = guesser.guess(restaurant.website)
            if not attempt.emails:
                log.debug("no guess for %s: %s", restaurant.website, attempt.error)
                continue
            row.emails = attempt.emails
            row.status = STATUS_GUESS
            row.sources = ["pattern guess (MX ok)"]
            row.note = f"unverified; mail exchanger: {', '.join(attempt.mx[:2])}"
            guessed += 1
        stats.from_guess = guessed
        if guessed:
            log.info("Guessed %d unverified address(es) from MX records", guessed)

    ordered = [results[r.website] for r in restaurants]
    for row in ordered:
        row.emails = unique(row.emails)
        if not row.emails:
            row.status = STATUS_NOT_FOUND
            stats.not_found += 1
        elif row.status == STATUS_NOT_FOUND:
            row.status = STATUS_SITE
        stats.with_email += 1 if row.emails else 0

    stats.duration = time.time() - started
    return ordered, stats


# ------------------------------------------------------------------- driver
def run(
    settings: Settings,
    *,
    query: str = "",
    limit: int = 40,
    stage: str = "all",
    provider: str | None = None,
    input_path: Path | None = None,
    out_dir: Path | None = None,
    workers: int | None = None,
    use_search: bool = True,
    search_limit: int = 30,
    js_fallback: bool = False,
    facebook_browser: bool = False,
    guess: bool = False,
) -> tuple[list[ContactResult], RunStats]:
    data_dir = out_dir or settings.ensure_data_dir()
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    stats = RunStats()

    if stage in {"all", "maps"}:
        if not query:
            raise ValueError("--query is required for the maps stage")
        raw = collect_restaurants(settings, query, limit, provider)
        save_restaurants_json(data_dir / "restaurants_raw.json", raw)
        stats.raw = len(raw)
        restaurants = filter_restaurants(raw, limit=limit)
        save_restaurants_json(data_dir / "restaurants_list.json", restaurants)
        stats.filtered = len(restaurants)
        log.info(
            "Filtered %d -> %d venues (no website / duplicates removed)",
            stats.raw,
            stats.filtered,
        )
    else:
        source = input_path or (data_dir / "restaurants_list.json")
        restaurants = load_restaurants(Path(source))
        stats.filtered = len(restaurants)
        log.info("Loaded %d venues from %s", len(restaurants), source)

    if stage == "maps":
        return [], stats

    results, enrich_stats = enrich_restaurants(
        settings,
        restaurants,
        query=query,
        use_search=use_search,
        search_limit=search_limit,
        js_fallback=js_fallback,
        facebook_browser=facebook_browser,
        guess=guess,
        workers=workers,
    )
    enrich_stats.raw = stats.raw or stats.filtered
    save_results_csv(data_dir / "final_contacts.csv", results)
    save_results_json(data_dir / "final_contacts.json", results)
    log.info(
        "Done in %.1fs: %d with e-mail (site %d, fb %d, search %d, js %d, guessed %d), "
        "%d not found, %d errors",
        enrich_stats.duration,
        enrich_stats.with_email,
        enrich_stats.from_site,
        enrich_stats.from_facebook,
        enrich_stats.from_search,
        enrich_stats.from_js,
        enrich_stats.from_guess,
        enrich_stats.not_found,
        enrich_stats.errors,
    )
    return results, enrich_stats

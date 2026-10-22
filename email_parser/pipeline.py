"""Pipeline: Maps -> filter -> website crawl -> search/Facebook -> export."""

from __future__ import annotations

import dataclasses
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .categories import build_queries
from .config import Settings
from .exporter import (
    load_restaurants,
    save_restaurants_json,
    save_results_csv,
    save_results_json,
    save_results_xlsx,
)
from .extractors import clean_facebook_url
from .facebook import SocialEmailFinder, filter_email_list
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
from .utils import (
    Area,
    city_from_query,
    clean_whitespace,
    normalize_url,
    registrable_domain,
    unique,
)
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
    without_site: int = 0
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
def build_search_queries(
    query: str, categories: list[str] | None = None, area: Area | None = None
) -> list[str]:
    """Turn CLI/UI input into the actual list of Google Maps search strings."""
    categories = [c for c in (categories or []) if c.strip()]
    if categories:
        # A drawn area defines the place, so plain categories search the polygon
        # instead of an unrelated "in City" text restriction.
        if area is not None:
            return list(unique(categories))
        return build_queries(query, categories)
    return [query] if (query or "").strip() else []


def collect_restaurants(
    settings: Settings,
    query: str = "",
    limit: int = 40,
    provider_name: str | None = None,
    categories: list[str] | None = None,
    area: Area | None = None,
) -> list[Restaurant]:
    provider = build_provider(settings, provider_name)
    queries = build_search_queries(query, categories, area)
    if not queries:
        raise ValueError(
            "Nothing to search: pass --query, --categories with a location, "
            "or --categories with --bbox"
        )
    log.info(
        "Provider: %s | %d quer(ies) | limit: %d | area: %s",
        provider.name,
        len(queries),
        limit,
        area.as_text() if area else "—",
    )
    started = time.time()
    if len(queries) == 1:
        items = provider.search(queries[0], limit, area)
    else:
        items = provider.search_many(queries, limit, area)
    log.info("Maps returned %d venues in %.1fs", len(items), time.time() - started)
    return items


def is_own_website(url: str) -> bool:
    domain = registrable_domain(url)
    if not domain:
        return False
    return not any(domain == agg or domain.endswith("." + agg) for agg in AGGREGATORS)


def filter_restaurants(
    items: list[Restaurant],
    limit: int | None = None,
    require_website: bool = False,
) -> list[Restaurant]:
    """Drop duplicates but keep venues without a website.

    Venues without a site are exactly the ones whose address can only be found
    on Facebook, so discarding them was hiding half of the city.
    """
    cleaned: list[Restaurant] = []
    seen: set = set()
    seen_domains: set[str] = set()

    for item in items:
        title_key = " ".join((item.title or "").lower().split())
        if not title_key:
            continue

        website = normalize_url(item.website)
        if website and not is_own_website(website):
            # Facebook/booking page listed as "website": keep it as a Facebook URL.
            facebook = clean_facebook_url(website)
            if facebook and not item.facebook_url:
                item.facebook_url = facebook
            website = ""
        if require_website and not website:
            continue

        domain = registrable_domain(website) if website else ""
        key = (item.place_id or "").strip() or (
            title_key,
            clean_whitespace(item.address).lower(),
        )
        if key in seen:
            continue
        if domain and domain in seen_domains:
            continue
        seen.add(key)
        if domain:
            seen_domains.add(domain)

        item.title = clean_whitespace(item.title)
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
        facebook_url=restaurant.facebook_url,
        category=restaurant.category,
        rating=restaurant.rating,
        reviews=restaurant.reviews,
        price=restaurant.price,
        latitude=restaurant.latitude,
        longitude=restaurant.longitude,
        place_id=restaurant.place_id,
        source_query=restaurant.source_query,
    )


def enrich_restaurants(
    settings: Settings,
    restaurants: list[Restaurant],
    *,
    query: str = "",
    use_search: bool = True,
    search_limit: int = 0,
    js_fallback: bool = False,
    facebook_browser: bool = True,
    facebook_limit: int = 60,
    guess: bool = False,
    workers: int | None = None,
) -> tuple[list[ContactResult], RunStats]:
    stats = RunStats(filtered=len(restaurants))
    stats.without_site = sum(1 for restaurant in restaurants if not restaurant.website)
    started = time.time()
    results: list[ContactResult] = [_empty_result(r) for r in restaurants]
    workers = max(1, workers or settings.http_workers)

    # ---- websites --------------------------------------------------------
    with HttpClient(timeout=settings.http_timeout) as http:
        crawler = WebsiteCrawler(http)
        targets = {
            index: restaurant
            for index, restaurant in enumerate(restaurants)
            if restaurant.website
        }
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(crawler.analyze, restaurant.website): index
                for index, restaurant in targets.items()
            }
            for done, future in enumerate(as_completed(futures), start=1):
                index = futures[future]
                restaurant = restaurants[index]
                row = results[index]
                try:
                    site = future.result()
                except Exception as exc:  # noqa: BLE001 - one bad site must not stop the run
                    stats.errors += 1
                    row.note = f"site error: {exc}"[:200]
                    log.debug("crawl failed %s: %s", restaurant.website, exc)
                    continue

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
                    len(targets),
                    restaurant.title[:45],
                    row.primary_email or "-",
                )

        for index, restaurant in enumerate(restaurants):
            if not restaurant.website:
                results[index].note = results[index].note or "нет сайта в Google Maps"

    # ---- search: Facebook snippets + open web ----------------------------
    pending = [index for index, row in enumerate(results) if not row.emails]
    if use_search and pending:
        city = city_from_query(query)
        with HttpClient(timeout=settings.http_timeout) as search_http:
            engine = build_web_search(settings, search_http)
            finder = SocialEmailFinder(engine)
            if not finder.enabled:
                log.info("No search backend available - Facebook/web lookup skipped")
            else:
                auto_budget = max(2, 2 * len(pending))
                budget = search_limit if search_limit > 0 else min(auto_budget, settings.search_max_queries)
                plans: list[tuple[int, list[tuple[str, str]]]] = []
                used = 0
                for index in pending:
                    if used >= budget:
                        break
                    remaining = min(3, budget - used)
                    plan = finder.plan(
                        restaurants[index].title,
                        city,
                        restaurants[index].phone,
                        results[index].facebook_url,
                        max_queries=remaining,
                    )
                    if not plan:
                        continue
                    plans.append((index, plan))
                    used += len(plan)

                all_queries = unique(
                    query_text for _index, plan in plans for _kind, query_text in plan
                )
                log.info(
                    "Search fallback: %d quer(ies) for %d venue(s) without e-mail",
                    len(all_queries),
                    len(plans),
                )
                hits_by_query = engine.search_many(all_queries, num=10)
                for index, plan in plans:
                    row = results[index]
                    found = finder.evaluate(
                        restaurants[index].title,
                        plan,
                        hits_by_query,
                        backend="google",
                        facebook_url=row.facebook_url,
                    )
                    if found.facebook_url and not row.facebook_url:
                        row.facebook_url = found.facebook_url
                        restaurants[index].facebook_url = found.facebook_url
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
                            restaurants[index].title[:40],
                            row.primary_email,
                        )

    # ---- browser stage: JS sites + Facebook pages ------------------------
    if js_fallback or facebook_browser:
        from .playwright_site import PlaywrightSiteRenderer

        try:
            with PlaywrightSiteRenderer(headless=settings.headless) as renderer:
                if js_fallback:
                    js_queue = [
                        index
                        for index, row in enumerate(results)
                        if not row.emails and restaurants[index].website
                    ]
                    if js_queue:
                        log.info("JS render for %d venue(s)", len(js_queue))
                    for index in js_queue:
                        row = results[index]
                        rendered = renderer.analyze(restaurants[index].website)
                        if rendered.emails:
                            row.emails = rendered.emails
                            row.status = STATUS_JS
                            row.sources = ["website (js render)"]
                            stats.from_js += 1
                        if rendered.facebook_url and not row.facebook_url:
                            row.facebook_url = clean_facebook_url(rendered.facebook_url)
                        if rendered.blocked and not row.emails:
                            row.note = "website blocks bots"

                if facebook_browser:
                    fb_queue = [
                        index
                        for index, row in enumerate(results)
                        if not row.emails and row.facebook_url
                    ][: max(0, facebook_limit)]
                    if fb_queue:
                        log.info(
                            "Facebook page render for %d venue(s) (this is slow)",
                            len(fb_queue),
                        )
                    for index in fb_queue:
                        row = results[index]
                        rendered = renderer.analyze(
                            row.facebook_url, follow_contact=False
                        )
                        emails = filter_email_list(rendered.emails)
                        if emails:
                            row.emails = emails
                            row.status = STATUS_FB
                            row.sources = ["facebook (page render)"]
                            stats.from_facebook += 1
                            log.info(
                                "  FB page hit: %s -> %s",
                                restaurants[index].title[:40],
                                row.primary_email,
                            )
        except Exception as exc:  # noqa: BLE001 - playwright missing / browser crash
            log.warning("Browser fallback unavailable: %s", exc)

    # ---- MX-validated pattern guessing (optional) ------------------------
    if guess:
        guesser = EmailGuesser()
        guessed = 0
        for index, restaurant in enumerate(restaurants):
            row = results[index]
            if row.emails or not restaurant.website:
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

    for row in results:
        row.emails = unique(row.emails)
        if not row.emails:
            row.status = STATUS_NOT_FOUND
            stats.not_found += 1
        elif row.status == STATUS_NOT_FOUND:
            row.status = STATUS_SITE
        stats.with_email += 1 if row.emails else 0

    stats.duration = time.time() - started
    return results, stats


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
    search_limit: int = 0,
    js_fallback: bool = False,
    facebook_browser: bool = True,
    facebook_limit: int = 60,
    guess: bool = False,
    categories: list[str] | None = None,
    area: Area | None = None,
    require_website: bool = False,
) -> tuple[list[ContactResult], RunStats]:
    data_dir = out_dir or settings.ensure_data_dir()
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    stats = RunStats()

    if stage in {"all", "maps"}:
        raw = collect_restaurants(
            settings,
            query,
            limit,
            provider,
            categories=categories,
            area=area,
        )
        save_restaurants_json(data_dir / "restaurants_raw.json", raw)
        stats.raw = len(raw)
        restaurants = filter_restaurants(
            raw, limit=limit, require_website=require_website
        )
        save_restaurants_json(data_dir / "restaurants_list.json", restaurants)
        stats.filtered = len(restaurants)
        log.info(
            "Filtered %d -> %d venues (%d without a website kept)",
            stats.raw,
            stats.filtered,
            sum(1 for r in restaurants if not r.website),
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
        facebook_limit=facebook_limit,
        guess=guess,
        workers=workers,
    )
    enrich_stats.raw = stats.raw or stats.filtered

    meta = {
        "query": query,
        "queries": build_search_queries(query, categories, area),
        "area": area,
        "provider": provider or settings.maps_provider,
        "categories": categories or [],
        "stats": enrich_stats.as_dict(),
    }
    save_results_csv(data_dir / "final_contacts.csv", results)
    save_results_json(data_dir / "final_contacts.json", results)
    save_results_xlsx(data_dir / "final_contacts.xlsx", results, meta)
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

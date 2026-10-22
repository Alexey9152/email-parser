"""Pipeline: Maps -> filter -> website crawl -> search/Facebook -> export."""

from __future__ import annotations

import dataclasses
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

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
    SOURCE_FB_PAGE,
    SOURCE_FB_SNIPPET,
    SOURCE_GUESS,
    SOURCE_SEARCH,
    SOURCE_SITE,
    SOURCE_SITE_JS,
    SOURCE_STATUS,
    STATUS_NOT_FOUND,
    STATUS_SITE,
    ContactResult,
    Restaurant,
    is_hiring_email,
)
from .progress import ProgressTracker
from .serp import build_web_search
from .utils import (
    Area,
    city_from_query,
    clean_whitespace,
    normalize_url,
    registrable_domain,
    unique,
)
from .website import WebsiteCrawler

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


def _progress_update(progress: ProgressTracker | None, **values) -> None:
    if progress is not None:
        progress.update(**values)


def _count_found(results: list[ContactResult]) -> int:
    return sum(1 for row in results if row.primary_email)


def _add_emails(row: ContactResult, emails: list[str], source: str) -> list[str]:
    """Merge addresses into a row, keeping the first source of each address."""
    added: list[str] = []
    for raw in emails:
        email = (raw or "").strip().lower()
        if not email or "@" not in email:
            continue
        row.email_sources.setdefault(email, source)
        if email not in row.emails:
            added.append(email)
    if added:
        row.emails = unique(row.emails + added)
        note = _source_note(source)
        if note:
            row.sources = unique(row.sources + [note])
    return added


def _source_note(source: str) -> str:
    return {
        SOURCE_SITE: "website",
        SOURCE_SITE_JS: "website (js render)",
        SOURCE_FB_PAGE: "facebook (page render)",
        SOURCE_FB_SNIPPET: "facebook (search snippet)",
        SOURCE_SEARCH: "web search",
        SOURCE_GUESS: "pattern guess (MX ok)",
    }.get(source, "")


def _needs_better_email(row: ContactResult) -> bool:
    """A generic info@ is not enough: a manager/hiring address is preferred."""
    return not row.emails or not any(is_hiring_email(email) for email in row.emails)


def _facebook_page_matches(page_url: str, emails: list[str]) -> bool:
    """Check that the local parts look like they belong to *this* page.

    Facebook login interstitials and redirects sometimes render another page;
    if the slug/title tokens are nowhere in the addresses, the addresses are
    dropped instead of being written to the report.
    """
    slug = urlsplit(page_url or "").path.strip("/").split("/")[0].lower()
    if not slug or slug.isdigit():
        # Numeric page ids carry no name hints at all.
        return True
    tokens = [token for token in re.split(r"[^a-z0-9]+", slug) if len(token) >= 3]
    if not tokens:
        return True
    joined = " ".join(emails).lower()
    return any(token in joined for token in tokens)


def _facebook_email_query(title: str, city: str) -> list[tuple[str, str]]:
    location = f' "{clean_whitespace(city)}"' if city else ""
    return [
        (
            "facebook",
            f'site:facebook.com "{clean_whitespace(title)}"{location} email',
        )
    ]


def _default_source(row: ContactResult) -> str:
    notes = " ".join(row.sources).lower()
    if "facebook" in notes:
        return SOURCE_FB_PAGE
    if "js render" in notes:
        return SOURCE_SITE_JS
    if row.website:
        return SOURCE_SITE
    return SOURCE_SEARCH


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
    progress: ProgressTracker | None = None,
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
        _progress_update(
            progress,
            stage="websites",
            message="Проверяем сайты и ищем ссылки на Facebook",
            venues_total=len(restaurants),
            emails_found=0,
            sites_total=len(targets),
            sites_done=0,
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    crawler.analyze, restaurant.website, restaurant.title
                ): index
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
                    _progress_update(progress, sites_done=done)
                    continue

                if site.emails:
                    _add_emails(row, site.emails, SOURCE_SITE)
                if site.facebook_url:
                    row.facebook_url = clean_facebook_url(site.facebook_url)
                    restaurant.facebook_url = row.facebook_url
                if site.blocked:
                    row.note = "website blocks bots"
                if site.error and not row.emails:
                    row.note = site.error

                _progress_update(
                    progress, sites_done=done, emails_found=_count_found(results)
                )
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
        _progress_update(
            progress,
            sites_done=len(targets),
            emails_found=_count_found(results),
            message="Сайты проверены, ищем дополнительные адреса",
        )

    # ---- search: Facebook snippets + open web ----------------------------
    # Search not only venues without any address: an info@ on the website is
    # often a reservations inbox, while the manager's address is on Facebook.
    pending = [index for index, row in enumerate(results) if not row.emails]
    weak = [
        index
        for index, row in enumerate(results)
        if row.emails
        and _needs_better_email(row)
        and (not row.facebook_url or not facebook_browser)
    ]
    if use_search and (pending or weak):
        city = city_from_query(query)
        with HttpClient(timeout=settings.http_timeout) as search_http:
            engine = build_web_search(settings, search_http)
            finder = SocialEmailFinder(engine)
            if not finder.enabled:
                log.info("No search backend available - Facebook/web lookup skipped")
            else:
                auto_budget = 2 * len(pending) + len(weak)
                budget = (
                    search_limit
                    if search_limit > 0
                    else min(auto_budget, settings.search_max_queries)
                )
                plans: list[tuple[int, list[tuple[str, str]]]] = []
                used = 0
                for index in pending:
                    if used >= budget:
                        break
                    remaining = min(2, budget - used)
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
                for index in weak:
                    if used >= budget:
                        break
                    plan = _facebook_email_query(restaurants[index].title, city)
                    plans.append((index, plan))
                    used += len(plan)

                all_queries = unique(
                    query_text for _index, plan in plans for _kind, query_text in plan
                )
                _progress_update(
                    progress,
                    stage="search",
                    message=(
                        f"Google ищет адреса: {len(all_queries)} запрос(ов) "
                        f"для {len(plans)} заведений"
                    ),
                    searches_total=len(all_queries),
                    searches_done=0,
                    emails_found=_count_found(results),
                )
                log.info(
                    "Search fallback: %d quer(ies) for %d venue(s) "
                    "(%d without any address, %d with a generic address)",
                    len(all_queries),
                    len(plans),
                    len(pending),
                    len(weak),
                )
                hits_by_query = engine.search_many(all_queries, num=10)
                _progress_update(
                    progress,
                    searches_done=len(all_queries),
                    emails_found=_count_found(results),
                )
                for index, plan in plans:
                    row = results[index]
                    found = finder.evaluate(
                        restaurants[index].title,
                        plan,
                        hits_by_query,
                        backend="google",
                        facebook_url=row.facebook_url,
                        restaurant_website=restaurants[index].website,
                    )
                    if found.facebook_url and not row.facebook_url:
                        row.facebook_url = found.facebook_url
                        restaurants[index].facebook_url = found.facebook_url
                    if found.emails:
                        source = (
                            SOURCE_FB_SNIPPET
                            if found.source == "facebook"
                            else SOURCE_SEARCH
                        )
                        added = _add_emails(row, found.emails, source)
                        if added:
                            log.info(
                                "  %s hit: %s -> %s",
                                found.source,
                                restaurants[index].title[:40],
                                row.primary_email,
                            )
                    elif found.unreliable and not row.emails:
                        # Snippets listed other venues; the browser stage should
                        # open this page and read the real contact block.
                        row.note = (
                            "search snippets belonged to other venues; "
                            "checking the page in a browser"
                        )
                _progress_update(progress, emails_found=_count_found(results))

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
                    _progress_update(
                        progress,
                        stage="js",
                        message="Рендерим сайты в Chromium",
                        js_total=len(js_queue),
                        js_done=0,
                        emails_found=_count_found(results),
                    )
                    if js_queue:
                        log.info("JS render for %d venue(s)", len(js_queue))
                    for done, index in enumerate(js_queue, start=1):
                        row = results[index]
                        rendered = renderer.analyze(restaurants[index].website)
                        if rendered.emails:
                            _add_emails(row, rendered.emails, SOURCE_SITE_JS)
                        if rendered.facebook_url and not row.facebook_url:
                            row.facebook_url = clean_facebook_url(
                                rendered.facebook_url
                            )
                            restaurants[index].facebook_url = row.facebook_url
                        if rendered.blocked and not row.emails:
                            row.note = "website blocks bots"
                        _progress_update(
                            progress,
                            js_done=done,
                            emails_found=_count_found(results),
                        )

                if facebook_browser:
                    fb_candidates = [
                        index
                        for index, row in enumerate(results)
                        if row.facebook_url
                        and (
                            _needs_better_email(row)
                            # A snippet address is kept, but the page render is
                            # more trustworthy; never skip a page because a weak
                            # snippet already produced an address.
                            or not row.emails
                        )
                    ]
                    # No address at all first, then generic info@ rows.
                    fb_candidates.sort(
                        key=lambda index: (
                            bool(results[index].emails),
                            len(results[index].emails),
                            index,
                        )
                    )
                    fb_queue = fb_candidates[: max(0, facebook_limit)]
                    _progress_update(
                        progress,
                        stage="facebook",
                        message="Открываем страницы Facebook (это медленно)",
                        fb_total=len(fb_queue),
                        fb_done=0,
                        emails_found=_count_found(results),
                    )
                    if fb_queue:
                        log.info(
                            "Facebook page render for %d venue(s) (this is slow)",
                            len(fb_queue),
                        )
                    for done, index in enumerate(fb_queue, start=1):
                        row = results[index]
                        page_url = row.facebook_url
                        rendered = renderer.analyze(page_url, follow_contact=False)
                        # Facebook often shows a login interstitial on the first
                        # hit; give a useful page one retry before giving up.
                        if (rendered.blocked or not rendered.emails) and _needs_better_email(row):
                            time.sleep(1.5)
                            retry = renderer.analyze(page_url, follow_contact=False)
                            if retry.emails and len(retry.emails) >= len(rendered.emails):
                                rendered = retry
                            elif not rendered.emails:
                                rendered = retry
                        emails = filter_email_list(rendered.emails)
                        if emails and not _facebook_page_matches(page_url, emails):
                            row.note = (
                                "Facebook page e-mail ignored: page identity unverified"
                            )
                            emails = []
                        added = _add_emails(row, emails, SOURCE_FB_PAGE)
                        if added:
                            log.info(
                                "  FB page hit: %s -> %s",
                                restaurants[index].title[:40],
                                row.primary_email,
                            )
                        elif rendered.blocked:
                            row.note = row.note or "Facebook page requires login"
                        elif not emails:
                            row.note = row.note or "Facebook page has no public e-mail"
                        _progress_update(
                            progress,
                            fb_done=done,
                            emails_found=_count_found(results),
                        )
        except Exception as exc:  # noqa: BLE001 - playwright missing / browser crash
            log.warning("Browser fallback unavailable: %s", exc)

    # ---- MX-validated pattern guessing (optional) ------------------------
    if guess:
        guesser = EmailGuesser()
        guess_queue = [
            index
            for index, restaurant in enumerate(restaurants)
            if not results[index].emails and restaurant.website
        ]
        _progress_update(
            progress,
            stage="guess",
            message="Подбираем info@/contact@ по MX-записям",
            guess_total=len(guess_queue),
            guess_done=0,
            emails_found=_count_found(results),
        )
        guessed = 0
        for done, index in enumerate(guess_queue, start=1):
            restaurant = restaurants[index]
            row = results[index]
            attempt = guesser.guess(restaurant.website)
            if attempt.emails:
                _add_emails(row, attempt.emails, SOURCE_GUESS)
                row.note = (
                    f"unverified; mail exchanger: {', '.join(attempt.mx[:2])}"
                )
                guessed += 1
            else:
                log.debug("no guess for %s: %s", restaurant.website, attempt.error)
            _progress_update(
                progress, guess_done=done, emails_found=_count_found(results)
            )
        if guessed:
            log.info("Guessed %d unverified address(es) from MX records", guessed)

    # ---- final ranking / statuses / stats --------------------------------
    for row in results:
        row.emails = unique(row.emails)
        for email in row.emails:
            row.email_sources.setdefault(email, _default_source(row))
        if not row.emails:
            row.status = STATUS_NOT_FOUND
            continue
        source = row.primary_email_source
        row.status = SOURCE_STATUS.get(source, row.status or STATUS_SITE)

    stats.with_email = _count_found(results)
    stats.from_site = sum(
        1 for row in results if row.primary_email_source == SOURCE_SITE
    )
    stats.from_facebook = sum(
        1
        for row in results
        if row.primary_email_source in {SOURCE_FB_PAGE, SOURCE_FB_SNIPPET}
    )
    stats.from_search = sum(
        1 for row in results if row.primary_email_source == SOURCE_SEARCH
    )
    stats.from_js = sum(
        1 for row in results if row.primary_email_source == SOURCE_SITE_JS
    )
    stats.from_guess = sum(
        1 for row in results if row.primary_email_source == SOURCE_GUESS
    )
    stats.not_found = sum(1 for row in results if not row.primary_email)
    stats.duration = time.time() - started

    _progress_update(
        progress,
        emails_found=stats.with_email,
        errors=stats.errors,
        message=(
            f"Найдено {stats.with_email} e-mail из {stats.filtered} заведений"
        ),
    )
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
    progress: ProgressTracker | None = None,
) -> tuple[list[ContactResult], RunStats]:
    data_dir = out_dir or settings.ensure_data_dir()
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    stats = RunStats()

    if stage in {"all", "maps"}:
        _progress_update(
            progress,
            stage="maps",
            message="Собираем заведения в Google Maps",
            emails_found=0,
        )
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
        _progress_update(progress, venues_total=len(restaurants))
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
        _progress_update(progress, venues_total=len(restaurants))
        log.info("Loaded %d venues from %s", len(restaurants), source)

    if stage == "maps":
        _progress_update(
            progress,
            stage="done",
            message=f"Собрано {stats.filtered} заведений",
        )
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
        progress=progress,
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
    _progress_update(
        progress,
        stage="export",
        message="Сохраняем Excel, CSV и JSON",
        emails_found=enrich_stats.with_email,
    )
    save_results_csv(data_dir / "final_contacts.csv", results)
    save_results_json(data_dir / "final_contacts.json", results)
    save_results_xlsx(data_dir / "final_contacts.xlsx", results, meta)
    _progress_update(
        progress,
        stage="done",
        message=(
            f"Готово: {enrich_stats.with_email} e-mail из "
            f"{enrich_stats.filtered} заведений"
        ),
        emails_found=enrich_stats.with_email,
    )
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

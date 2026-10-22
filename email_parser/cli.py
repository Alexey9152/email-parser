"""Command line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .categories import CATEGORY_GROUPS, FOOD_CATEGORIES, parse_categories
from .config import PROJECT_ROOT, Settings
from .pipeline import run
from .utils import parse_bbox

log = logging.getLogger("email_parser")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="email-parser",
        description="Collect food & drink venues from Google Maps and find their e-mails.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python main.py -q "restaurants in Ocean City, MD" --limit 40\n'
            '  python main.py -q "Ocean City, MD" --all-food --limit 300 --provider apify\n'
            '  python main.py --bbox "38.32,-75.10,38.40,-75.03" --all-food --limit 300\n'
            '  python main.py -q "Austin, TX" --categories "ice cream,shawarma,cocktail bar"\n'
            "  python main.py --stage emails --input data/restaurants_list.json --js\n"
            "  python main.py --list-categories\n"
            "  python ui.py   (map-based UI in the browser)\n"
        ),
    )
    parser.add_argument(
        "-q",
        "--query",
        default="",
        help='Maps query or just the location, e.g. "restaurants in Ocean City, MD" or "Ocean City, MD"',
    )
    parser.add_argument("-n", "--limit", type=int, default=40, help="max venues to collect (default: 40)")
    parser.add_argument(
        "--stage",
        choices=("all", "maps", "emails"),
        default="all",
        help="all = maps + emails, maps = collect only, emails = enrich an existing list",
    )
    parser.add_argument(
        "--provider",
        choices=("playwright", "apify", "outscraper"),
        default=None,
        help="Google Maps backend (default: MAPS_PROVIDER from .env)",
    )
    parser.add_argument("--input", type=Path, default=None, help="restaurants_list.json for --stage emails")
    parser.add_argument("--out-dir", type=Path, default=None, help="output directory (default: ./data)")
    parser.add_argument("--workers", type=int, default=None, help="parallel website crawlers (default: 8)")
    parser.add_argument("--timeout", type=float, default=None, help="HTTP timeout per request, seconds")

    # -- coverage ----------------------------------------------------------
    parser.add_argument(
        "--categories",
        default="",
        help='comma-separated categories or group names: "ice cream,shawarma,bars,all"',
    )
    parser.add_argument(
        "--all-food",
        action="store_true",
        help=f"search every food category ({len(FOOD_CATEGORIES)} searches)",
    )
    parser.add_argument(
        "--bbox",
        default="",
        help='map rectangle as "south,west,north,east" (min_lat,min_lng,max_lat,max_lng)',
    )
    parser.add_argument(
        "--only-with-website",
        action="store_true",
        help="keep the old behaviour and discard venues without a website",
    )
    parser.add_argument(
        "--list-categories",
        action="store_true",
        help="print available categories and exit",
    )

    # -- e-mail stages -----------------------------------------------------
    parser.add_argument(
        "--no-search",
        action="store_true",
        help="skip Facebook/web search for venues without an e-mail on the site",
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=0,
        help="max search queries per run (default: 0 = 2 per venue, capped by SEARCH_MAX_QUERIES)",
    )
    parser.add_argument(
        "--search-provider",
        choices=("auto", "apify", "serpapi", "ddg", "none"),
        default=None,
        help="web search backend (auto = Apify Google, then DDG)",
    )
    parser.add_argument("--js", action="store_true", help="render failed sites in headless Chromium (slow)")
    parser.add_argument(
        "--guess",
        action="store_true",
        help="add info@/contact@ for venues without an address when the domain has MX records",
    )
    parser.add_argument(
        "--no-fb-page",
        action="store_true",
        help="do not open Facebook pages in Chromium (they are opened by default)",
    )
    parser.add_argument(
        "--fb-limit",
        type=int,
        default=60,
        help="max Facebook pages to render per run (default: 60)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="show the browser window (debugging)",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv debug")
    return parser


def _print_categories() -> None:
    print("Группы:")
    for name, categories in CATEGORY_GROUPS.items():
        print(f"  {name:12} {', '.join(categories)}")
    print("\nВсе категории:")
    print("  " + ", ".join(FOOD_CATEGORIES))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose > 1 else logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # httpx logs full request URLs, and Apify passes its token as a query param.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if args.list_categories:
        _print_categories()
        return 0

    categories = parse_categories(args.categories)
    if args.all_food:
        for category in FOOD_CATEGORIES:
            if category not in categories:
                categories.append(category)

    try:
        area = parse_bbox(args.bbox)
    except ValueError as exc:
        log.error("%s", exc)
        return 2

    settings = Settings.from_env()
    if args.timeout:
        settings.http_timeout = args.timeout
    if args.workers:
        settings.http_workers = args.workers
    if args.headed:
        settings.headless = False
    if args.out_dir:
        settings.data_dir = args.out_dir if args.out_dir.is_absolute() else PROJECT_ROOT / args.out_dir
    if args.search_provider and args.search_provider != "none":
        settings.search_backend = args.search_provider

    if args.stage in {"all", "maps"}:
        if not args.query and not (categories and area):
            log.error(
                "Need --query (city/location), or --bbox together with --categories/--all-food"
            )
            return 2

    try:
        results, stats = run(
            settings,
            query=args.query,
            limit=args.limit,
            stage=args.stage,
            provider=args.provider,
            input_path=args.input,
            out_dir=args.out_dir,
            workers=args.workers,
            use_search=not args.no_search and args.search_provider != "none",
            search_limit=args.search_limit,
            js_fallback=args.js,
            facebook_browser=not args.no_fb_page,
            facebook_limit=args.fb_limit,
            guess=args.guess,
            categories=categories,
            area=area,
            require_website=args.only_with_website,
        )
    except (KeyboardInterrupt, BrokenPipeError):
        log.warning("interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        log.error("%s", exc)
        if args.verbose > 1:
            raise
        return 1

    if args.stage == "maps":
        print(f"Saved {stats.filtered} venues to {settings.data_dir / 'restaurants_list.json'}")
        return 0

    print(f"\nResults: {stats.with_email} e-mail(s) found out of {stats.filtered} venues")
    print(f"  without website : {stats.without_site}")
    print(f"  on website      : {stats.from_site}")
    print(f"  on Facebook     : {stats.from_facebook}")
    print(f"  web search      : {stats.from_search}")
    print(f"  JS render       : {stats.from_js}")
    print(f"  guessed         : {stats.from_guess}")
    print(f"  not found       : {stats.not_found}")
    print(f"Excel: {settings.data_dir / 'final_contacts.xlsx'}")
    print(f"CSV  : {settings.data_dir / 'final_contacts.csv'}")
    return 0 if results else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

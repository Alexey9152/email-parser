"""Command line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import PROJECT_ROOT, Settings
from .pipeline import run

log = logging.getLogger("email_parser")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="email-parser",
        description="Collect restaurant contacts from Google Maps and their websites.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python main.py -q "restaurants in Ocean City, MD" --limit 40\n'
            '  python main.py -q "cafes in Austin, TX" --provider apify --limit 200\n'
            "  python main.py --stage emails --input data/restaurants_list.json --js\n"
        ),
    )
    parser.add_argument("-q", "--query", default="", help='Maps query, e.g. "restaurants in Ocean City, MD"')
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

    parser.add_argument(
        "--no-search",
        action="store_true",
        help="skip Facebook/web search for venues without an e-mail on the site",
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=30,
        help="max venues looked up in search engines per run (default: 30)",
    )
    parser.add_argument("--js", action="store_true", help="render failed sites in headless Chromium (slow)")
    parser.add_argument(
        "--guess",
        action="store_true",
        help="add info@/contact@ for venues with no address when the domain has MX records",
    )
    parser.add_argument("--fb-browser", action="store_true", help="open Facebook pages in Chromium (rarely works)")
    parser.add_argument("--headed", action="store_true", help="show the browser window (debugging)")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv debug")
    return parser


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

    settings = Settings.from_env()
    if args.timeout:
        settings.http_timeout = args.timeout
    if args.workers:
        settings.http_workers = args.workers
    if args.headed:
        settings.headless = False
    if args.out_dir:
        settings.data_dir = args.out_dir if args.out_dir.is_absolute() else PROJECT_ROOT / args.out_dir

    if args.stage in {"all", "maps"} and not args.query:
        log.error("--query is required unless --stage emails is used")
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
            use_search=not args.no_search,
            search_limit=args.search_limit,
            js_fallback=args.js,
            facebook_browser=args.fb_browser,
            guess=args.guess,
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
    print(f"  on website   : {stats.from_site}")
    print(f"  on Facebook  : {stats.from_facebook}")
    print(f"  web search   : {stats.from_search}")
    print(f"  JS render    : {stats.from_js}")
    print(f"  guessed      : {stats.from_guess}")
    print(f"  not found    : {stats.not_found}")
    print(f"CSV: {settings.data_dir / 'final_contacts.csv'}")
    print(f"JSON: {settings.data_dir / 'final_contacts.json'}")
    return 0 if results else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

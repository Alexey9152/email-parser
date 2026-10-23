"""Create a reusable Facebook browser session.

Run this once on your own machine, log in to Facebook in the opened window,
then close it.  The persistent Chromium profile keeps the cookies, so later
pipeline runs can read contact blocks on pages that show a login or 18+ gate to
anonymous visitors.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import PROJECT_ROOT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="email-parser facebook-login",
        description="Open Facebook in Chromium and save the login for later runs.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "facebook-profile",
        help="persistent Chromium profile (default: data/facebook-profile)",
    )
    parser.add_argument(
        "--url",
        default="https://www.facebook.com/",
        help="page to open for logging in (default: facebook.com)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile = args.profile_dir.expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright is not installed. Run: "
            ".venv\\Scripts\\python.exe -m playwright install chromium",
            file=sys.stderr,
        )
        return 1

    print(f"Opening Facebook with profile: {profile}")
    print("Log in, then return to this terminal and press Enter to save the session.")
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            locale="en-US",
            viewport={"width": 1366, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
        finally:
            context.close()

    print()
    print("Session saved.")
    print("The default profile is auto-detected on the next run.")
    print("For a custom location, add this to .env:")
    print(f"FACEBOOK_PROFILE_DIR={profile}")
    print("Then run the usual email-parser command.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""MX-validated e-mail pattern guessing.

When a venue publishes nothing, the domain itself is still a strong signal: if
it has MX records it accepts mail, and small businesses overwhelmingly use a
handful of aliases. Guesses are always reported as unverified.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .models import STATUS_GUESS
from .utils import registrable_domain

log = logging.getLogger(__name__)

DEFAULT_ALIASES = ("info", "contact")


@dataclass(slots=True)
class GuessResult:
    emails: list[str] = field(default_factory=list)
    domain: str = ""
    mx: list[str] = field(default_factory=list)
    verified: bool = False
    error: str = ""

    @property
    def found(self) -> bool:
        return bool(self.emails)


def dns_mx_lookup(domain: str, timeout: float = 5.0) -> list[str]:
    """MX hosts for a domain; [] when there are none or dnspython is missing."""
    try:
        import dns.resolver  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - optional dependency
        log.debug("dnspython not installed, MX check skipped")
        return []
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=timeout)
        return sorted(str(record.exchange).rstrip(".") for record in answers)
    except Exception as exc:  # noqa: BLE001 - NXDOMAIN, timeout, no answer
        log.debug("MX lookup failed for %s: %s", domain, exc)
        return []


class EmailGuesser:
    def __init__(self, aliases: tuple[str, ...] = DEFAULT_ALIASES, mx_lookup=dns_mx_lookup):
        self.aliases = aliases
        self._mx_lookup = mx_lookup

    def guess(self, website: str) -> GuessResult:
        domain = registrable_domain(website)
        if not domain or "." not in domain:
            return GuessResult(error="no domain")

        mx = self._mx_lookup(domain)
        if not mx:
            return GuessResult(domain=domain, error="domain has no MX records")

        aliases = self.aliases or DEFAULT_ALIASES
        return GuessResult(
            emails=[f"{alias}@{domain}" for alias in aliases],
            domain=domain,
            mx=mx,
            verified=True,
        )

    @staticmethod
    def status() -> str:
        return STATUS_GUESS

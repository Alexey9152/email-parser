"""MX-validated address guessing (no network: resolver is injected)."""

from __future__ import annotations

from email_parser.guess import EmailGuesser


def test_no_mx_means_no_guess():
    guesser = EmailGuesser(mx_lookup=lambda domain: [])
    result = guesser.guess("https://joesdiner.com")
    assert not result.found
    assert "MX" in result.error


def test_guess_with_mx_records():
    guesser = EmailGuesser(mx_lookup=lambda domain: ["mx1.mail-host.com"])
    result = guesser.guess("https://www.joesdiner.com/menu")
    assert result.emails == ["info@joesdiner.com", "contact@joesdiner.com"]
    assert result.domain == "joesdiner.com"
    assert result.verified


def test_custom_aliases_and_bad_urls():
    guesser = EmailGuesser(aliases=("hello",), mx_lookup=lambda domain: ["mx.example.com"])
    assert guesser.guess("https://joesdiner.com").emails == ["hello@joesdiner.com"]
    assert not guesser.guess("").found

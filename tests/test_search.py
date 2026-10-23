"""Search backends: HTML parsing, relevance and e-mail filtering (no network)."""

from __future__ import annotations

from email_parser.facebook import SocialEmailFinder
from email_parser.serp import (
    BackendBlocked,
    DDGSBackend,
    DuckDuckGoBackend,
    SearchHit,
    WebSearch,
    unwrap_ddg_link,
)

DDG_HTML = """
<html><body>
<div class="result results_links">
  <h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.facebook.com%2Fjoesdiner">Joe's Diner | Facebook</a></h2>
  <a class="result__snippet">Write to us: <b>joes@gmail.com</b> or call 555-0101.</a>
</div>
<div class="result results_links">
  <h2><a class="result__a" href="https://joesdiner.com/contact">Contact - Joe's Diner</a></h2>
  <a class="result__snippet">support@yelp.com is not us.</a>
</div>
</body></html>
"""

DDG_LITE_HTML = """
<html><body><table>
<tr><td><a class="result-link" href="https://joesdiner.com/contact">Contact Joe's Diner</a></td></tr>
<tr><td class="result-snippet">Reach us at hello@joesdiner.com</td></tr>
</table></body></html>
"""


def test_ddg_parse_and_link_unwrap():
    hits = DuckDuckGoBackend._parse(DDG_HTML)
    assert len(hits) == 2
    assert hits[0].link == "https://www.facebook.com/joesdiner"
    assert "joes@gmail.com" in hits[0].snippet


def test_ddg_lite_parse():
    hits = DuckDuckGoBackend._parse(DDG_LITE_HTML)
    assert hits[0].link == "https://joesdiner.com/contact"
    assert "hello@joesdiner.com" in hits[0].snippet


def test_unwrap_plain_and_redirect_links():
    assert unwrap_ddg_link("https://a.com/x") == "https://a.com/x"
    assert (
        unwrap_ddg_link("//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%2Fp%3Fx%3D1")
        == "https://a.com/p?x=1"
    )


def test_ddgs_rows_are_converted_without_network():
    rows = [
        {
            "title": "OC Uptown Eats | Ocean City MD - Facebook",
            "href": "https://www.facebook.com/ocuptowneats/",
            "body": "Write to alluse@aol.com or call us.",
        },
        {"title": "", "href": "", "body": ""},
    ]
    hits = DDGSBackend._to_hits(rows, num=10)
    assert len(hits) == 1
    assert hits[0].link == "https://www.facebook.com/ocuptowneats/"
    assert "alluse@aol.com" in hits[0].snippet


def test_email_filtering_drops_directories():
    hits = [
        SearchHit(title="Joe's Diner", snippet="joes@gmail.com"),
        SearchHit(title="Joe's Diner", snippet="support@yelp.com noreply@facebook.com"),
    ]
    assert SocialEmailFinder._emails(hits) == ["joes@gmail.com"]


def test_relevance_requires_title_words():
    hit = SearchHit(title="Joe's Diner - Ocean City", snippet="Breakfast all day")
    other = SearchHit(title="Best Pizza Awards 2026", snippet="unrelated")
    assert SocialEmailFinder._relevant(hit, "Joe's Diner")
    assert not SocialEmailFinder._relevant(other, "Joe's Diner")


def test_chain_falls_through_empty_backend():
    class Empty:
        name = "empty"
        calls = 0

        def search(self, query, num=20):
            self.calls += 1
            return []

    class Good:
        name = "good"

        def search(self, query, num=20):
            return [SearchHit(title="x")]

    empty, good = Empty(), Good()
    chain = WebSearch([empty, good], delay=0, cooldown=60)
    assert chain.search("q")[1] == "good"
    chain.search("q")
    assert empty.calls == 2
    # Empty results are normal for exact venue queries and must not disable a
    # backend; only an explicit block/rate-limit does that.
    chain.search("q")
    assert empty.calls == 3
    assert chain.search("q")[1] == "good"
    assert empty.calls == 4


def test_chain_marks_blocked_backend():
    class Blocked:
        name = "blocked"
        calls = 0

        def search(self, query, num=20):
            self.calls += 1
            raise BackendBlocked("rate limit")

    blocked = Blocked()
    chain = WebSearch([blocked], delay=0, cooldown=60)
    assert chain.search("q") == ([], "")
    assert blocked.calls == 1
    assert not chain.available
    assert chain.search("q") == ([], "")  # skipped, not retried
    assert blocked.calls == 1


def test_finder_respects_query_budget():
    class Empty:
        name = "empty"
        enabled = True
        available = True

        def search(self, query, num=20):
            return [], ""

    finder = SocialEmailFinder(Empty())
    result = finder.find("Joe's Diner", "Ocean City, MD", max_queries=1)
    assert len(result.queries) == 1

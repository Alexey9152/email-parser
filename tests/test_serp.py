"""Search backend parsing / batching."""

from __future__ import annotations

from email_parser.serp import ApifySearchBackend, SearchHit, WebSearch, unwrap_ddg_link


def test_apify_item_parsing():
    item = {
        "organicResults": [
            {
                "title": "Coastal Salt | Ocean City MD",
                "url": "https://www.facebook.com/CoastalSaltOC/",
                "description": "Breakfast, lunch and dinner.",
            },
            {"title": "", "url": "", "description": ""},
        ]
    }
    hits = ApifySearchBackend._parse_item(item, num=10)
    assert len(hits) == 1
    assert hits[0].link == "https://www.facebook.com/CoastalSaltOC/"


class BatchBackend:
    name = "batch"

    def __init__(self):
        self.calls: list[list[str]] = []

    def search(self, query: str, num: int = 20) -> list[SearchHit]:  # pragma: no cover
        return []

    def search_many(self, queries: list[str], num: int = 10) -> dict[str, list[SearchHit]]:
        self.calls.append(list(queries))
        return {queries[0]: [SearchHit(title="hit", link="https://example.com")]}


def test_web_search_batches_and_skips_resolved_queries():
    backend = BatchBackend()
    engine = WebSearch([backend], delay=0)
    found = engine.search_many(["a", "b"], num=5)
    assert list(found) == ["a"]
    assert backend.calls == [["a", "b"]]


def test_unwrap_ddg_link():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fmenu"
    assert unwrap_ddg_link(wrapped) == "https://example.com/menu"
    assert unwrap_ddg_link("https://example.com") == "https://example.com"

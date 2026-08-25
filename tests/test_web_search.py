"""
Search backend fallback tests — offline, no network.

The bug these exist to prevent: every backend failed, the module returned the
same "找不到相關搜尋結果" string it returns for a genuinely empty query, and the
agent told users their question had no answer when in fact search was down.

    python -m pytest tests/test_web_search.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_core.integrations import web_search as ws  # noqa: E402

HIT = {"title": "南亞科", "body": "股價 510", "href": "https://example.com/2408"}


class _FakeDDGS:
    """Stands in for ddgs.DDGS. `plan` maps backend name -> results or Exception."""

    plan: dict = {}
    tried: list = []
    kwargs_seen: dict = {}

    def text(self, query, max_results=5, backend=None, **kwargs):
        type(self).tried.append(backend)
        type(self).kwargs_seen = kwargs
        outcome = type(self).plan.get(backend, [])
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def fake(monkeypatch):
    _FakeDDGS.plan, _FakeDDGS.tried, _FakeDDGS.kwargs_seen = {}, [], {}
    monkeypatch.setattr(ws, "DDGS", _FakeDDGS)
    monkeypatch.setattr(ws, "BACKENDS", ["google", "brave", "bing"])
    return _FakeDDGS


def test_first_working_backend_wins(fake):
    fake.plan = {"google": [HIT]}
    assert "南亞科" in ws.web_search("南亞科 股價")
    assert fake.tried == ["google"]


def test_falls_through_a_ratelimited_backend(fake):
    fake.plan = {"google": RuntimeError("202 Ratelimit"), "brave": [HIT]}
    assert "南亞科" in ws.web_search("南亞科 股價")
    assert fake.tried == ["google", "brave"]


def test_falls_through_a_backend_that_answers_empty(fake):
    fake.plan = {"google": [], "brave": [], "bing": [HIT]}
    assert "南亞科" in ws.web_search("南亞科 股價")
    assert fake.tried == ["google", "brave", "bing"]


def test_every_backend_erroring_is_reported_as_broken_not_empty(fake):
    fake.plan = {b: RuntimeError("202 Ratelimit") for b in ["google", "brave", "bing"]}
    out = ws.web_search("南亞科 股價")
    assert out == ws.SEARCH_UNAVAILABLE
    assert out != ws.NO_RESULTS


def test_a_genuinely_empty_query_is_not_reported_as_broken(fake):
    fake.plan = {b: [] for b in ["google", "brave", "bing"]}
    out = ws.web_search("asdkjhasdkjh")
    assert out == ws.NO_RESULTS
    assert out != ws.SEARCH_UNAVAILABLE


def test_deep_search_makes_the_same_distinction(fake, monkeypatch):
    monkeypatch.setattr(ws, "_fetch_page_content", lambda url, max_chars=0: "page text")

    fake.plan = {b: RuntimeError("down") for b in ["google", "brave", "bing"]}
    assert ws.deep_web_search("q") == ws.SEARCH_UNAVAILABLE

    fake.tried.clear()
    fake.plan = {"google": [HIT]}
    assert "page text" in ws.deep_web_search("q")


def test_the_region_is_passed_to_ddgs(fake):
    # Without it ddgs defaults to us-en, which restricts a Chinese query to
    # US/English pages and is how "原相 股價" ended up on English spam farms.
    fake.plan = {"google": [HIT]}
    ws._search("原相 股價", 3)
    assert fake.kwargs_seen.get("region") == ws.REGION


# --- The bug that made every search return junk ----------------------------
# ddgs does not raise on a backend it cannot serve. `google`, `bing` and
# `yandex` ship disabled and `mullvad_brave` never existed, so naming any of
# them left ddgs with an empty engine list and it silently fell back to `auto`,
# which shuffles all engines and fronts wikipedia/grokipedia. The first
# iteration therefore always "succeeded" with a random engine's output — SEO
# spam included — and the engines that work were never tried.


def test_every_default_backend_actually_exists_in_ddgs():
    from ddgs.engines import ENGINES

    available = set(ENGINES.get("text", {}))
    unusable = [b for b in ws.DEFAULT_BACKENDS if b not in available]
    assert not unusable, (
        f"{unusable} are not servable text backends; ddgs would silently fall "
        f"back to shuffled 'auto'. Available: {sorted(available)}"
    )


def test_unusable_backends_are_filtered_out_of_the_live_list():
    from ddgs.engines import ENGINES

    available = set(ENGINES.get("text", {}))
    assert ws.BACKENDS, "no backend left to search with"
    assert all(b in available for b in ws.BACKENDS)

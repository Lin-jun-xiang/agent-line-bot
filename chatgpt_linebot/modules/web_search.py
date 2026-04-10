"""Lightweight free web search module using DuckDuckGo native backends.

Designed for low-memory environments (≤500 MB RAM).
No API key required.

NOTE: duckduckgo-search v8.x defaults to Bing backend which gets blocked
on cloud IPs (Render, Railway, etc.). We bypass this by calling the native
DuckDuckGo HTML/Lite backends directly — these are much less likely to be
blocked since they hit html.duckduckgo.com / lite.duckduckgo.com.

Search priority:
1. DDG HTML backend  (html.duckduckgo.com)
2. DDG Lite backend  (lite.duckduckgo.com)
3. DDG default/Bing  (fallback, may fail on cloud)
"""

from duckduckgo_search import DDGS


def _format_results(results: list[dict]) -> str:
    """Format a list of {title, body, href} dicts into readable text."""
    if not results:
        return ""
    formatted = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        body = r.get("body", "")
        href = r.get("href", "")
        formatted.append(f"{i}. {title}\n{body}\n{href}")
    return "\n\n".join(formatted)


def _search_ddg_html(ddgs: DDGS, query: str, max_results: int) -> list[dict]:
    """Use DDG native HTML backend (html.duckduckgo.com)."""
    try:
        results = ddgs._text_html(keywords=query, max_results=max_results)
        print(f"[DDG-HTML] results count: {len(results)}")
        return results
    except Exception as e:
        print(f"[DDG-HTML] error: {e}")
        return []


def _search_ddg_lite(ddgs: DDGS, query: str, max_results: int) -> list[dict]:
    """Use DDG native Lite backend (lite.duckduckgo.com)."""
    try:
        results = ddgs._text_lite(keywords=query, max_results=max_results)
        print(f"[DDG-Lite] results count: {len(results)}")
        return results
    except Exception as e:
        print(f"[DDG-Lite] error: {e}")
        return []


def _search_ddg_default(ddgs: DDGS, query: str, max_results: int) -> list[dict]:
    """Use DDG default backend (Bing) as last resort."""
    try:
        results = list(ddgs.text(query, region="wt-wt", max_results=max_results))
        print(f"[DDG-Default] results count: {len(results)}")
        return results
    except Exception as e:
        print(f"[DDG-Default] error: {e}")
        return []


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo with automatic backend fallback.

    Priority: DDG HTML → DDG Lite → DDG Default (Bing).

    The HTML and Lite backends hit DuckDuckGo's own servers directly,
    which are far less likely to block cloud server IPs compared to Bing.

    Args:
        query: The search query string.
        max_results: Maximum number of results to return.

    Returns:
        A formatted string containing search results (title + snippet + url).
    """
    with DDGS() as ddgs:
        for name, search_fn in [
            ("DDG-HTML", _search_ddg_html),
            ("DDG-Lite", _search_ddg_lite),
            ("DDG-Default", _search_ddg_default),
        ]:
            results = search_fn(ddgs, query, max_results)
            if results:
                print(f"[web_search] Used backend: {name}")
                return _format_results(results)

    return "找不到相關搜尋結果，所有搜尋引擎皆無法取得資料。"

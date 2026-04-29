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

import random
import re

import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

from config import TAVILY_API_KEY

# Rotate user-agent to reduce blocking
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def _tavily_search(query: str, max_results: int) -> list[dict]:
    """Search using Tavily API (cloud-friendly, requires TAVILY_API_KEY)."""
    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(query=query, max_results=max_results)
        results = []
        for r in response.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "body": r.get("content", ""),
                "href": r.get("url", ""),
            })
        print(f"[Tavily] results count: {len(results)}")
        return results
    except Exception as e:
        print(f"[Tavily] error: {e}")
        return []


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
    # Try Tavily first when API key is available
    if TAVILY_API_KEY:
        results = _tavily_search(query, max_results)
        if results:
            print("[web_search] Used backend: Tavily")
            return _format_results(results)

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


# --------------- Page Content Fetching ---------------
def _fetch_page_content(url: str, max_chars: int = 3000) -> str:
    """Fetch and extract main text content from a URL (free, no API key)."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": random.choice(_USER_AGENTS)},
            timeout=10,
            allow_redirects=True,
        )
        resp.encoding = resp.apparent_encoding or "utf-8"
        if resp.status_code != 200:
            return f"[HTTP {resp.status_code}]"

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove noise elements
        for tag in soup.select(
            "script, style, nav, footer, header, aside, iframe, noscript, form"
        ):
            tag.decompose()

        # Try common article containers first, fall back to body
        article = (
            soup.select_one("article")
            or soup.select_one("div.article-content")
            or soup.select_one("div.entry-content")
            or soup.select_one("main")
            or soup.body
        )
        if not article:
            return "[無法解析網頁內容]"

        text = article.get_text(separator="\n", strip=True)
        # Collapse multiple blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:max_chars]

    except Exception as e:
        return f"[抓取失敗: {e}]"


def _format_deep_results(
    results: list[dict], max_chars_per_page: int = 2000
) -> str:
    """Format search results with full page content scraped from each URL."""
    if not results:
        return ""
    parts = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        href = r.get("href", "")

        print(f"[deep_search] [{i}/{len(results)}] 正在抓取: {href}")
        content = _fetch_page_content(href, max_chars=max_chars_per_page)

        parts.append(
            f"{'=' * 50}\n"
            f"【{i}】{title}\n"
            f"{href}\n"
            f"{'─' * 40}\n"
            f"{content}\n"
        )
    return "\n".join(parts)


def deep_web_search(
    query: str,
    max_results: int = 3,
    max_chars_per_page: int = 2000,
) -> str:
    """Search DDG then fetch full page content from top results. 100% free.

    Same backend fallback as web_search (HTML → Lite → Default),
    but additionally scrapes the actual web pages to get detailed content
    instead of just short snippets.

    Args:
        query: The search query string.
        max_results: Maximum number of pages to fetch (keep small to save time).
        max_chars_per_page: Max characters to extract per page.

    Returns:
        A formatted string with full page content for each search result.
    """
    # Try Tavily first when API key is available
    if TAVILY_API_KEY:
        results = _tavily_search(query, max_results)
        if results:
            print("[deep_web_search] Used backend: Tavily")
            return _format_deep_results(results, max_chars_per_page)

    with DDGS() as ddgs:
        for name, search_fn in [
            ("DDG-HTML", _search_ddg_html),
            ("DDG-Lite", _search_ddg_lite),
            ("DDG-Default", _search_ddg_default),
        ]:
            results = search_fn(ddgs, query, max_results)
            if results:
                print(f"[deep_web_search] Used backend: {name}")
                return _format_deep_results(results, max_chars_per_page)

    return "找不到相關搜尋結果，所有搜尋引擎皆無法取得資料。"

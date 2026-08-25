"""Free web search, no API key required.

Backend history, because it matters for debugging:

`duckduckgo-search` was renamed to `ddgs` and its DuckDuckGo endpoints now answer
`202 Ratelimit` to essentially every server-side caller — the HTML and Lite
"native backends" this module used to prefer included. All three fell through to
the same empty result, so every search silently returned "找不到相關搜尋結果"
and the agent, having no way to tell "the search broke" from "nothing exists",
told users their question had no answer.

So we no longer depend on DuckDuckGo at all. `ddgs` brokers several engines; we
walk them in order and take the first that answers. DuckDuckGo stays in the list
but last, since it is the one that rate-limits.

Set AGENT_SEARCH_BACKENDS to override the order (comma-separated), and
AGENT_SEARCH_REGION to override the locale.

One ddgs behaviour to know about, because it silently destroyed result quality
here: a backend name ddgs does not recognise is not an error. `google`, `bing`
and `yandex` ship with `disabled = True`, and `mullvad_brave` does not exist at
all, so asking for any of them leaves ddgs with an empty engine list and it
falls back to `auto` — which *shuffles* every engine and pushes `wikipedia` and
`grokipedia` to the front. That first "google" attempt therefore always
succeeded with whatever a random engine coughed up (including SEO spam), the
loop returned, and the engines that actually work were never reached. So we
validate names against ddgs' own registry and drop the ones it cannot serve.
"""

import os
import random
import re

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from ddgs.engines import ENGINES

# Ordered by how reliably they answer from a datacentre IP (Render, Railway).
# `auto` is deliberately not used: it shuffles, so the same query gets a
# different engine (and a different quality of answer) every time.
DEFAULT_BACKENDS = ["brave", "duckduckgo", "startpage", "mojeek", "yahoo"]

_REQUESTED = [
    b.strip()
    for b in os.environ.get("AGENT_SEARCH_BACKENDS", ",".join(DEFAULT_BACKENDS)).split(",")
    if b.strip()
]

_AVAILABLE = set(ENGINES.get("text", {}))
BACKENDS = [b for b in _REQUESTED if b in _AVAILABLE]

if _unknown := [b for b in _REQUESTED if b not in _AVAILABLE]:
    # Loud on purpose: passing these through would silently degrade every search
    # to a random `auto` pick, which reads as "the bot answers nonsense".
    print(
        f"[search] ignoring unusable backends {_unknown} — ddgs offers "
        f"{sorted(_AVAILABLE)}"
    )
if not BACKENDS:
    BACKENDS = sorted(_AVAILABLE)
    print(f"[search] no usable backend configured, falling back to {BACKENDS}")

# ddgs defaults to region="us-en", which restricts results to US/English pages
# (`lr=`/`cr=` on the engines that support it). For a Traditional-Chinese query
# that is how "台積電 股價" drifts into English and Vietnamese spam farms.
REGION = os.environ.get("AGENT_SEARCH_REGION", "tw-tzh")

# Distinct from "no results": the agent must be able to tell the user "I could not
# search" rather than "that does not exist".
SEARCH_UNAVAILABLE = (
    "搜尋服務目前無法使用（所有搜尋後端都失敗）。"
    "這是工具的問題，不代表查不到這個東西——請告訴使用者搜尋暫時壞掉，不要說找不到資料。"
)
NO_RESULTS = "搜尋成功，但這個查詢沒有任何結果。"

# Rotate user-agent to reduce blocking when we scrape the result pages.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


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


def _search(query: str, max_results: int) -> tuple[list[dict], bool]:
    """Try each backend in turn.

    Returns (results, reachable). `reachable` is False only when every backend
    errored — that is a broken tool, not an empty result set, and the caller
    must say so differently.
    """
    reachable = False
    for backend in BACKENDS:
        try:
            results = DDGS().text(
                query, region=REGION, max_results=max_results, backend=backend
            )
        except Exception as exc:  # noqa: BLE001 - ratelimit, timeout, parse errors
            print(f"[search:{backend}] {type(exc).__name__}: {exc}")
            continue
        reachable = True
        if results:
            print(f"[search:{backend}] {len(results)} results for {query!r}")
            return list(results), True
        print(f"[search:{backend}] no results")
    return [], reachable


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return title + snippet + url for each hit."""
    results, reachable = _search(query, max_results)
    if results:
        return _format_results(results)
    return NO_RESULTS if reachable else SEARCH_UNAVAILABLE


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


def _format_deep_results(results: list[dict], max_chars_per_page: int = 2000) -> str:
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
    """Search, then scrape the top pages for detail the snippets do not carry.

    Snippets alone are useless for the questions people actually ask a bot —
    a stock price, today's weather — because the number lives in the page, not
    the snippet.
    """
    results, reachable = _search(query, max_results)
    if results:
        return _format_deep_results(results, max_chars_per_page)
    return NO_RESULTS if reachable else SEARCH_UNAVAILABLE

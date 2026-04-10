"""Lightweight free web search module using DuckDuckGo.

Designed for low-memory environments (≤500 MB RAM).
No API key required.
"""

from duckduckgo_search import DDGS


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo and return formatted results.

    Args:
        query: The search query string.
        max_results: Maximum number of results to return.

    Returns:
        A formatted string containing search results (title + snippet + url).
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region="wt-wt", max_results=max_results))
            print(f"Web Search: {results}")

        if not results:
            return "找不到相關搜尋結果。"

        formatted = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            formatted.append(f"{i}. {title}\n{body}\n{href}")

        return "\n\n".join(formatted)

    except Exception as e:
        print(f"Web search error: {e}")
        return f"搜尋時發生錯誤：{e}"

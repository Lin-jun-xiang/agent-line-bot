"""Optional You.com search backend — keyless free tier or authenticated.

Can be enabled via `AGENT_SEARCH_BACKENDS=youcom` in the env. When no
YOUCOM_API_KEY is set, the keyless free profile is used (basic web search,
no API key or credit card needed).

Endpoint the backend calls:
  keyless:  https://api.you.com/mcp?profile=free  (MCP you-search tool)
  authenticated: https://api.you.com/search       (REST Search API)

Set AGENT_SEARCH_BACKENDS=youcom,brave,duckduckgo to try You.com first
and fall through to the free backends.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
import urllib.parse

BASE_URL = "https://api.you.com"

SEARCH_UNAVAILABLE_MSG = (
    "You.com search is currently unavailable (connection or rate-limit error). "
    "The search tool is down — tell the user search is temporarily broken, "
    "do not say the data does not exist."
)


def _mcp_search(query: str, max_results: int) -> list[dict]:
    """Call the You.com MCP you-search tool (keyless free profile)."""
    url = f"{BASE_URL}/mcp?profile=free"
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "you-search",
            "arguments": {"query": query, "count": max_results},
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        print(f"[youcom:mcp] {type(exc).__name__}: {exc}")
        raise

    result = data.get("result", {})
    content = result.get("content", [])
    results = []
    for entry in content:
        text = entry.get("text", "")
        # Parse MCP text content that may contain structured results
        results.append({
            "title": entry.get("title", ""),
            "body": text,
            "href": entry.get("url", ""),
        })
    return results


def _rest_search(query: str, max_results: int, api_key: str) -> list[dict]:
    """Call the You.com REST Search API (authenticated)."""
    params = urllib.parse.urlencode({
        "query": query,
        "num_web_results": max_results,
    })
    url = f"{BASE_URL}/search?{params}"

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        print(f"[youcom:rest] {type(exc).__name__}: {exc}")
        raise

    results = []
    for hit in data.get("web", {}).get("results", []):
        results.append({
            "title": hit.get("title", ""),
            "body": hit.get("description", ""),
            "href": hit.get("url", ""),
        })
    return results


def youcom_search(query: str, max_results: int = 5) -> list[dict]:
    """Search using You.com. Returns a list of {title, body, href} dicts.

    Uses the keyless MCP free profile when YOUCOM_API_KEY is not set,
    and the authenticated REST API when it is.

    Raises on network/API errors so the caller can fall through to
    the next backend.
    """
    api_key = os.environ.get("YOUCOM_API_KEY", "").strip()

    if api_key:
        return _rest_search(query, max_results, api_key)
    else:
        return _mcp_search(query, max_results)
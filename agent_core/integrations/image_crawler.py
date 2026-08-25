"""Find an existing photo on the web, by URL.

icrawler's Google backend is dead: Google changed its image-results markup, so
`GoogleParser.parse()` returns None and every crawl ends with
`TypeError: 'NoneType' object is not iterable` and an empty URL list. The
SerpAPI path was no better — `from serpapi import GoogleSearch` was wrapped in a
bare `except: pass`, so a missing package surfaced as
`cannot access local variable 'GoogleSearch'`.

So this module scrapes/queries providers that actually answer without an API
key, in order of coverage, and returns the first URL that verifies as an image
LINE will accept (https, JPEG/PNG, under the 10MB cap).
"""

from __future__ import annotations

import html
import json
import re

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# LINE only renders JPEG/PNG in an image message, and caps it at 10MB.
SENDABLE_TYPES = ("image/jpeg", "image/jpg", "image/png")
MAX_BYTES = 9 * 1024 * 1024

_TIMEOUT = 12


class ImageCrawler:
    """Search the web for a photo matching a text query."""

    def __init__(
        self,
        engine: str = "auto",
        nums: int = 5,
        api_key: str | None = None,
    ) -> None:
        self.engine = engine
        self.nums = nums
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            }
        )

    # ------------------------------------------------------------------ checks
    def _is_img_url(self, url: str) -> bool:
        """Verify a URL really serves a LINE-sendable image."""
        if not url.startswith("https://"):
            return False  # LINE refuses plain http
        try:
            resp = self.session.head(url, timeout=_TIMEOUT, allow_redirects=True)
            # Plenty of CDNs answer 403/405 to HEAD but serve a normal GET.
            if resp.status_code >= 400 or not resp.headers.get("content-type"):
                resp = self.session.get(
                    url, timeout=_TIMEOUT, allow_redirects=True, stream=True
                )
                resp.close()
            if resp.status_code >= 400:
                return False
            content_type = resp.headers.get("content-type", "").split(";")[0].strip()
            if content_type.lower() not in SENDABLE_TYPES:
                return False
            length = resp.headers.get("content-length")
            return not (length and length.isdigit() and int(length) > MAX_BYTES)
        except requests.RequestException:
            return False

    # --------------------------------------------------------------- providers
    def _bing(self, search_query: str) -> list[str]:
        """Scrape Bing image search — widest coverage, no key needed."""
        resp = self.session.get(
            "https://www.bing.com/images/search",
            params={"q": search_query, "form": "HDRSC2", "first": 1},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        # Each result tile carries its metadata as HTML-escaped JSON in `m=`.
        urls = []
        for match in re.finditer(r"murl&quot;:&quot;(.*?)&quot;", resp.text):
            url = html.unescape(match.group(1)).replace("\\/", "/")
            if url not in urls:
                urls.append(url)
        return urls

    def _wikimedia(self, search_query: str) -> list[str]:
        """Wikimedia Commons — reliable for landmarks, people and places."""
        resp = self.session.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": f"filetype:bitmap {search_query}",
                "gsrlimit": max(self.nums, 5),
                "gsrnamespace": 6,  # File:
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": 1280,  # ask for a thumbnail, not a 20MB original
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        pages = (resp.json().get("query") or {}).get("pages") or {}
        urls = []
        for page in pages.values():
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("thumburl") or info.get("url")
            if url:
                urls.append(url)
        return urls

    def _openverse(self, search_query: str) -> list[str]:
        """Openverse — openly-licensed images, no key needed."""
        resp = self.session.get(
            "https://api.openverse.org/v1/images/",
            params={"q": search_query, "page_size": max(self.nums, 5)},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return [
            item["url"] for item in resp.json().get("results", []) if item.get("url")
        ]

    def _serpapi(self, search_query: str) -> list[str]:
        """Google Images via SerpAPI. Needs SERPAPI_API_KEY."""
        if not self.api_key:
            return []
        resp = self.session.get(
            "https://serpapi.com/search",
            params={
                "engine": "google",
                "q": search_query,
                "tbm": "isch",
                "api_key": self.api_key,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("images_results") or []
        return [item["original"] for item in results if item.get("original")]

    # ------------------------------------------------------------------ public
    def _providers(self):
        named = {
            "bing": self._bing,
            "wikimedia": self._wikimedia,
            "openverse": self._openverse,
            "serpapi": self._serpapi,
        }
        if self.engine in named:
            return [(self.engine, named[self.engine])]
        # "auto" (and the legacy "icrawler" value): try everything, cheapest first.
        order = ["bing", "wikimedia", "openverse"]
        if self.api_key:
            order.append("serpapi")
        return [(name, named[name]) for name in order]

    def get_url(self, search_query: str) -> str | None:
        """Return one usable image URL for the query, or None."""
        for name, provider in self._providers():
            try:
                urls = provider(search_query)
            except Exception as exc:  # noqa: BLE001 - try the next provider
                print(f"[image_search] {name} failed: {type(exc).__name__}: {exc}")
                continue
            if not urls:
                print(f"[image_search] {name}: no results")
                continue
            for url in urls[: max(self.nums, 5)]:
                if self._is_img_url(url):
                    print(f"[image_search] {name} -> {url}")
                    return url
            print(f"[image_search] {name}: {len(urls)} results, none sendable")
        return None


if __name__ == "__main__":  # manual smoke test: python -m agent_core.integrations.image_crawler
    import sys

    query = " ".join(sys.argv[1:]) or "台北101"
    print(json.dumps({"query": query, "url": ImageCrawler().get_url(query)}, ensure_ascii=False))

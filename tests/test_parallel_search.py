"""Exercise registered botkit tools through the real MCP HTTP client, offline."""

import asyncio
import json

import httpx
import pytest
from mcp import types

from agent_core import settings, tools
from agent_core.integrations import parallel_search as parallel


async def dispatch(server, name, arguments):
    response = await server.request_handlers[types.CallToolRequest](
        types.CallToolRequest(params=types.CallToolRequestParams(
            name=name, arguments=arguments
        ))
    )
    return response.root.content[0].text


@pytest.fixture
def remote(monkeypatch):
    requests = []
    outcome = {"content": [{"type": "text", "text": "Page title\nuseful excerpt\nhttps://example.com"}]}

    async def respond(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            result = {
                "protocolVersion": payload["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif payload["method"] == "tools/list":
            result = {"tools": [
                {"name": name, "inputSchema": {"type": "object"}}
                for name in ("web_search", "web_fetch")
            ]}
        else:
            assert payload["method"] == "tools/call"
            result = outcome
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": payload["id"], "result": result
        })

    original = parallel.streamablehttp_client

    def transport(url, **kwargs):
        # Keep the contributed URL/headers/timeouts, replacing only the network.
        def client_factory(headers=None, timeout=None, auth=None):
            return httpx.AsyncClient(
                headers=headers, timeout=timeout, auth=auth,
                transport=httpx.MockTransport(respond),
            )
        return original(url, httpx_client_factory=client_factory, **kwargs)

    monkeypatch.setattr(parallel, "streamablehttp_client", transport)
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "parallel")
    return requests, outcome


def test_registered_search_and_fetch_share_anonymous_session(remote, monkeypatch):
    requests, _ = remote
    monkeypatch.setenv("PARALLEL_API_KEY", "must-not-be-used")
    server = tools.build_tool_server([], session_id="private-line-user")['instance']

    async def run():
        assert "useful excerpt" in await dispatch(server, "web_search", {"query": "Python docs"})
        assert "useful excerpt" in await dispatch(server, "fetch_url", {"url": "https://example.com"})
    asyncio.run(run())

    for request, _ in requests:
        assert str(request.url) == parallel.ENDPOINT
        assert request.headers["user-agent"] == parallel.USER_AGENT
        assert "authorization" not in request.headers
    calls = [p["params"] for _, p in requests if p["method"] == "tools/call"]
    assert calls[0]["name"] == "web_search"
    assert calls[0]["arguments"]["objective"] == "Python docs"
    assert calls[0]["arguments"]["search_queries"] == ["Python docs"]
    assert calls[1]["name"] == "web_fetch"
    assert calls[1]["arguments"]["urls"] == ["https://example.com"]
    session = calls[0]["arguments"]["session_id"]
    assert len(session) == 64 and "private-line-user" not in session
    assert calls[1]["arguments"]["session_id"] == session


@pytest.mark.parametrize("name,args", [
    ("web_search", {"query": "q"}), ("fetch_url", {"url": "https://example.com"})
])
def test_remote_errors_are_not_presented_as_success(remote, name, args):
    _, outcome = remote
    outcome["isError"] = True
    outcome["content"][0]["text"] = "Search temporarily unavailable"
    server = tools.build_tool_server([])['instance']
    text = asyncio.run(dispatch(server, name, args))
    assert text.startswith(f"{name} failed:")
    assert "Search temporarily unavailable" in text


def test_default_keeps_existing_search(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "ddgs")
    monkeypatch.setattr(
        "agent_core.integrations.web_search.deep_web_search",
        lambda query, **kwargs: f"ddgs result: {query}",
    )
    server = tools.build_tool_server([])['instance']
    assert asyncio.run(dispatch(server, "web_search", {"query": "q"})) == "ddgs result: q"


def test_unknown_provider_is_actionable(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "typo")
    server = tools.build_tool_server([])['instance']
    assert "Unknown AGENT_SEARCH_PROVIDER: typo" in asyncio.run(
        dispatch(server, "web_search", {"query": "q"})
    )


def test_cancelled_call_closes_transport(monkeypatch):
    from contextlib import asynccontextmanager

    closed = []
    started = asyncio.Event()

    @asynccontextmanager
    async def blocked_transport(*args, **kwargs):
        try:
            started.set()
            await asyncio.Future()
            yield
        finally:
            closed.append(True)

    monkeypatch.setattr(parallel, "streamablehttp_client", blocked_transport)

    async def run():
        task = asyncio.create_task(parallel.call_tool("web_search", {}, "session"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert closed == [True]

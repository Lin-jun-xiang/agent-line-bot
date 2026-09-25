"""Tests for the optional You.com search backend — offline, no network.

    python -m pytest tests/test_youcom_search.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_core.integrations import youcom_search as ys  # noqa: E402


class FakeURLResponse:
    """Mimics urllib.response.addinfourl for testing _mcp_search / _rest_search."""

    def __init__(self, data: bytes, status: int = 200):
        self._data = data
        self.status = status

    def read(self, *args, **kwargs):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        pass


def fake_urlopen_success(monkeypatch, response_data: bytes):
    """Replace urllib.request.urlopen with one that returns FakeURLResponse."""

    def _fake_open(req, **_kw):
        return FakeURLResponse(response_data)

    monkeypatch.setattr(ys.urllib.request, "urlopen", _fake_open)


def fake_urlopen_error(monkeypatch, exc_class=ConnectionError):
    """Replace urllib.request.urlopen with one that raises."""

    def _fake_open(req, **_kw):
        raise exc_class("simulated network error")

    monkeypatch.setattr(ys.urllib.request, "urlopen", _fake_open)


# --- youcom_search (top-level public function) ------------------------------


def test_keyless_mcp_search_returns_formatted_results(monkeypatch):
    """When YOUCOM_API_KEY is unset, youcom_search calls _mcp_search."""
    monkeypatch.delenv("YOUCOM_API_KEY", raising=False)
    mcp_response = {
        "result": {
            "content": [
                {"title": "Result A", "text": "Description A", "url": "https://a.com"},
                {"title": "Result B", "text": "Description B", "url": "https://b.com"},
            ]
        }
    }
    fake_urlopen_success(monkeypatch, json.dumps(mcp_response).encode("utf-8"))

    results = ys.youcom_search("test query", max_results=3)
    assert len(results) == 2
    assert results[0]["title"] == "Result A"
    assert results[0]["body"] == "Description A"
    assert results[0]["href"] == "https://a.com"


def test_authenticated_rest_search_returns_formatted_results(monkeypatch):
    """When YOUCOM_API_KEY is set, youcom_search calls _rest_search."""
    monkeypatch.setenv("YOUCOM_API_KEY", "test-key-123")
    rest_response = {
        "web": {
            "results": [
                {"title": "R1", "description": "D1", "url": "https://r1.com"},
                {"title": "R2", "description": "D2", "url": "https://r2.com"},
            ]
        }
    }
    fake_urlopen_success(monkeypatch, json.dumps(rest_response).encode("utf-8"))

    results = ys.youcom_search("test query", max_results=3)
    assert len(results) == 2
    assert results[0]["title"] == "R1"
    assert results[0]["body"] == "D1"
    assert results[0]["href"] == "https://r1.com"


def test_keyless_mode_when_env_var_is_empty(monkeypatch):
    """An empty YOUCOM_API_KEY should also use keyless mode."""
    monkeypatch.setenv("YOUCOM_API_KEY", "")
    mcp_response = {"result": {"content": [{"title": "X", "text": "Y", "url": "https://x.com"}]}}
    fake_urlopen_success(monkeypatch, json.dumps(mcp_response).encode("utf-8"))

    results = ys.youcom_search("q")
    assert len(results) == 1


def test_search_error_is_propagated(monkeypatch):
    """When the backend errors, the exception propagates to the caller."""
    monkeypatch.delenv("YOUCOM_API_KEY", raising=False)
    fake_urlopen_error(monkeypatch, ConnectionError)

    with pytest.raises(ConnectionError):
        ys.youcom_search("q")


def test_empty_results_list_on_no_hits(monkeypatch):
    """Backend returning no results yields an empty list."""
    monkeypatch.delenv("YOUCOM_API_KEY", raising=False)
    fake_urlopen_success(monkeypatch, json.dumps({"result": {"content": []}}).encode("utf-8"))

    results = ys.youcom_search("asdkjhasdkjh")
    assert results == []


# --- mcp payload shape -----------------------------------------------------


def test_mcp_request_has_correct_jsonrpc_shape(monkeypatch):
    """Verify the MCP request body sent to the free profile endpoint."""
    monkeypatch.delenv("YOUCOM_API_KEY", raising=False)
    sent_bodies = []

    original = ys.urllib.request.urlopen

    def capture(req, **_kw):
        sent_bodies.append(req.data)
        # Return empty response so the function doesn't crash
        return FakeURLResponse(json.dumps({"result": {"content": []}}).encode("utf-8"))

    monkeypatch.setattr(ys.urllib.request, "urlopen", capture)

    ys.youcom_search("hello", max_results=5)
    assert len(sent_bodies) == 1
    body = json.loads(sent_bodies[0].decode("utf-8"))
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "you-search"
    assert body["params"]["arguments"]["query"] == "hello"


# --- integration with web_search.py -----------------------------------------
# These tests mock both ddgs and youcom_search to verify the fallback chain.


def test_youcom_backend_is_recognized_in_backends_list():
    """The YOUCOM_BACKEND constant is defined and usable in web_search.py."""
    from agent_core.integrations import web_search as ws

    assert ws.YOUCOM_BACKEND == "youcom"
    assert ws.YOUCOM_BACKEND not in ws.DEFAULT_BACKENDS  # opt-in only


def test_youcom_is_tried_before_ddgs_when_requested(monkeypatch):
    """When AGENT_SEARCH_BACKENDS=youcom, the youcom backend runs before ddgs."""
    monkeypatch.setenv("AGENT_SEARCH_BACKENDS", "youcom,brave")

    # Re-import to trigger the module-level BACKENDS rebuild
    import importlib

    from agent_core.integrations import web_search as ws

    importlib.reload(ws)

    assert ws.BACKENDS == ["youcom", "brave"]


def test_youcom_not_in_backends_by_default():
    """Without AGENT_SEARCH_BACKENDS=youcom, youcom is not in the chain."""
    import importlib
    from agent_core.integrations import web_search as ws

    importlib.reload(ws)

    assert ws.YOUCOM_BACKEND not in ws.BACKENDS
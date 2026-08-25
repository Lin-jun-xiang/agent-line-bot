"""Signed file links: URL resolution, signing, and the serving route's refusals.

The route is the only part of the workspace exposed to the open internet, so the
negative cases matter more than the happy path.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_core import filelinks, settings, workspace
from api.files_api import files_api

SESSION = "filelink-tests"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Point the workspace at a temp dir and pin a known secret and origin."""
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(settings, "SESSION_INDEX", tmp_path / "_sessions.json")
    monkeypatch.setattr(settings, "FILE_LINK_SECRET", "test-secret")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://example.onrender.com")
    monkeypatch.setattr(settings, "RENDER_EXTERNAL_URL", "")
    monkeypatch.setattr(filelinks, "_learned_base_url", "")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(files_api)
    return TestClient(app)


def _write(name: str, data: bytes = b"\xff\xd8\xffnot-really-a-jpeg") -> "object":
    target = workspace.session_dir(SESSION) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


# --- base url resolution ---------------------------------------------------

def test_explicit_base_url_wins_over_learned_and_render(monkeypatch):
    filelinks.remember_base_url("https://learned.example.com/callback")
    monkeypatch.setattr(settings, "RENDER_EXTERNAL_URL", "https://render.example.com")
    assert filelinks.base_url() == "https://example.onrender.com"


def test_learned_host_beats_render_default(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings, "RENDER_EXTERNAL_URL", "https://render.example.com")
    filelinks.remember_base_url("https://real-host.onrender.com/callback")
    assert filelinks.base_url() == "https://real-host.onrender.com"


def test_render_url_used_before_any_webhook_arrives(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings, "RENDER_EXTERNAL_URL", "https://render.example.com")
    assert filelinks.base_url() == "https://render.example.com"


def test_learning_ignores_a_url_with_no_host(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    filelinks.remember_base_url("/callback")
    assert filelinks.base_url() == ""


# --- link building ---------------------------------------------------------

def test_public_url_is_signed_and_scoped_to_the_session():
    target = _write("uploads/photo.jpg")
    url = filelinks.public_url(SESSION, target)
    slug = workspace.slugify_session(SESSION)
    assert url.startswith(f"https://example.onrender.com/files/{slug}/uploads/photo.jpg?")
    assert "sig=" in url and "exp=" in url


def test_no_link_without_a_known_public_host(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings, "RENDER_EXTERNAL_URL", "")
    assert filelinks.public_url(SESSION, _write("a.jpg")) is None


def test_no_link_for_a_file_outside_the_session(tmp_path):
    outsider = tmp_path / "elsewhere" / "secret.jpg"
    outsider.parent.mkdir(parents=True, exist_ok=True)
    outsider.write_bytes(b"x")
    assert filelinks.public_url(SESSION, outsider) is None


def test_kind_for_only_recognises_what_line_can_render():
    from pathlib import Path

    assert filelinks.kind_for(Path("a.JPG")) == "image"
    assert filelinks.kind_for(Path("a.mp4")) == "video"
    assert filelinks.kind_for(Path("a.txt")) is None
    assert filelinks.kind_for(Path("a.pdf")) is None


# --- serving ---------------------------------------------------------------

def test_signed_link_serves_the_bytes(client):
    payload = b"\xff\xd8\xffhello"
    url = filelinks.public_url(SESSION, _write("uploads/photo.jpg", payload))
    resp = client.get(url.replace("https://example.onrender.com", ""))
    assert resp.status_code == 200
    assert resp.content == payload
    assert resp.headers["content-type"].startswith("image/jpeg")


def test_tampered_path_is_refused(client):
    _write("uploads/photo.jpg")
    _write("MEMORY.md", b"private")
    url = filelinks.public_url(SESSION, workspace.session_dir(SESSION) / "uploads/photo.jpg")
    path = url.replace("https://example.onrender.com", "")
    resp = client.get(path.replace("uploads/photo.jpg", "MEMORY.md"))
    assert resp.status_code == 403


def test_expired_link_is_refused(client):
    _write("uploads/photo.jpg")
    url = filelinks.public_url(
        SESSION, workspace.session_dir(SESSION) / "uploads/photo.jpg", ttl=-10
    )
    assert client.get(url.replace("https://example.onrender.com", "")).status_code == 403


def test_unsigned_request_is_refused(client):
    _write("uploads/photo.jpg")
    slug = workspace.slugify_session(SESSION)
    assert client.get(f"/files/{slug}/uploads/photo.jpg").status_code == 422
    resp = client.get(f"/files/{slug}/uploads/photo.jpg?exp={int(time.time()) + 60}&sig=abc")
    assert resp.status_code == 403


@pytest.mark.parametrize("rel", ["../_sessions.json", "uploads/../../_sessions.json"])
def test_traversal_cannot_escape_the_session_even_when_signed(client, rel):
    """Even holding the secret, ../ must not reach outside the session.

    Driven through serve_file directly rather than the HTTP client: httpx
    normalises `..` out of a URL before it is ever sent, so a request-level test
    would pass without the containment check doing anything.
    """
    from fastapi import HTTPException

    from api.files_api import serve_file

    _write("uploads/photo.jpg")
    (workspace.workspace_root() / "_sessions.json").write_text("{}", encoding="utf-8")

    slug = workspace.slugify_session(SESSION)
    expires = int(time.time()) + 60
    sig = filelinks._signature(slug, rel, expires)

    with pytest.raises(HTTPException) as caught:
        import asyncio

        asyncio.run(serve_file(slug, rel, exp=expires, sig=sig))
    assert caught.value.status_code == 404

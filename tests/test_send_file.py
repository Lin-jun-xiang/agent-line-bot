"""send_file — the tool that lets the agent hand a workspace file back to LINE."""

from __future__ import annotations

import pytest

from agent_core import filelinks, settings, tools, workspace

SESSION = "send-file-tests"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(settings, "SESSION_INDEX", tmp_path / "_sessions.json")
    monkeypatch.setattr(settings, "FILE_LINK_SECRET", "test-secret")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://example.onrender.com")
    monkeypatch.setattr(settings, "RENDER_EXTERNAL_URL", "")
    monkeypatch.setattr(filelinks, "_learned_base_url", "")


def _cwd() -> str:
    return str(workspace.session_dir(SESSION))


def _write(rel: str, data: bytes = b"\xff\xd8\xff") -> None:
    target = workspace.session_dir(SESSION) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _send(rel: str, artifacts: list[dict] | None = None):
    artifacts = artifacts if artifacts is not None else []
    return tools.send_file_impl(rel, _cwd(), SESSION, artifacts), artifacts


def test_an_image_becomes_a_sendable_artifact():
    _write("uploads/photo.jpg")
    reply, artifacts = _send("uploads/photo.jpg")
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "image"
    assert artifacts[0]["url"].startswith("https://example.onrender.com/files/")
    assert "photo.jpg" in reply


def test_relative_paths_resolve_against_the_session_dir():
    _write("李中照片庫/備份/a.jpg")
    _, artifacts = _send("李中照片庫/備份/a.jpg")
    assert len(artifacts) == 1


def test_mp4_is_sent_as_a_video():
    _write("clip.mp4", b"\x00\x00\x00\x18ftypmp42")
    _, artifacts = _send("clip.mp4")
    assert artifacts[0]["kind"] == "video"


def test_repeated_calls_accumulate_so_every_photo_is_sent():
    _write("a.jpg")
    _write("b.jpg")
    artifacts: list[dict] = []
    _send("a.jpg", artifacts)
    _send("b.jpg", artifacts)
    assert [a["prompt"] for a in artifacts] == ["a.jpg", "b.jpg"]


def test_an_unsupported_format_is_refused_with_advice():
    _write("notes.txt", b"hello")
    reply, artifacts = _send("notes.txt")
    assert artifacts == []
    assert "only displays" in reply


def test_a_missing_file_says_so():
    reply, artifacts = _send("nope.jpg")
    assert artifacts == []
    assert "no such file" in reply


def test_a_path_outside_the_workspace_is_refused(tmp_path):
    outsider = tmp_path.parent / "outside.jpg"
    outsider.write_bytes(b"\xff\xd8\xff")
    reply, artifacts = _send(str(outsider))
    assert artifacts == []
    assert "outside the workspace" in reply


def test_no_artifact_when_the_public_host_is_unknown(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    _write("uploads/photo.jpg")
    reply, artifacts = _send("uploads/photo.jpg")
    assert artifacts == []
    assert "public" in reply


def test_the_link_it_produces_actually_serves_the_file():
    """End to end: what send_file hands the model must resolve to the bytes."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.files_api import files_api

    payload = b"\xff\xd8\xffreal-bytes"
    _write("uploads/photo.jpg", payload)
    _, artifacts = _send("uploads/photo.jpg")

    app = FastAPI()
    app.include_router(files_api)
    path = artifacts[0]["url"].replace("https://example.onrender.com", "")
    resp = TestClient(app).get(path)
    assert resp.status_code == 200
    assert resp.content == payload

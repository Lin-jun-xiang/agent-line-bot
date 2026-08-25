"""
Tests for the one failure that matters most: asked 「這張圖片是啥」the bot used to
answer 「我看不到圖片」without ever calling describe_image.

There is deliberately no keyword matching on the user's phrasing anywhere — that
was tried and thrown away, because Chinese has more ways to ask about a photo
than any list can hold (「這是什麼」contains no image word at all). Instead the
model is told plainly that the tool is its eyes, and a failed lookup answers with
the paths that do exist so it can retry itself.

These tests pin down both halves.

    python -m pytest tests/test_image_questions.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_core import prompt, tools, workspace  # noqa: E402

SESSION = "pytest-image-questions"


@pytest.fixture
def session():
    workspace.clear_session(SESSION)
    yield SESSION
    workspace.clear_session(SESSION)


# --- the instruction that replaced the heuristic ---------------------------

def test_the_prompt_forbids_the_refusal(session):
    workspace.store_upload(session, b"x", "photo.jpg")
    block = prompt._recent_uploads(session)
    assert "絕對不要回答「我看不到圖片」" in block
    assert "describe_image" in block


def test_the_prompt_names_the_full_relative_path(session):
    # Given only a bare filename the model guesses "photo.jpg" and the lookup
    # misses, because the file is under uploads/.
    workspace.store_upload(session, b"x", "photo.jpg")
    block = prompt._recent_uploads(session)
    assert "uploads/photo.jpg" in block


def test_the_prompt_points_at_the_newest_upload(session):
    import time

    workspace.store_upload(session, b"x", "old.jpg")
    time.sleep(0.01)
    workspace.store_upload(session, b"y", "new.jpg")
    block = prompt._recent_uploads(session)
    assert "最上面那個（uploads/new.jpg）是最新的" in block


def test_no_uploads_means_no_instruction(session):
    assert prompt._recent_uploads(session) == "（目前沒有）"


def test_the_prompt_mentions_the_retry_path(session):
    workspace.store_upload(session, b"x", "photo.jpg")
    assert "再呼叫一次" in prompt._recent_uploads(session)


# --- the self-correcting failure message -----------------------------------

def test_a_wrong_path_gets_told_the_right_one(session):
    workspace.store_upload(session, b"x", "photo.jpg")
    cwd = str(workspace.session_dir(session))
    # The model's usual mistake: the bare filename, without uploads/.
    out = tools._available_images(cwd)
    assert "uploads/photo.jpg" in out
    assert "call describe_image again" in out


def test_suggestions_are_relative_to_the_agents_cwd(session):
    workspace.store_upload(session, b"x", "photo.jpg")
    out = tools._available_images(str(workspace.session_dir(session)))
    # An absolute path would be useless: the agent must pass back a relative one.
    assert str(workspace.session_dir(session)) not in out


def test_newest_image_is_suggested_first(session):
    import time

    workspace.store_upload(session, b"x", "old.jpg")
    time.sleep(0.01)
    workspace.store_upload(session, b"y", "new.jpg")
    out = tools._available_images(str(workspace.session_dir(session)))
    assert out.index("uploads/new.jpg") < out.index("uploads/old.jpg")


def test_an_empty_workspace_says_so_plainly(session):
    workspace.session_dir(session)
    out = tools._available_images(str(workspace.session_dir(session)))
    assert "no image files" in out


def test_non_images_are_not_suggested(session):
    folder = workspace.session_dir(session)
    (folder / "notes.md").write_text("not an image", encoding="utf-8")
    assert "notes.md" not in tools._available_images(str(folder))


def test_the_manifest_is_not_offered_as_an_image(session):
    workspace.store_upload(session, b"x", "photo.jpg", sender="李中")
    out = tools._available_images(str(workspace.session_dir(session)))
    assert "_senders.json" not in out


def test_a_missing_cwd_does_not_raise(session):
    assert "Could not list" in tools._available_images(None)
    assert "Could not list" in tools._available_images(str(Path("no/such/dir")))


def test_describe_image_failure_includes_the_suggestions(session):
    import asyncio

    workspace.store_upload(session, b"x", "photo.jpg")
    out = asyncio.run(
        tools.describe_image_impl("photo.jpg", "什麼", str(workspace.session_dir(session)))
    )
    # No vision call happens: the path check fails first.
    assert out.startswith("no such image: photo.jpg")
    assert "uploads/photo.jpg" in out

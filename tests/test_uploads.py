"""
Upload rotation tests — no API key or network required.

Nothing else in the system deletes an uploaded image, so these assert the
newest-N rotation actually bounds the directory. Without it one chatty group
fills the disk that also holds every conversation's MEMORY.md.

    python -m pytest tests/test_uploads.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_core import prompt, settings, workspace  # noqa: E402

SESSION = "pytest-uploads"


@pytest.fixture
def session():
    workspace.clear_session(SESSION)
    yield SESSION
    workspace.clear_session(SESSION)


def _upload(session_id: str, n: int) -> None:
    for i in range(n):
        workspace.store_upload(session_id, b"jpeg-bytes", f"{i:03d}.jpg")
        # Rotation orders by mtime, whose resolution is coarse on Windows.
        time.sleep(0.01)


def _names(session_id: str) -> list[str]:
    folder = workspace.uploads_dir(session_id, create=False)
    return sorted(p.name for p in folder.iterdir())


def test_under_the_cap_nothing_is_deleted(session):
    _upload(session, settings.MAX_UPLOADS)
    assert len(_names(session)) == settings.MAX_UPLOADS


def test_over_the_cap_only_the_newest_survive(session):
    total = settings.MAX_UPLOADS + 4
    _upload(session, total)
    kept = _names(session)
    assert len(kept) == settings.MAX_UPLOADS
    # The last MAX_UPLOADS written, by name, are exactly the ones left.
    assert kept == sorted(f"{i:03d}.jpg" for i in range(total - settings.MAX_UPLOADS, total))


def test_store_upload_reports_what_it_evicted(session):
    _upload(session, settings.MAX_UPLOADS)
    _, evicted = workspace.store_upload(session, b"x", "newest.jpg")
    assert evicted == 1
    assert "newest.jpg" in _names(session)


def test_uploads_stay_inside_the_session(session):
    path, _ = workspace.store_upload(session, b"x", "photo.jpg")
    assert workspace.is_within(path, workspace.session_dir(session))


def test_prune_is_safe_when_nothing_was_ever_uploaded(session):
    assert workspace.prune_uploads(session) == 0


def test_keep_zero_retains_nothing(session):
    # Documented as the "don't keep images at all" setting, so pin it down.
    path, _ = workspace.store_upload(session, b"x", "a.jpg")
    assert workspace.prune_uploads(session, keep=0) == 1
    assert not path.exists()


def test_prompt_never_lists_more_than_rotation_keeps():
    # Otherwise the prompt advertises files that rotation has already deleted.
    assert prompt.UPLOADS_SHOWN <= settings.MAX_UPLOADS


# --- sender attribution ----------------------------------------------------
# A group shares one workspace. Without a recorded sender the agent, asked to
# "back up the photos 李中 sent", invents an attribution rather than admitting
# it cannot tell. These pin down that it always has an honest answer available.

def test_sender_is_recorded_and_shown(session):
    workspace.store_upload(session, b"x", "a.jpg", sender="李中", at="08/25 16:06")
    assert workspace.read_upload_senders(session)["a.jpg"]["sender"] == "李中"
    listing = prompt._recent_uploads(session)
    assert "李中" in listing and "08/25 16:06" in listing


def test_unattributed_upload_is_marked_unknown_not_blank(session):
    workspace.store_upload(session, b"x", "known.jpg", sender="李中")
    time.sleep(0.01)
    workspace.store_upload(session, b"y", "mystery.jpg")
    listing = prompt._recent_uploads(session)
    assert "不知道誰傳的" in listing
    assert "不要自己猜" in listing


def test_one_to_one_chat_is_not_annotated(session):
    # No senders recorded at all means a 1:1, where the conversation is the
    # person — annotating "unknown" there would be noise.
    workspace.store_upload(session, b"x", "a.jpg")
    assert "不知道誰傳的" not in prompt._recent_uploads(session)


def test_manifest_is_never_listed_as_an_upload(session):
    workspace.store_upload(session, b"x", "a.jpg", sender="李中")
    names = [p.name for p in workspace.upload_files(session)]
    assert settings.UPLOADS_MANIFEST not in names
    assert settings.UPLOADS_MANIFEST not in prompt._recent_uploads(session)


def test_manifest_does_not_consume_a_retention_slot(session):
    for i in range(settings.MAX_UPLOADS):
        workspace.store_upload(session, b"x", f"{i:03d}.jpg", sender="李中")
        time.sleep(0.01)
    assert len(workspace.upload_files(session)) == settings.MAX_UPLOADS


def test_pruning_forgets_the_senders_of_deleted_uploads(session):
    total = settings.MAX_UPLOADS + 3
    for i in range(total):
        workspace.store_upload(session, b"x", f"{i:03d}.jpg", sender=f"member-{i}")
        time.sleep(0.01)
    manifest = workspace.read_upload_senders(session)
    surviving = {p.name for p in workspace.upload_files(session)}
    assert set(manifest) == surviving
    assert "000.jpg" not in manifest


def test_corrupt_manifest_degrades_to_unknown(session):
    workspace.store_upload(session, b"x", "a.jpg", sender="李中")
    (workspace.uploads_dir(session) / settings.UPLOADS_MANIFEST).write_text(
        "{not json", encoding="utf-8"
    )
    assert workspace.read_upload_senders(session) == {}
    # Still lists the file rather than blowing up the whole prompt.
    assert "a.jpg" in prompt._recent_uploads(session)


def test_prompt_lists_newest_first(session):
    _upload(session, settings.MAX_UPLOADS)
    listing = prompt._recent_uploads(session)
    lines = [ln for ln in listing.splitlines() if ln.startswith("- ")]
    assert len(lines) == prompt.UPLOADS_SHOWN
    newest = f"{settings.MAX_UPLOADS - 1:03d}.jpg"
    assert lines[0].endswith(newest)

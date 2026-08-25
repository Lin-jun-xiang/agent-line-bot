"""
Conversation roster tests — offline, no network.

LINE does not expose a group's member list to an unverified account, so the bot
can only know the people it has actually heard from. These pin down that the
prompt says exactly that, rather than letting the agent report the roster size
as the group size.

    python -m pytest tests/test_members.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_core import prompt, settings, workspace  # noqa: E402

SESSION = "pytest-members"
SPEAKER = "林祥（成員代號 ccc333）"


@pytest.fixture
def session():
    workspace.clear_session(SESSION)
    yield SESSION
    workspace.clear_session(SESSION)


def test_a_speaker_is_recorded_and_listed(session):
    workspace.note_member(session, "Uaaa", "李中", at="08/25 16:06")
    assert workspace.read_members(session)["Uaaa"]["name"] == "李中"
    assert "李中" in prompt._roster_block(session, SPEAKER)


def test_repeat_speakers_are_counted_not_duplicated(session):
    workspace.note_member(session, "Uaaa", "李中", at="08/25 16:06")
    workspace.note_member(session, "Uaaa", "李中", at="08/25 16:09")
    members = workspace.read_members(session)
    assert len(members) == 1
    assert members["Uaaa"]["count"] == 2
    assert members["Uaaa"]["last"] == "08/25 16:09"
    assert members["Uaaa"]["first"] == "08/25 16:06"


def test_roster_is_not_presented_as_the_group_size(session):
    workspace.note_member(session, "Uaaa", "李中", at="08/25 16:06")
    block = prompt._roster_block(session, SPEAKER)
    assert "不是群組人數" in block
    assert "絕對不要猜一個數字" in block


def test_one_to_one_chat_gets_no_roster(session):
    workspace.note_member(session, "Uaaa", "李中", at="08/25 16:06")
    # speaker is None only in a 1:1, where a roster is meaningless.
    assert prompt._roster_block(session, None) == ""


def test_empty_roster_renders_nothing(session):
    assert prompt._roster_block(session, SPEAKER) == ""


def test_roster_is_capped_and_says_so(session, monkeypatch):
    monkeypatch.setattr(prompt, "MEMBERS_SHOWN", 3)
    for i in range(6):
        workspace.note_member(session, f"U{i}", f"member-{i}", at=f"08/25 16:0{i}")
    block = prompt._roster_block(session, SPEAKER)
    assert len([ln for ln in block.splitlines() if ln.startswith("- ")]) == 3
    assert "還有 3 位沒列出來" in block
    assert "共 6 位" in block


def test_most_recent_speakers_are_the_ones_shown(session, monkeypatch):
    monkeypatch.setattr(prompt, "MEMBERS_SHOWN", 2)
    workspace.note_member(session, "Uold", "早退的人", at="08/25 10:00")
    workspace.note_member(session, "Umid", "中間的人", at="08/25 15:00")
    workspace.note_member(session, "Unew", "最近的人", at="08/25 16:00")
    block = prompt._roster_block(session, SPEAKER)
    assert "最近的人" in block and "中間的人" in block
    assert "早退的人" not in block


def test_missing_user_id_is_ignored(session):
    workspace.note_member(session, "", "沒有 id 的人")
    assert workspace.read_members(session) == {}


def test_corrupt_roster_degrades_to_empty(session):
    workspace.note_member(session, "Uaaa", "李中", at="08/25 16:06")
    (workspace.session_dir(session) / settings.MEMBERS_FILENAME).write_text(
        "{not json", encoding="utf-8"
    )
    assert workspace.read_members(session) == {}
    assert prompt._roster_block(session, SPEAKER) == ""


def test_forget_clears_the_roster(session):
    workspace.note_member(session, "Uaaa", "李中", at="08/25 16:06")
    workspace.clear_session(session)
    assert workspace.read_members(session) == {}

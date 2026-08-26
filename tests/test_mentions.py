"""Recognising an @mention of the bot in a group message.

The v2 model classes the routes use throw away the webhook's `isSelf` flag, so
identity has to be re-established from the mentionee's user id — and from the
display name for members who never added the bot and whose id LINE withholds.
"""

from __future__ import annotations

import pytest

pytest.importorskip("config", reason="LINE routes need channel credentials")

from line_bot import routes  # noqa: E402

BOT_ID = "Ubot000000000000000000000000000"


class _Mentionee:
    def __init__(self, index, length, user_id=None):
        self.index, self.length, self.user_id = index, length, user_id


class _Event:
    """Just enough of a MessageEvent for _strip_bot_mention."""

    def __init__(self, mentionees):
        self.message = type("_Msg", (), {})()
        self.message.mention = (
            type("_Mention", (), {"mentionees": mentionees})() if mentionees is not None else None
        )


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    monkeypatch.setattr(routes, "_bot_identity", lambda: (BOT_ID, "小幫手"))


def strip(text, mentionees):
    return routes._strip_bot_mention(_Event(mentionees), text)


def test_a_mention_of_the_bot_is_stripped_and_the_rest_is_the_question():
    text = "@小幫手 今天天氣如何"
    assert strip(text, [_Mentionee(0, 4, BOT_ID)]) == "今天天氣如何"


def test_a_mention_of_someone_else_is_not_addressed_to_us():
    text = "@李中 你看一下"
    assert strip(text, [_Mentionee(0, 3, "Usomeone")]) is None


def test_the_bot_is_found_among_several_mentions():
    text = "@李中 @小幫手 幫他查一下"
    mentionees = [_Mentionee(0, 3, "Usomeone"), _Mentionee(4, 4, BOT_ID)]
    assert strip(text, mentionees) == "@李中  幫他查一下".strip()


def test_a_mention_in_the_middle_leaves_the_surrounding_text():
    text = "幫我問 @小幫手 這個問題"
    assert strip(text, [_Mentionee(4, 4, BOT_ID)]) == "幫我問  這個問題"


def test_an_id_less_mention_falls_back_to_the_display_name():
    # LINE omits userId for members who have not added the bot as a friend.
    text = "@小幫手 在嗎"
    assert strip(text, [_Mentionee(0, 4)]) == "在嗎"


def test_mention_all_does_not_count_as_addressing_the_bot():
    assert strip("@All 大家早", [_Mentionee(0, 4)]) is None


def test_a_message_with_no_mention_object_is_left_alone():
    assert strip("今天天氣如何", None) is None
    assert strip("今天天氣如何", []) is None


def test_offsets_that_do_not_land_on_the_tag_are_recovered_by_name():
    # LINE counts offsets its own way; an astral-plane emoji earlier in the
    # message shifts them against Python's indices.
    text = "\U0001f600 @小幫手 查一下"
    assert strip(text, [_Mentionee(3, 4, BOT_ID)]) == "\U0001f600  查一下".strip()


def test_a_bare_mention_leaves_an_empty_question():
    assert strip("@小幫手", [_Mentionee(0, 4, BOT_ID)]) == ""


def test_without_a_known_identity_nothing_is_treated_as_a_mention(monkeypatch):
    monkeypatch.setattr(routes, "_bot_identity", lambda: (None, None))
    assert strip("@小幫手 在嗎", [_Mentionee(0, 4, BOT_ID)]) is None

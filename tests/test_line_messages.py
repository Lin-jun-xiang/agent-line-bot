"""How an AgentRunResult becomes LINE messages.

Covers the two failures that made "回傳照片給我看" impossible: every artifact has
to be sent, not just the newest, and a run that died has to say why instead of
returning a canned apology.
"""

from __future__ import annotations

import pytest
from linebot.models import ImageSendMessage, TextSendMessage, VideoSendMessage

pytest.importorskip("config", reason="LINE routes need channel credentials")

from agent_core.runner import AgentRunResult  # noqa: E402
from line_bot.routes import MAX_MEDIA, _messages_for  # noqa: E402


def _image(url: str) -> dict:
    return {"kind": "image", "url": url, "prompt": "x"}


def test_every_image_is_sent_not_just_the_last():
    result = AgentRunResult(text="兩張都在這", artifacts=[_image("u1"), _image("u2")])
    messages = _messages_for(result)
    urls = [m.original_content_url for m in messages if isinstance(m, ImageSendMessage)]
    assert urls == ["u1", "u2"]


def test_media_comes_before_the_text():
    messages = _messages_for(AgentRunResult(text="說明", artifacts=[_image("u1")]))
    assert isinstance(messages[0], ImageSendMessage)
    assert isinstance(messages[-1], TextSendMessage)


def test_overflow_is_capped_and_admitted_in_the_text():
    artifacts = [_image(f"u{i}") for i in range(MAX_MEDIA + 3)]
    messages = _messages_for(AgentRunResult(text="都在這", artifacts=artifacts))
    images = [m for m in messages if isinstance(m, ImageSendMessage)]
    assert len(images) == MAX_MEDIA
    assert len(messages) == MAX_MEDIA + 1
    assert "3" in messages[-1].text


def test_video_uses_its_cover_as_the_preview():
    result = AgentRunResult(
        text="", artifacts=[{"kind": "video", "url": "v", "cover_url": "c"}]
    )
    video = next(m for m in _messages_for(result) if isinstance(m, VideoSendMessage))
    assert (video.original_content_url, video.preview_image_url) == ("v", "c")


def test_artifacts_without_a_url_are_skipped():
    result = AgentRunResult(text="hi", artifacts=[{"kind": "image", "url": None}])
    assert not [m for m in _messages_for(result) if isinstance(m, ImageSendMessage)]


def test_a_failed_run_reports_the_error_instead_of_apologising():
    result = AgentRunResult(text="", is_error=True, error="RuntimeError: cli died")
    text = _messages_for(result)[0].text
    assert "cli died" in text
    assert "沒能產出結果" not in text


def test_a_genuinely_empty_run_still_gets_the_apology():
    assert "沒能產出結果" in _messages_for(AgentRunResult(text=""))[0].text


def test_media_alone_needs_no_filler_text():
    messages = _messages_for(AgentRunResult(text="", artifacts=[_image("u1")]))
    assert len(messages) == 1
    assert isinstance(messages[0], ImageSendMessage)

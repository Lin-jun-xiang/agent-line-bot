"""
LINE webhook routes.

The bot no longer runs its own tool loop — every message is handed to the same
`AgentRunner` the REST API uses, so LINE and /agent/run exercise identical
behaviour and identical sandboxing.
"""

import sys
import threading

from fastapi import APIRouter, HTTPException, Request
from linebot import LineBotApi, SignatureValidator, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    ImageMessage,
    ImageSendMessage,
    MessageEvent,
    TextMessage,
    TextSendMessage,
    VideoSendMessage,
)
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent_core import (
    commands,
    filelinks,
    memory,
    prompt,
    run_sync,
    settings,
    workspace,
)

sys.path.append(".")

import config  # noqa: E402

line_app = APIRouter()

line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
_signature_validator = SignatureValidator(config.LINE_CHANNEL_SECRET)


@line_app.post("/callback")
async def callback(request: Request) -> str:
    """LINE Bot webhook callback."""
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    # LINE can only have reached us at our real public address, so this request
    # tells us what it is — which is exactly what send_file needs to build image
    # URLs, and what no local configuration can reliably know.
    #
    # Validated explicitly here rather than relying on handler.handle below,
    # for two reasons. Host and X-Forwarded-Host are caller-controlled, so
    # believing them before the signature checks out would let anyone aim our
    # image links at a host of their choosing. And handle() dispatches to
    # handlers that spawn the agent thread, so recording afterwards would leave
    # the very first message's run without a known host.
    if not _signature_validator.validate(body.decode(), signature):
        raise HTTPException(status_code=400, detail="Invalid signature")
    filelinks.remember_base_url(str(request.url))

    try:
        # handler.handle is synchronous and our handlers block on the agent, so
        # keep it off the event loop.
        await run_in_threadpool(handler.handle, body.decode(), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"


# display names change rarely and every lookup is an API round trip, so cache.
_display_names: dict[str, str] = {}


def _now_stamp() -> str:
    """Local wall-clock for upload bookkeeping, in the bot's configured zone."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(prompt.TIMEZONE)).strftime("%m/%d %H:%M")


def _describe_speaker(event) -> str | None:
    """Who sent this message, for group and room conversations.

    A session is keyed by conversation, not person, so in a group everyone shares
    one workspace and one MEMORY.md. Without naming the speaker the agent files
    every member's preferences under a single anonymous "you". Returns None for
    1:1 chats, where the conversation already *is* the person.
    """
    user_id, name = _speaker_identity(event)
    if name is None:
        return None
    if not user_id:
        return name
    # The id suffix disambiguates two members with the same display name.
    return f"{name}（成員代號 {user_id[-6:]}）"


def _speaker_identity(event) -> tuple[str | None, str | None]:
    """(user_id, display_name) for a group/room event; (None, None) for a 1:1.

    Split out from _describe_speaker because the roster needs the id and the raw
    name separately, and neither should trigger a second profile lookup.
    """
    source = event.source
    if source.type == "user":
        return None, None

    user_id = getattr(source, "user_id", None)
    if not user_id:
        return None, "（不明成員）"

    name = _display_names.get(user_id)
    if name is None:
        try:
            if source.type == "group":
                profile = line_bot_api.get_group_member_profile(source.group_id, user_id)
            else:
                profile = line_bot_api.get_room_member_profile(source.room_id, user_id)
            name = profile.display_name
        except LineBotApiError:
            # Happens when the member has not added the bot as a friend.
            name = "（不明成員）"
        if len(_display_names) > 500:
            _display_names.clear()
        _display_names[user_id] = name

    return user_id, name


def _note_speaker(event, source_id: str) -> str | None:
    """Record the speaker in the conversation roster and return their label.

    Called only where the bot is already handling the message — an @chat turn or
    a photo. Silent members stay unrecorded on purpose; see workspace.note_member.
    """
    speaker = _describe_speaker(event)
    if speaker:
        user_id, name = _speaker_identity(event)
        if user_id:
            workspace.note_member(source_id, user_id, name, at=_now_stamp())
    return speaker


def _reply_or_push(reply_token: str, source_id: str, messages) -> None:
    """Reply with the token, falling back to a push if it already expired.

    Agent turns can outlive a LINE reply token, so the fallback matters.
    """
    if not isinstance(messages, list):
        messages = [messages]
    try:
        line_bot_api.reply_message(reply_token, messages=messages)
    except LineBotApiError:
        if source_id:
            line_bot_api.push_message(source_id, messages=messages)


# LINE accepts at most 5 messages per reply, and the text explaining them is one
# of the 5. Asked for "all of 李中's photos" the agent may well call send_file more
# times than that, so cap here and let the text say what was left out.
MAX_MEDIA = 4


def _media_messages(result) -> tuple[list, int]:
    """Every image/video the turn produced, plus how many did not fit.

    All of them, not just the newest: one send_file call per photo is the natural
    way for the agent to answer "send me the pictures", and taking only the last
    artifact silently dropped the rest.
    """
    messages = []
    for artifact in result.artifacts:
        if len(messages) >= MAX_MEDIA:
            break
        url = artifact.get("url")
        if not url:
            continue
        if artifact.get("kind") == "video":
            messages.append(
                VideoSendMessage(
                    original_content_url=url,
                    preview_image_url=artifact.get("cover_url") or url,
                )
            )
        elif artifact.get("kind") == "image":
            messages.append(ImageSendMessage(original_content_url=url, preview_image_url=url))

    sendable = sum(1 for a in result.artifacts if a.get("url") and a.get("kind") in ("image", "video"))
    return messages, sendable - len(messages)


# Upstream failures the CLI hands back as the turn's *answer*, so they reach the
# chat verbatim. Asking about a photo got someone
# "API Error: Request rejected (429) · [1302][您的账户已达到速率限制]" — which reads
# like the bot is broken when in fact it is busy, and tells them nothing they can
# act on. The real text still goes to the log, where the operator needs it.
_ERROR_REPLIES = (
    (("429", "速率限制", "rate limit", "rate_limit"), "我現在被限流了，等一兩分鐘再問我一次 🙏"),
    (("401", "403", "invalid api key", "unauthorized"), "我的金鑰好像有問題，要請你檢查一下設定 🙏"),
    (("timeout", "timed out", "502", "504"), "剛剛連線逾時了，再跟我說一次看看 🙏"),
)


def _friendly_error(text: str) -> str | None:
    """Rewrite an upstream API error as something worth reading; None if not one."""
    if not text:
        return None
    lowered = text.lower()
    if "api error" not in lowered and "error" not in lowered:
        return None
    for needles, reply in _ERROR_REPLIES:
        if any(needle in lowered for needle in needles):
            return reply
    return None


def _messages_for(result) -> list:
    """Turn an AgentRunResult into LINE messages."""
    messages, dropped = _media_messages(result)

    text = (result.text or "").strip()
    friendly = _friendly_error(text)
    if friendly:
        print(f"[agent] upstream error shown as: {text[:200]}")
        text = friendly
    if not text and not messages:
        # A run that dies before producing text sets result.error and nothing else
        # (see AgentRunner._collect), so replying with the canned apology here
        # threw away the only description of what actually went wrong — the CLI
        # stderr the runner went to some trouble to capture. Show it: a turn that
        # fails silently is indistinguishable from one that failed for a reason.
        text = (
            (_friendly_error(result.error) or f"這次沒跑起來：{result.error}")
            if result.error
            else "抱歉，我這次沒能產出結果，請再說一次 🙏"
        )
    if dropped > 0:
        # Said plainly rather than dropped quietly: "here are the photos" beside a
        # short count is how you notice the reply was truncated.
        note = f"（還有 {dropped} 個檔案這次放不進來，再跟我說一聲就繼續傳）"
        text = f"{text}\n{note}" if text else note
    if text:
        # LINE hard-caps a text message at 5000 characters.
        messages.append(TextSendMessage(text=text[:4900]))
    return messages


def _run_and_reply(
    user_message: str, source_id: str, reply_token: str, speaker: str | None = None
) -> None:
    try:
        # The `@prompt` persona is loaded from disk inside build_system_prompt.
        result = run_sync(user_message, source_id, speaker=speaker)
        print(
            f"[agent] session={source_id} turns={result.num_turns} "
            f"tools={[c.name for c in result.tool_calls]} blocked={result.blocked}"
        )
        _reply_or_push(reply_token, source_id, _messages_for(result))
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        _reply_or_push(reply_token, source_id, TextSendMessage(text=f"發生錯誤：{exc}"))


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event) -> None:
    """Event - user sent a text message."""
    reply_token = event.reply_token
    user_message = event.message.text

    source_type = event.source.type
    source_id = getattr(event.source, f"{source_type}_id", None)

    # Answered locally: instant, costs no tokens, and always accurate.
    if user_message.strip().lower() in commands.help_triggers():
        _reply_or_push(reply_token, source_id, TextSendMessage(text=commands.help_text()))
        return

    if user_message.startswith("@prompt"):
        instruction = user_message.replace("@prompt", "", 1).strip()
        if instruction:
            memory.write_persona(source_id, instruction)
            reply = "已更新自訂設定 ✅"
        else:
            reply = "已還原成預設個性 ✅" if memory.clear_persona(source_id) else "本來就是預設個性。"
        # The persona is only read at the start of a turn, so drop the thread too.
        workspace.forget_claude_session(source_id)
        _reply_or_push(reply_token, source_id, TextSendMessage(text=reply))
        return

    if user_message.startswith("@init"):
        # Drops the conversation thread but keeps MEMORY.md and the persona —
        # what it knows about you should survive starting a fresh chat.
        workspace.forget_claude_session(source_id)
        _reply_or_push(reply_token, source_id, TextSendMessage(text="好，重新開始 👌"))
        return

    if user_message.startswith("@forget"):
        workspace.forget_claude_session(source_id)
        workspace.clear_session(source_id)
        _reply_or_push(
            reply_token, source_id, TextSendMessage(text="我把關於你的記憶都清掉了。")
        )
        return

    if source_type != "user":
        # In groups the bot only answers when addressed.
        if not user_message.startswith("@chat"):
            return
        user_message = user_message.replace("@chat", "", 1).strip()

    # Fire and forget: the webhook must return within LINE's timeout, but an
    # agent turn can take minutes.
    threading.Thread(
        target=_run_and_reply,
        args=(user_message, source_id, reply_token, _note_speaker(event, source_id)),
        daemon=True,
    ).start()


@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event) -> None:
    """Save an uploaded image into the sender's workspace so the agent can use it."""
    source_type = event.source.type
    source_id = getattr(event.source, f"{source_type}_id", None)

    try:
        content = line_bot_api.get_message_content(event.message.id).content
        # Who sent it, recorded now while we still know: the agent sees a shared
        # workspace and cannot otherwise tell one member's photos from another's.
        sender = _note_speaker(event, source_id)
        path, evicted = workspace.store_upload(
            source_id,
            content,
            f"{event.message.id}.jpg",
            sender=sender,
            at=_now_stamp(),
        )
        print(f"[line] stored upload for {source_id}: {path} (evicted {evicted})")

        _reply_or_push(
            event.reply_token,
            source_id,
            TextSendMessage(text=f"收到圖片了，已存成 uploads/{path.name}，要我做什麼？"),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[line] image error: {exc}")


class BroadcastRequest(BaseModel):
    prompt: str = Field(..., description="What the agent should compose and broadcast")
    session_id: str = Field(default="broadcast", description="Workspace to compose in")
    dry_run: bool = Field(default=False, description="Return the text without sending")


@line_app.post("/broadcast")
async def broadcast(req: BroadcastRequest) -> dict:
    """Have the agent compose a message and push it to every follower.

    Replaces the old hard-coded /recommend and /cwsChannel endpoints: instead of
    a separate LLM call path per broadcast type, describe the job in the prompt
    and the agent researches and writes it with the same tools and persona it
    uses in chat. Point a cron service at this with whatever prompt you want.
    """
    result = await run_in_threadpool(run_sync, req.prompt, req.session_id)
    text = (result.text or "").strip()

    if result.is_error or not text:
        return {"status": "failed", "error": result.error or "agent produced no text"}

    if not req.dry_run:
        line_bot_api.broadcast(TextSendMessage(text=text[:4900]))

    return {
        "status": "success",
        "sent": not req.dry_run,
        "text": text,
        "tools_used": sorted({c.name for c in result.tool_calls}),
    }

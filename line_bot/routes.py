"""
LINE webhook routes.

The bot no longer runs its own tool loop — every message is handed to the same
`AgentRunner` the REST API uses, so LINE and /agent/run exercise identical
behaviour and identical sandboxing.
"""

import sys
import threading

from fastapi import APIRouter, HTTPException, Request
from linebot import LineBotApi, WebhookHandler
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

from agent_core import commands, memory, run_sync, workspace

sys.path.append(".")

import config  # noqa: E402

line_app = APIRouter()

line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)


@line_app.post("/callback")
async def callback(request: Request) -> str:
    """LINE Bot webhook callback."""
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    try:
        # handler.handle is synchronous and our handlers block on the agent, so
        # keep it off the event loop.
        await run_in_threadpool(handler.handle, body.decode(), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"


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


def _messages_for(result) -> list:
    """Turn an AgentRunResult into LINE messages."""
    messages = []
    video = result.video
    if video:
        cover = video.get("cover_url") or video["url"]
        messages.append(
            VideoSendMessage(original_content_url=video["url"], preview_image_url=cover)
        )
    elif result.image_url:
        messages.append(
            ImageSendMessage(
                original_content_url=result.image_url, preview_image_url=result.image_url
            )
        )

    text = (result.text or "").strip()
    if not text and not messages:
        text = "抱歉，我這次沒能產出結果，請再說一次 🙏"
    if text:
        # LINE hard-caps a text message at 5000 characters.
        messages.append(TextSendMessage(text=text[:4900]))
    return messages


def _run_and_reply(user_message: str, source_id: str, reply_token: str) -> None:
    try:
        # The `@prompt` persona is loaded from disk inside build_system_prompt.
        result = run_sync(user_message, source_id)
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
        args=(user_message, source_id, reply_token),
        daemon=True,
    ).start()


@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event) -> None:
    """Save an uploaded image into the sender's workspace so the agent can use it."""
    source_type = event.source.type
    source_id = getattr(event.source, f"{source_type}_id", None)

    try:
        content = line_bot_api.get_message_content(event.message.id).content
        target = workspace.session_dir(source_id) / "uploads"
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{event.message.id}.jpg"
        path.write_bytes(content)
        print(f"[line] stored upload for {source_id}: {path}")
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

import base64
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import *

from chatgpt_linebot.memory import Memory
from chatgpt_linebot.modules import (
    CWArticleScraper,
    RapidAPIs,
    chat_completion,
    recommend_videos,
    run_agent,
    AgentResult,
)
from chatgpt_linebot.prompts import system_prompt

sys.path.append(".")

import config

line_app = APIRouter()
memory = Memory(20, system_prompt)
rapidapis = RapidAPIs(config.RAPID)
cws_scraper = CWArticleScraper()

line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)


@line_app.post("/callback")
async def callback(request: Request) -> str:
    """LINE Bot webhook callback

    Args:
        request (Request): Request Object.

    Raises:
        HTTPException: Invalid Signature Error

    Returns:
        str: OK
    """
    signature = request.headers["X-Line-Signature"]
    body = await request.body()

    # handle webhook body
    try:
        handler.handle(body.decode(), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Missing Parameter")
    return "OK"


def _build_env_override(source_id: str) -> dict:
    """Build per-user environment overrides for skill data isolation."""
    from chatgpt_linebot.modules.skill_loader import discover_skills
    user_data_root = Path.home() / ".chatbot-skills"
    env_override = {}
    for skill in discover_skills():
        env_key = skill.get("env_key")
        skill_name = skill.get("name", "default")
        if env_key:
            user_dir = user_data_root / skill_name / str(source_id)
            user_dir.mkdir(parents=True, exist_ok=True)
            env_override[env_key] = str(user_dir)
    return env_override


def send_video_reply(reply_token, video_url: str, preview_image_url: str) -> None:
    """Sends a video message to the user."""
    video_message = VideoSendMessage(
        original_content_url=video_url,
        preview_image_url=preview_image_url
    )
    line_bot_api.reply_message(reply_token, messages=video_message)


def send_image_reply(reply_token, img_url: str) -> None:
    """Sends an image message to the user."""
    if not img_url:
        send_text_reply(reply_token, 'Cannot get image.')
    image_message = ImageSendMessage(original_content_url=img_url, preview_image_url=img_url)
    line_bot_api.reply_message(reply_token, messages=image_message)


def send_text_reply(reply_token, text: str) -> None:
    """Sends a text message to the user."""
    if not text:
        text = "There're some problem in server."
    text_message = TextSendMessage(text=text)
    line_bot_api.reply_message(reply_token, messages=text_message)


@handler.add(MessageEvent, message=(TextMessage))
def handle_message(event) -> None:
    """Event - User sent message"""
    global memory

    if not isinstance(event.message, TextMessage):
        return

    reply_token = event.reply_token
    user_message = event.message.text

    source_type = event.source.type
    source_id = getattr(event.source, f"{source_type}_id", None)
    print('ID:', source_id, 'Memory:', memory.get(source_id))

    if user_message.startswith('@prompt'):
        custom_prompt = user_message.replace('@prompt', '').strip()
        memory.set_system_prompt(source_id, f"System Instruction:\n{custom_prompt}")
        print(f'Reset System Prompt for user {source_id}.')
        send_text_reply(reply_token, f"Successfully reset system prompt.")
        return
    elif user_message.startswith('@init'):
        memory.remove(source_id)
        print(f'Initialized Bot for user {source_id}.')
        send_text_reply(reply_token, f"Successfully initialized which will clear conversation history.")
        return

    if source_type == 'user':
        user_name = line_bot_api.get_profile(source_id).display_name
        print(f'{user_name}: {user_message}')
    else:
        if not user_message.startswith('@chat'):
            return
        else:
            user_message = user_message.replace('@chat', '')

    try:
        # Build per-user env overrides for skill data isolation
        env_override = _build_env_override(source_id)

        # Check if user has an uploaded image
        image_b64 = memory.image_storage.get(source_id)

        # Run the agent loop
        result = run_agent(
            user_message=user_message,
            source_id=source_id,
            memory=memory,
            env_override=env_override,
            image_base64=image_b64 if user_message_needs_image(user_message) else None,
        )

        print(f"""
        Agent Result
        =========================================
        Text: {result.text[:200] if result.text else '(none)'}
        Image: {result.image_url}
        Video: {result.video_url}
        Tool calls: {result.tool_calls_log}
        """)

        # Send appropriate reply based on what the agent produced
        if result.video_url:
            send_video_reply(reply_token, result.video_url, result.video_cover_url or result.video_url)
        elif result.image_url:
            send_image_reply(reply_token, result.image_url)
            if result.text:
                # Image already sent via reply, push text as follow-up
                line_bot_api.push_message(source_id, TextSendMessage(text=result.text))
        else:
            send_text_reply(reply_token, result.text)

    except Exception as e:
        import traceback
        traceback.print_exc()
        send_text_reply(reply_token, f"發生錯誤：{e}")


def user_message_needs_image(msg: str) -> bool:
    """Heuristic: does the user message likely refer to an uploaded image?"""
    keywords = ['這張', '這個圖', '圖片', '照片', '分析', '看看', '是什麼', '描述', 'image', 'photo', '截圖']
    return any(k in msg for k in keywords)


@handler.add(MessageEvent, message=(ImageMessage))
def handle_image_message(event) -> None:
    global memory

    if not isinstance(event.message, ImageMessage):
        return

    message_id = event.message.id
    
    source_type = event.source.type
    source_id = getattr(event.source, f"{source_type}_id", None)

    try:
        message_content = line_bot_api.get_message_content(message_id)
        image_data = message_content.content
        
        image_base64 = base64.b64encode(image_data).decode('utf-8')

        memory.image_storage[source_id] = f"{image_base64}"
        
        print(f"User {source_id} uploaded image, stored in memory")

    except Exception as e:
        print(f"Error processing image: {e}")


@line_app.get("/recommend")
def recommend_from_yt() -> dict:
    """Line Bot Broadcast

    Descriptions
    ------------
    Recommend youtube videos to all followed users.
    (Use cron-job.org to call this api)

    References
    ----------
    https://www.cnblogs.com/pungchur/p/14385539.html
    https://steam.oxxostudio.tw/category/python/example/line-push-message.html
    """
    videos = recommend_videos()

    if videos and "There're something wrong in openai api when call, please try again." not in videos:
        line_bot_api.broadcast(TextSendMessage(text=videos))

        # Push message to group via known group (event.source.group_id)
        known_group_ids = [
            'C6d-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
            'Ccc-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
            'Cbb-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
        ]
        for group_id in known_group_ids:
            line_bot_api.push_message(group_id, TextSendMessage(text=videos))

        print('Successfully recommended videos')
        return {"status": "success", "message": "recommended videos."}

    else:
        print('Failed recommended videos')
        return {"status": "failed", "message": "no get recommended videos."}


@line_app.get('/cwsChannel')
def get_cws_channel() -> dict:
    article_details = cws_scraper.get_latest_article()
    cws_channel_response = cws_scraper.get_cws_channel_response(article_details)

    if cws_channel_response:
        line_bot_api.broadcast(TextSendMessage(text=cws_channel_response))
        return {"status": "success", "message": "got cws channel response."}

    else:
        return {"status": "failed", "message": "no get cws channel response."}

import base64
import json
import re
import sys

from fastapi import APIRouter, HTTPException, Request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import *

from chatgpt_linebot.memory import Memory
from chatgpt_linebot.modules import (
    CWArticleScraper,
    Horoscope,
    ImageCrawler,
    RapidAPIs,
    chat_completion,
    recommend_videos,
)
from chatgpt_linebot.prompts import agent_template, system_prompt

sys.path.append(".")

import config

line_app = APIRouter()
memory = Memory(5, system_prompt)
horoscope = Horoscope()
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


def extract_legal_json(text: str) -> dict:
    """Convert the LLM Resonse to JSON."""
    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r'`json\n([\s\S]+?)\n`', text)
    json_str = match.group(1)
    json_data = json.loads(json_str)
    return json_data


def agent(query: str) -> tuple[str]:
    """Auto use correct tool by user query."""
    prompt = agent_template + query
    message = [{'role': 'user', 'content': prompt}]

    try:
        response = chat_completion(memory=message, method=config.GPT_METHOD)

        result = extract_legal_json(response)
        tool = result.get('tool', 'chat_completion')
        input_query = result.get('input', query)

        print(f"""
        Agent
        =========================================
        Query: {query}
        Tool: {tool}
        Input: {input_query}
        Raw Response: {response}
        """)

        return tool, input_query
        
    except Exception as e:
        print(f"JSON Parser Error for: {response}")
        return 'chat_completion', query


def send_video_reply(reply_token, video_url: str, preview_image_url: str) -> None:
    """Sends a video message to the user."""
    video_message = VideoSendMessage(
        original_content_url=video_url,
        preview_image_url=preview_image_url
    )
    line_bot_api.reply_message(reply_token, messages=video_message)


def search_image_url(query: str) -> str:
    """Fetches image URL from different search sources."""
    img_crawler = ImageCrawler(nums=5)
    img_url = img_crawler.get_url(query)
    if not img_url:
        img_serp = ImageCrawler(engine='serpapi', nums=5, api_key=config.SERPAPI_API_KEY)
        img_url = img_serp.get_url(query)
        print('Used Serpapi search image instead of icrawler.')
    return img_url


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
    """Event - User sent message

    Args:
        event (LINE Event Object)

    Refs:
        https://developers.line.biz/en/reference/messaging-api/#message-event
        https://www.21cs.tw/Nurse/showLiangArticle.xhtml?liangArticleId=503
    """
    global memory

    if not isinstance(event.message, TextMessage):
        return

    reply_token = event.reply_token
    user_message = event.message.text

    source_type = event.source.type
    source_id = getattr(event.source, f"{source_type}_id", None)
    print('ID:', source_id, 'Memory:', memory.get(source_id))

    if user_message.startswith('@prompt'):
        # 讓使用者自訂 prompt
        memory = Memory(5, user_message.replace('@prompt', ''))
        print('Reset System Prompt.')
        send_text_reply(reply_token, f"Successfully reset system prompt.")
        return
    elif user_message.startswith('@init'):
        # 初始化 prompt
        memory = Memory(5, system_prompt)
        print('Initialized Bot.')
        send_text_reply(reply_token, f"Successfully initialized which will clear conversation history.")
        return

    if source_type == 'user':
        # 非群組聊天
        user_name = line_bot_api.get_profile(source_id).display_name
        print(f'{user_name}: {user_message}')

    else:
        # 群組聊天
        if not user_message.startswith('@chat'):
            # 在群組聊天中若沒有透過 @chat 輸入訊息則忽略
            return
        else:
            user_message = user_message.replace('@chat', '')

    response = ""
    try:
        tool, input_query = agent(user_message)

        if tool in ['chat_completion']:
            memory.append(source_id, 'user', f"{user_message}")
        elif tool in ['image_inference']:
            memory.append(source_id, 'user', [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{memory.image_storage[source_id]}"}},
                {"type": "text", "text": f"{user_message}"}
            ])
        else:
            memory.append(source_id, 'user', f"{user_message}")

        if tool in ['chat_completion']:
            response = chat_completion(source_id, memory, config.GPT_METHOD)
            send_text_reply(reply_token, response)

        elif tool in ['image_inference']:
            response = chat_completion(source_id, memory, config.GPT_METHOD, zhipuai_type='image_inference')
            send_text_reply(reply_token, response)

        elif tool in ['generate_image']:
            response = chat_completion(source_id, input_query, config.GPT_METHOD, zhipuai_type='image_gen')
            send_image_reply(reply_token, response)

        elif tool in ['text_gen_video']:
            response = chat_completion(source_id, input_query, config.GPT_METHOD, zhipuai_type='text_gen_video')
            send_video_reply(reply_token, response.video_result[0].url, response.video_result[0].cover_image_url)
            response = str(response)

        elif tool in ['img_gen_video']:
            response = chat_completion(source_id, memory, config.GPT_METHOD, zhipuai_type='img_gen_video')
            send_video_reply(reply_token, response.video_result[0].url, response.video_result[0].cover_image_url)
            response = str(response)

        elif tool in ['search_image_url']:
            response = eval(f"{tool}('{input_query}')")
            send_image_reply(reply_token, response)

        else:
            response = eval(f"{tool}('{input_query}')")
            send_text_reply(reply_token, response)

        memory.append(source_id, 'system', response)

    except Exception as e:
        send_text_reply(reply_token, f"{e}: {response}")


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

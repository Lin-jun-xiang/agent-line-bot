import os
import time

from zhipuai import ZhipuAI

from chatgpt_linebot.memory import Memory

api_key = os.environ.get('GPT_API_KEY')


def chat_completion(
    id: int = 0,
    memory: Memory = Memory(5),
    zhipuai_type: str = 'text',
) -> str:
    """Use ZhipuAI API for chat completion"""
    if isinstance(memory, Memory):
        message = memory.get(id)
    else:
        message = memory

    try:
        client = ZhipuAI(api_key=api_key)

        if zhipuai_type == 'text':
            response = client.chat.completions.create(
                model="glm-4-flash",
                messages=message,
            )

        elif zhipuai_type == 'image_inference':
            response = client.chat.completions.create(
                model="glm-4v-flash",
                messages=message
            )

        elif zhipuai_type == 'image_gen':
            response = client.images.generations(
                model="cogview-3-flash",
                prompt=message,
            )
            image_url = response.data[0].url
            return image_url

        elif zhipuai_type == 'text_gen_video':
            response = client.videos.generations(
                model="cogvideox-flash",
                prompt=str(message),
                quality="quality",
                with_audio=False,
                fps=30,
            )
            video = client.videos.retrieve_videos_result(
                id=response.id
            )
            while video.task_status == 'PROCESSING':
                time.sleep(1)
                video = client.videos.retrieve_videos_result(
                    id=response.id
                )
            return video

        elif zhipuai_type == 'img_gen_video':
            response = client.videos.generations(
                model="cogvideox-flash",
                image_url=memory.image_storage[id],
                prompt=str(message[-1]),
                quality="quality",
                with_audio=False,
                fps=30,
            )
            video = client.videos.retrieve_videos_result(
                id=response.id
            )
            while video.task_status == 'PROCESSING':
                time.sleep(1)
                video = client.videos.retrieve_videos_result(
                    id=response.id
                )
            return video

        response = response.choices[0].message.content

        return response

    except Exception as e:
        response = (
        "There're something wrong in api, please try again.\n"
        "Or connect to developer: https://github.com/Lin-jun-xiang/chatgpt-line-bot/issues \n"
        f"{e}"
        )
        print(e)
        return response

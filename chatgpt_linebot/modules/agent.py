"""
Agent Loop — Real multi-turn agent using ZhipuAI function calling.

The agent can:
1. Read files (e.g. SKILL.md) to understand how to use a skill
2. Execute shell commands (e.g. finance CLI)
3. Search the web for real-time info
4. Generate images / videos
5. Respond with text when done

The loop continues until the LLM produces a final text response (no more tool calls)
or hits the max iteration limit.
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from zhipuai import ZhipuAI

from chatgpt_linebot.modules.execute_command import execute_command as _exec_cmd
from chatgpt_linebot.modules.web_search import deep_web_search
from chatgpt_linebot.modules.image_crawler import ImageCrawler

api_key = os.environ.get("GPT_API_KEY")

MAX_ITERATIONS = 8

# ---------------------------------------------------------------------------
# Tool definitions (ZhipuAI function-calling format)
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the content of a file. Use this to read SKILL.md or any documentation "
                "to understand how a CLI skill works before executing commands."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file to read.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {            "name": "execute_command",
            "description": (
                "Execute a shell command for a registered skill CLI. "
                "IMPORTANT: You must ALWAYS call read_file on the skill's SKILL.md FIRST "
                "before calling this tool. Never guess command syntax. "
                "Per-user data isolation is handled automatically via environment variables."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The full shell command to execute.",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the internet for real-time information, news, current events, "
                "weather, stock prices, or any factual data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an image from a text description using AI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Text description of the image to generate.",
                    }
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_image",
            "description": "Search the web for an existing image matching the description.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What image to search for.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": "Generate a video from a text description.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Text description of the video to generate.",
                    }
                },
                "required": ["prompt"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Agent result
# ---------------------------------------------------------------------------
@dataclass
class AgentResult:
    """What the agent loop produces."""
    text: str = ""
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    video_cover_url: Optional[str] = None
    tool_calls_log: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def _dispatch_tool(
    name: str,
    args: dict,
    env_override: dict,
    result: AgentResult,
) -> str:
    """Execute a tool and return the result string to feed back to LLM."""

    if name == "read_file":
        path = args.get("path", "")
        try:
            resolved = Path(path).resolve()
            # Only allow reading files under the skills directory
            skills_root = Path(__file__).resolve().parent.parent.parent / "skills"
            if not str(resolved).startswith(str(skills_root)):
                return f"❌ 只能讀取 skills/ 目錄下的檔案，不允許：{path}"
            content = resolved.read_text(encoding="utf-8")
            # Truncate very long files
            if len(content) > 8000:
                content = content[:8000] + "\n\n... (truncated, file too long)"
            return content
        except Exception as e:
            return f"❌ Cannot read file: {e}"

    elif name == "execute_command":
        command = args.get("command", "")
        return _exec_cmd(command, env_override=env_override)

    elif name == "web_search":
        query = args.get("query", "")
        try:
            return deep_web_search(query, max_results=3, max_chars_per_page=2000)
        except Exception as e:
            return f"❌ Search error: {e}"

    elif name == "generate_image":
        prompt = args.get("prompt", "")
        try:
            client = ZhipuAI(api_key=api_key)
            resp = client.images.generations(model="cogview-3-flash", prompt=prompt)
            url = resp.data[0].url
            result.image_url = url
            return f"✅ Image generated: {url}"
        except Exception as e:
            return f"❌ Image generation error: {e}"

    elif name == "search_image":
        query = args.get("query", "")
        try:
            import sys
            sys.path.append(".")
            import config
            crawler = ImageCrawler(nums=5)
            url = crawler.get_url(query)
            if not url:
                crawler2 = ImageCrawler(engine="serpapi", nums=5, api_key=config.SERPAPI_API_KEY)
                url = crawler2.get_url(query)
            if not url and getattr(config, 'TAVILY_API_KEY', None):
                crawler3 = ImageCrawler(engine="tavily", nums=5, api_key=config.TAVILY_API_KEY)
                url = crawler3.get_url(query)
            if url:
                result.image_url = url
                return f"✅ Image found: {url}"
            return "❌ No image found."
        except Exception as e:
            return f"❌ Image search error: {e}"

    elif name == "generate_video":
        prompt = args.get("prompt", "")
        try:
            client = ZhipuAI(api_key=api_key)
            resp = client.videos.generations(
                model="cogvideox-flash",
                prompt=prompt,
                quality="quality",
                with_audio=False,
                fps=30,
            )
            video = client.videos.retrieve_videos_result(id=resp.id)
            while video.task_status == "PROCESSING":
                time.sleep(1)
                video = client.videos.retrieve_videos_result(id=resp.id)
            result.video_url = video.video_result[0].url
            result.video_cover_url = video.video_result[0].cover_image_url
            return f"✅ Video generated: {result.video_url}"
        except Exception as e:
            return f"❌ Video generation error: {e}"

    return f"❌ Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(
    user_message: str,
    source_id: str,
    memory,
    env_override: dict = None,
    image_base64: str = None,
) -> AgentResult:
    """
    Run the agent loop.

    1. Append user message to memory
    2. Call LLM with tools
    3. If LLM returns tool_calls → execute them, feed results back, repeat
    4. If LLM returns text (no tool_calls) → done
    """
    if env_override is None:
        env_override = {}

    # Build user content (support image if present)
    if image_base64:
        user_content = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
            {"type": "text", "text": user_message},
        ]
    else:
        user_content = user_message

    memory.append(source_id, "user", user_content)
    messages = memory.get(source_id)

    result = AgentResult()
    client = ZhipuAI(api_key=api_key)

    for iteration in range(MAX_ITERATIONS):
        try:
            response = client.chat.completions.create(
                model="glm-4-flash",
                messages=messages,
                tools=TOOLS,
            )
        except Exception as e:
            result.text = f"❌ API error: {e}"
            break

        choice = response.choices[0]
        msg = choice.message

        # If no tool calls → final response
        if not msg.tool_calls:
            result.text = msg.content or ""
            # Save assistant response to memory
            memory.append(source_id, "assistant", result.text)
            break

        # Has tool calls → execute each and feed back
        # Append the assistant message (with tool_calls) to messages
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except Exception:
                fn_args = {}

            print(f"  [Agent] Tool call: {fn_name}({fn_args})")
            result.tool_calls_log.append({"tool": fn_name, "args": fn_args})

            tool_result = _dispatch_tool(fn_name, fn_args, env_override, result)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(tool_result),
            })

    else:
        # Hit max iterations
        if not result.text:
            result.text = "抱歉，處理步驟太多了，請試著簡化你的請求 🙏"

    # Update memory storage with final messages (trim tool messages for storage)
    # We keep the memory object's internal storage in sync
    memory.storage[source_id] = messages

    return result

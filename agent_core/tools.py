"""
In-process MCP tools.

Claude Code's built-in WebSearch/WebFetch are Anthropic server-side tools, so they
do not exist when the CLI is pointed at Z.AI. We supply equivalents (plus the
bot's media generators) as an SDK MCP server that runs inside this Python process
— no subprocess, no extra port.

Tool results also land in an `artifacts` list so the LINE handler can turn a
generated image/video into a proper LINE message instead of a URL in text.
"""

from __future__ import annotations

import time
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from agent_core import settings

SERVER_NAME = "botkit"
SHELL_TIMEOUT = 60

# Longest edge sent to the vision model. Well inside its 5MB / 6000px limits
# while keeping enough detail to answer questions about a photo.
VISION_MAX_EDGE = 1568


def _text(payload: str) -> dict:
    return {"content": [{"type": "text", "text": payload}]}


def _encode_image(path) -> tuple[str | None, str | None]:
    """Base64-encode an image, shrinking it to fit the vision API's limits.

    The vision models reject anything over 5MB or 6000x6000, and photos straight
    off a phone routinely exceed both. Downscaling to VISION_MAX_EDGE also makes
    the request substantially faster with no practical loss of detail for
    "what is in this picture" questions.

    Returns (base64, error).
    """
    import base64
    import io

    try:
        from PIL import Image
    except ImportError:  # Pillow missing — send the original and hope it fits
        try:
            return base64.b64encode(path.read_bytes()).decode("utf-8"), None
        except OSError as exc:
            return None, str(exc)

    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((VISION_MAX_EDGE, VISION_MAX_EDGE))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("utf-8"), None
    except Exception as exc:  # noqa: BLE001 - unreadable/unsupported image
        return None, f"{type(exc).__name__}: {exc}"


async def describe_image_impl(raw: str, question: str, cwd: str | None) -> str:
    """Answer a question about an image file. Module-level so it is testable.

    The chat model has no vision, so this routes to a vision model. Tools that
    open files themselves must run the containment check directly — the
    PreToolUse hook only sees `path` as an opaque string.
    """
    from zhipuai import ZhipuAI

    target = _resolve_in_workspace(raw, cwd)
    if target is None:
        return f"describe_image refused: '{raw}' is outside the workspace"
    if not target.is_file():
        return f"no such image: {raw}"

    encoded, encode_error = _encode_image(target)
    if encoded is None:
        print(f"[describe_image] cannot encode {target}: {encode_error}")
        return f"describe_image could not read the image: {encode_error}"

    client = ZhipuAI(api_key=settings.GLM_API_KEY)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": encoded}},
                {"type": "text", "text": question},
            ],
        }
    ]

    # The free vision models answer 429 when saturated instead of queueing,
    # so try each in turn rather than failing the whole request.
    errors = []
    for model in settings.VISION_MODELS:
        try:
            resp = client.chat.completions.create(model=model, messages=messages)
            text = resp.choices[0].message.content
            if text:
                return text
            errors.append(f"{model}: empty response")
        except Exception as exc:  # noqa: BLE001
            # Logged, not just returned: the model paraphrases failures away
            # ("抱歉我看不了這張圖片") and the real cause never reaches the operator.
            errors.append(f"{model}: {type(exc).__name__}: {exc}")
    detail = "; ".join(errors)
    print(f"[describe_image] all vision models failed for {target.name}: {detail}")
    return f"describe_image failed on every vision model — {detail}"


def _resolve_in_workspace(raw: str, cwd: str | None):
    """Resolve a path the model handed us, refusing anything outside the sandbox.

    Tools that read files themselves bypass the PreToolUse hook's path checks
    (the hook only sees `path`/`question` as opaque strings), so they have to run
    the containment check directly.
    """
    from pathlib import Path

    from agent_core import workspace

    base = Path(cwd) if cwd else workspace.workspace_root()
    roots = [base, workspace.shared_dir(), workspace.skills_dir()]
    return workspace.resolve_within(raw, roots, base=base)


def build_tool_server(artifacts: list[dict], cwd: str | None = None):
    """Create the MCP server for one agent run.

    `artifacts` is appended to as media is produced; the runner reads it after
    the run finishes. `cwd` is the session workspace that run_shell executes in.
    """

    @tool(
        "run_shell",
        # Kept deliberately terse. The built-in Bash tool's description is ~740
        # tokens of git/safety/platform guidance, and on the metered free tier
        # that one schema is enough to push every turn from ~4s to ~2min. This
        # replaces it at roughly a tenth of the size.
        "Run a shell command in the working directory and return its output. "
        "Use for calculations, data processing, running scripts. One command at a time.",
        {"command": str},
    )
    async def run_shell(args: dict[str, Any]) -> dict:
        import asyncio

        command = args.get("command", "")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=SHELL_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                return _text(f"timed out after {SHELL_TIMEOUT}s")
            text = out.decode("utf-8", errors="replace").strip()
            if len(text) > 6000:
                text = text[:6000] + "\n…(output truncated)"
            return _text(text or f"(exit {proc.returncode}, no output)")
        except Exception as exc:  # noqa: BLE001
            return _text(f"run_shell failed: {exc}")

    @tool(
        "web_search",
        "Search the live internet and read the top results. Use for news, prices, "
        "weather, sports, or anything after your knowledge cutoff.",
        {"query": str},
    )
    async def web_search(args: dict[str, Any]) -> dict:
        from agent_core.integrations.web_search import deep_web_search

        query = args.get("query", "")
        try:
            return _text(deep_web_search(query, max_results=3, max_chars_per_page=2500))
        except Exception as exc:  # noqa: BLE001 - surfaced back to the model
            return _text(f"web_search failed: {exc}")

    @tool(
        "fetch_url",
        "Fetch one URL and return its readable text content.",
        {"url": str},
    )
    async def fetch_url(args: dict[str, Any]) -> dict:
        import requests
        from bs4 import BeautifulSoup

        url = args.get("url", "")
        try:
            resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = " ".join(soup.get_text(" ").split())
            return _text(text[:8000])
        except Exception as exc:  # noqa: BLE001
            return _text(f"fetch_url failed: {exc}")

    @tool(
        "generate_image",
        "Generate an image from a text description. Returns a public URL.",
        {"prompt": str},
    )
    async def generate_image(args: dict[str, Any]) -> dict:
        from zhipuai import ZhipuAI

        prompt = args.get("prompt", "")
        try:
            client = ZhipuAI(api_key=settings.GLM_API_KEY)
            resp = client.images.generations(model="cogview-3-flash", prompt=prompt)
            url = resp.data[0].url
            artifacts.append({"kind": "image", "url": url, "prompt": prompt})
            return _text(f"Image generated: {url}")
        except Exception as exc:  # noqa: BLE001
            # Logged as well as returned: the model paraphrases tool failures
            # into a vague apology and the real cause never reaches the operator.
            print(f"[generate_image] {type(exc).__name__}: {exc}")
            return _text(f"generate_image failed: {exc}")

    @tool(
        "describe_image",
        "Look at an image file in the workspace and answer a question about it. "
        "Use whenever the user asks about a picture they sent.",
        {"path": str, "question": str},
    )
    async def describe_image(args: dict[str, Any]) -> dict:
        return _text(
            await describe_image_impl(
                args.get("path", ""),
                args.get("question") or "這張圖片裡有什麼？請詳細描述。",
                cwd,
            )
        )

    @tool(
        "generate_video",
        "Generate a short video from a text description, optionally animating an "
        "existing image in the workspace. Returns a public URL.",
        {"prompt": str, "image_path": str},
    )
    async def generate_video(args: dict[str, Any]) -> dict:
        import base64

        from zhipuai import ZhipuAI

        prompt = args.get("prompt", "")
        image_path = args.get("image_path") or ""
        try:
            client = ZhipuAI(api_key=settings.GLM_API_KEY)
            kwargs: dict[str, Any] = {
                "model": "cogvideox-flash",
                "prompt": prompt,
                "with_audio": False,
                "fps": 30,
            }
            if image_path:
                target = _resolve_in_workspace(image_path, cwd)
                if target is None or not target.is_file():
                    return _text(f"generate_video: cannot use image '{image_path}'")
                kwargs["image_url"] = base64.b64encode(target.read_bytes()).decode("utf-8")
            resp = client.videos.generations(**kwargs)
            video = client.videos.retrieve_videos_result(id=resp.id)
            deadline = time.time() + 180
            while video.task_status == "PROCESSING" and time.time() < deadline:
                time.sleep(2)
                video = client.videos.retrieve_videos_result(id=resp.id)
            if video.task_status != "SUCCESS":
                return _text(f"generate_video did not finish: {video.task_status}")
            item = video.video_result[0]
            artifacts.append(
                {
                    "kind": "video",
                    "url": item.url,
                    "cover_url": getattr(item, "cover_image_url", None),
                    "prompt": prompt,
                }
            )
            return _text(f"Video generated: {item.url}")
        except Exception as exc:  # noqa: BLE001
            return _text(f"generate_video failed: {exc}")

    @tool(
        "search_image",
        "Find an existing photo on the web matching a description. Returns a URL.",
        {"query": str},
    )
    async def search_image(args: dict[str, Any]) -> dict:
        query = args.get("query", "")
        try:
            import config

            from agent_core.integrations.image_crawler import ImageCrawler

            url = ImageCrawler(
                nums=5, api_key=getattr(config, "SERPAPI_API_KEY", None)
            ).get_url(query)
            if not url:
                return _text(
                    "No image found for that query. Say so plainly — do not claim "
                    "the image tools are broken."
                )
            artifacts.append({"kind": "image", "url": url, "prompt": query})
            return _text(f"Image found: {url}")
        except Exception as exc:  # noqa: BLE001
            print(f"[search_image] {type(exc).__name__}: {exc}")
            return _text(f"search_image failed: {exc}")

    return create_sdk_mcp_server(
        name=SERVER_NAME,
        version="1.0.0",
        tools=[
            run_shell,
            web_search,
            fetch_url,
            generate_image,
            search_image,
            describe_image,
            generate_video,
        ],
    )


# Fully-qualified names, for allow-listing.
TOOL_NAMES = [
    f"mcp__{SERVER_NAME}__{name}"
    for name in (
        "run_shell",
        "web_search",
        "fetch_url",
        "generate_image",
        "search_image",
        "describe_image",
        "generate_video",
    )
]

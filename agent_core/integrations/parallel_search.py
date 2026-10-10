"""Anonymous Parallel Search MCP, using the SDK's Streamable HTTP client."""

import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ENDPOINT = "https://search.parallel.ai/mcp"
USER_AGENT = (
    "agent-line-bot/2.0 (+https://github.com/Lin-jun-xiang/agent-line-bot)"
)
CALL_TIMEOUT = 30


async def call_tool(name: str, arguments: dict, session_id: str) -> str:
    """Return the remote tool's text, surfacing service errors to the bot.

    A fresh connection per call avoids sharing transport state across the
    runner's event loops. Cancellation closes both the session and transport.
    No credentials are loaded from the environment or the CLI's saved settings.
    """
    async with asyncio.timeout(CALL_TIMEOUT):
        async with streamablehttp_client(
            ENDPOINT,
            headers={"User-Agent": USER_AGENT},
            timeout=20,
            sse_read_timeout=CALL_TIMEOUT,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    name, {**arguments, "session_id": session_id}
                )

    text = "\n".join(
        block.text for block in result.content if block.type == "text"
    )
    if result.isError:
        raise RuntimeError(text or "Parallel MCP tool failed")
    if not text:
        raise RuntimeError("Parallel MCP returned no text")
    return text

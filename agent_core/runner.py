"""
AgentRunner — one entry point for every caller (LINE webhook, REST API, CLI test).

It wires together:
  settings.py   provider config (Claude Code CLI -> Z.AI GLM)
  workspace.py  per-session sandbox directory + session-id index
  guard.py      PreToolUse hook + can_use_tool containment
  tools.py      in-process MCP tools (search / fetch / image / video)
  prompt.py     system prompt

`stream()` yields plain dicts so the REST layer can forward them as SSE without
knowing anything about the SDK; `run()` is a thin collector over it.
"""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
import time
import warnings
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from agent_core import settings, tools, workspace
from agent_core.guard import Sandbox, make_can_use_tool, make_pre_tool_use_hook
from agent_core.prompt import build_system_prompt

# We deliberately pre-approve whole tools AND pass can_use_tool. The SDK warns
# that the callback is then shadowed — correct, and fine: containment is enforced
# by the PreToolUse hook, which runs before every rule and mode. can_use_tool is
# only a backstop for calls the allow-list doesn't cover.
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)

# The harness ships far more tools than a chat bot should have — cron scheduling,
# git worktrees, cross-session messaging, sub-workflows. `tools=` is an explicit
# availability allowlist (`--tools a,b,c`), so anything absent here never reaches
# the model, including tools added by future CLI versions.
#
# WebSearch/WebFetch are deliberately absent too: they are Anthropic server-side
# tools that do not exist behind the Z.AI endpoint. mcp__botkit__* replaces them.
_TOOL_PROFILES = {
    "full": ["Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "TodoWrite", "Task", "Skill"],
    "lean": ["Read", "Write", "Edit", "Glob"],
    "files": ["Read", "Write", "Edit"],  # no Bash — see _wants_bash below
    "chat": [],
}
# Bash roughly doubles the tool-schema cost on its own, so it is opt-in per profile.
_BASH_PROFILES = {"full", "lean"}

# Belt-and-braces deny rules; these hold even under bypassPermissions.
_BASH_DENY = [
    "Bash(rm -rf /*)",
    "Bash(sudo *)",
    "Bash(git push *)",
    "Bash(curl * -o /*)",
    "Bash(shutdown *)",
    "Bash(reg *)",
]

# Expected chatter from the CLI when it runs against a non-Anthropic endpoint.
# Kept in the error payload, just not printed on every turn.
_BENIGN_STDERR = (
    "claude.ai connectors are disabled",
    "unrecognized_model",
)

_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


class _AgentLoop:
    """A dedicated event loop on its own thread, used for every agent run.

    Spawning the `claude` subprocess needs a loop that supports subprocesses. On
    Windows that means ProactorEventLoop, but uvicorn picks SelectorEventLoop
    whenever it runs with --reload or multiple workers — and SelectorEventLoop
    raises a bare NotImplementedError when asked to spawn a process, which the
    SDK surfaces as an empty "Failed to start Claude Code:".

    Owning our own loop makes the agent independent of however the web server
    was started, and gives the sync callers (the LINE handler thread) somewhere
    to submit work without spinning up a fresh loop per message.
    """

    def __init__(self) -> None:
        if sys.platform == "win32":
            self._loop = asyncio.ProactorEventLoop()
        else:
            self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="agent-loop", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro):
        """Run a coroutine on the agent loop; returns a concurrent.futures.Future."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def pump(self, agen_factory) -> queue.Queue:
        """Drive an async generator on the agent loop, delivering items to a
        thread-safe queue the caller can drain from any loop or thread."""
        out: queue.Queue = queue.Queue()

        async def _pump() -> None:
            try:
                async for item in agen_factory():
                    out.put(("item", item))
            except BaseException as exc:  # noqa: BLE001 - forwarded to the caller
                out.put(("error", exc))
            finally:
                out.put(("done", None))

        self.submit(_pump())
        return out


_agent_loop: _AgentLoop | None = None
_agent_loop_lock = threading.Lock()


def agent_loop() -> _AgentLoop:
    global _agent_loop
    with _agent_loop_lock:
        if _agent_loop is None:
            _agent_loop = _AgentLoop()
    return _agent_loop


@dataclass
class ToolCall:
    name: str
    input: dict = field(default_factory=dict)
    ok: bool | None = None
    preview: str = ""


@dataclass
class AgentRunResult:
    text: str = ""
    session_id: str = ""
    claude_session_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    num_turns: int = 0
    duration_ms: int = 0
    api_ms: int = 0
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None
    is_error: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["tool_calls"] = [asdict(t) if not isinstance(t, dict) else t for t in self.tool_calls]
        return data

    @property
    def image_url(self) -> str | None:
        return next((a["url"] for a in reversed(self.artifacts) if a["kind"] == "image"), None)

    @property
    def video(self) -> dict | None:
        return next((a for a in reversed(self.artifacts) if a["kind"] == "video"), None)


class AgentRunner:
    def __init__(self, profile: settings.GlmProfile | None = None):
        self.profile = profile or settings.DEFAULT_PROFILE

    # -- options ------------------------------------------------------------
    def _build_options(
        self,
        sandbox: Sandbox,
        artifacts: list[dict],
        blocked: list[dict],
        *,
        resume: str | None,
        max_turns: int,
        model: str | None,
        persona: str | None,
        speaker: str | None,
        allow_bash: bool,
        stderr_sink: list[str],
    ) -> ClaudeAgentOptions:
        if settings.TOOL_OVERRIDE:
            profile = [t for t in settings.TOOL_OVERRIDE if t != "Bash"]
            wants_bash = "Bash" in settings.TOOL_OVERRIDE
        else:
            profile = _TOOL_PROFILES.get(settings.TOOL_PROFILE, _TOOL_PROFILES["files"])
            wants_bash = settings.TOOL_PROFILE in _BASH_PROFILES

        available = list(profile) + [
            f"mcp__{tools.SERVER_NAME}__{name}" for name in settings.MCP_TOOLS
        ]
        disallowed: list[str] = []
        if allow_bash and wants_bash:
            available.append("Bash")
            disallowed += _BASH_DENY

        def _stderr(line: str) -> None:
            # Without this the SDK swallows CLI startup failures and raises
            # "Failed to start Claude Code:" with an empty message.
            stderr_sink.append(line)
            if not any(noise in line for noise in _BENIGN_STDERR):
                print(f"[claude-cli] {line.rstrip()}")

        return ClaudeAgentOptions(
            stderr=_stderr,
            cli_path=settings.CLI_PATH,
            cwd=str(sandbox.session_dir),
            # Read-only siblings; writes into them are still refused by the hook.
            add_dirs=[str(workspace.shared_dir()), str(workspace.skills_dir())],
            # Do not inherit the host's ~/.claude settings — those may point at
            # the real Anthropic API and carry unrelated permissions.
            setting_sources=[],
            # Deliberately NOT the "claude_code" preset: that preset opens with
            # "You are Claude Code, Anthropic's official CLI", which would blow the
            # AI寶寶 persona the moment anyone asks who she is. prompt.py carries
            # the tool-usage discipline the preset would otherwise provide.
            system_prompt=build_system_prompt(sandbox.session_id, persona, speaker),
            model=model or self.profile.model,
            env=self.profile.env(),
            mcp_servers={
                tools.SERVER_NAME: tools.build_tool_server(
                    artifacts, cwd=str(sandbox.session_dir)
                )
            },
            tools=available,
            # Pre-approving them only removes prompting; the hook still vets each call.
            allowed_tools=available,
            disallowed_tools=disallowed,
            permission_mode="acceptEdits",
            hooks={
                "PreToolUse": [
                    HookMatcher(
                        matcher=None,
                        hooks=[make_pre_tool_use_hook(sandbox, blocked.append)],
                    )
                ]
            },
            can_use_tool=make_can_use_tool(sandbox),
            max_turns=max_turns,
            max_budget_usd=settings.MAX_BUDGET_USD,
            effort=settings.EFFORT,
            thinking={"type": "adaptive"} if settings.THINKING else {"type": "disabled"},
            resume=resume,
            include_partial_messages=False,
        )

    # -- streaming ----------------------------------------------------------
    async def stream(self, prompt: str, session_id: str = "default", **kwargs) -> AsyncIterator[dict]:
        """Yield event dicts: init | thinking | text | tool_use | tool_result |
        blocked | result | error.

        The run itself happens on the dedicated agent loop; this just relays its
        output onto whatever loop the caller is using.
        """
        events = agent_loop().pump(lambda: self._stream(prompt, session_id, **kwargs))
        loop = asyncio.get_running_loop()
        while True:
            kind, payload = await loop.run_in_executor(None, events.get)
            if kind == "item":
                yield payload
            elif kind == "error":
                yield {"type": "error", "error": f"{type(payload).__name__}: {payload}"}
            else:
                return

    async def _stream(
        self,
        prompt: str,
        session_id: str = "default",
        *,
        resume: bool = True,
        max_turns: int | None = None,
        model: str | None = None,
        persona: str | None = None,
        speaker: str | None = None,
        allow_bash: bool | None = None,
    ) -> AsyncIterator[dict]:
        """The real run. Always executes on the agent loop."""
        problems = settings.missing_config()
        if problems:
            yield {"type": "error", "error": "; ".join(problems)}
            return

        sandbox = Sandbox(session_id)
        artifacts: list[dict] = []
        blocked: list[dict] = []
        stderr_sink: list[str] = []
        resume_id = workspace.get_claude_session(session_id) if resume else None

        options = self._build_options(
            sandbox,
            artifacts,
            blocked,
            stderr_sink=stderr_sink,
            resume=resume_id,
            max_turns=max_turns or settings.MAX_TURNS,
            model=model,
            persona=persona,
            speaker=speaker,
            allow_bash=settings.ALLOW_BASH if allow_bash is None else allow_bash,
        )

        yield {
            "type": "init",
            "session_id": session_id,
            "cwd": str(sandbox.session_dir),
            "resumed_from": resume_id,
            "model": options.model,
            "base_url": self.profile.base_url,
        }

        started = time.time()
        seen_blocked = 0
        async with _locks[workspace.slugify_session(session_id)]:
            try:
                async for message in query(prompt=prompt, options=options):
                    for event in self._to_events(message, session_id, artifacts):
                        yield event
                    while seen_blocked < len(blocked):
                        yield {"type": "blocked", **blocked[seen_blocked]}
                        seen_blocked += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
                if stderr_sink:
                    detail += " | cli stderr: " + " ".join(stderr_sink[-8:])
                yield {
                    "type": "error",
                    "error": detail,
                    "elapsed_ms": int((time.time() - started) * 1000),
                }

    def _to_events(self, message: Any, session_id: str, artifacts: list[dict]):
        if isinstance(message, SystemMessage):
            if message.subtype == "init":
                yield {"type": "system", "subtype": "init", "data": message.data}
            return

        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    yield {"type": "text", "text": block.text}
                elif isinstance(block, ThinkingBlock):
                    yield {"type": "thinking", "text": block.thinking}
                elif isinstance(block, ToolUseBlock):
                    yield {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
            return

        if isinstance(message, UserMessage):
            content = message.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        yield {
                            "type": "tool_result",
                            "id": block.tool_use_id,
                            "is_error": bool(block.is_error),
                            "preview": _preview(block.content),
                        }
            return

        if isinstance(message, ResultMessage):
            if message.session_id:
                workspace.set_claude_session(session_id, message.session_id)
            usage = message.usage or {}
            yield {
                "type": "result",
                "text": message.result or "",
                "claude_session_id": message.session_id,
                "num_turns": message.num_turns,
                "duration_ms": message.duration_ms,
                # duration_api_ms is time spent waiting on the model; the gap
                # between it and duration_ms is CLI startup + local tool work.
                "api_ms": message.duration_api_ms,
                "cost_usd": message.total_cost_usd,
                "is_error": message.is_error,
                "usage": {
                    "input": usage.get("input_tokens"),
                    "output": usage.get("output_tokens"),
                    "cache_read": usage.get("cache_read_input_tokens"),
                    "cache_write": usage.get("cache_creation_input_tokens"),
                },
                "artifacts": list(artifacts),
            }

    # -- collected ----------------------------------------------------------
    async def run(self, prompt: str, session_id: str = "default", **kwargs) -> AgentRunResult:
        """Collected result. Runs on the agent loop, awaited from the caller's."""
        future = agent_loop().submit(self._collect(prompt, session_id, **kwargs))
        return await asyncio.wrap_future(future)

    async def _collect(self, prompt: str, session_id: str = "default", **kwargs) -> AgentRunResult:
        result = AgentRunResult(session_id=session_id)
        texts: list[str] = []
        pending: dict[str, ToolCall] = {}

        async for event in self._stream(prompt, session_id, **kwargs):
            kind = event["type"]
            if kind == "text":
                texts.append(event["text"])
            elif kind == "tool_use":
                call = ToolCall(name=event["name"], input=event["input"])
                pending[event["id"]] = call
                result.tool_calls.append(call)
            elif kind == "tool_result":
                call = pending.get(event["id"])
                if call:
                    call.ok = not event["is_error"]
                    call.preview = event["preview"]
            elif kind == "blocked":
                result.blocked.append({"tool": event.get("tool"), "reason": event.get("reason")})
            elif kind == "result":
                result.text = event["text"] or "\n".join(texts)
                result.claude_session_id = event["claude_session_id"]
                result.num_turns = event["num_turns"] or 0
                result.duration_ms = event["duration_ms"] or 0
                result.api_ms = event.get("api_ms") or 0
                result.usage = event.get("usage") or {}
                result.cost_usd = event["cost_usd"]
                result.is_error = bool(event["is_error"])
                result.artifacts = event["artifacts"]
            elif kind == "error":
                result.is_error = True
                result.error = event["error"]

        if not result.text:
            result.text = "\n".join(texts).strip()
        result.files = workspace.list_files(session_id, limit=50)
        return result


def _preview(content: Any, limit: int = 400) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    else:
        text = str(content)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


_default_runner: AgentRunner | None = None


def get_runner() -> AgentRunner:
    global _default_runner
    if _default_runner is None:
        workspace.sync_skills()
        _default_runner = AgentRunner()
    return _default_runner


def run_sync(prompt: str, session_id: str = "default", **kwargs) -> AgentRunResult:
    """Blocking helper for non-async callers (the LINE webhook handler thread)."""
    runner = get_runner()
    result = AgentRunResult(session_id=session_id)
    future = agent_loop().submit(runner._collect(prompt, session_id, **kwargs))
    try:
        result = future.result(timeout=settings.RUN_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        result.is_error = True
        result.error = f"{type(exc).__name__}: {exc}"
    return result

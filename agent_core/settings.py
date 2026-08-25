"""
Agent settings — wraps the Claude Code CLI so it talks to Z.AI's GLM models.

Claude Code speaks the Anthropic Messages API. Z.AI exposes an Anthropic-compatible
endpoint at https://api.z.ai/api/anthropic, so pointing ANTHROPIC_BASE_URL there and
mapping the opus/sonnet/haiku aliases onto GLM model ids gives us the full Claude Code
harness (Read/Write/Edit/Bash/Glob/Grep/Task/skills/hooks) driven by GLM.

Everything is env-driven so nothing here needs editing to swap providers.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def find_cli() -> str | None:
    """Locate a `claude` binary the SDK will actually run.

    On Windows the SDK refuses npm's `claude.cmd` shim (cmd.exe argument
    injection), so we must hand it a native executable. The npm package ships
    one at node_modules/@anthropic-ai/claude-code/bin/claude.exe; the native
    installer puts one in ~/.local/bin. Check both before giving up.
    """
    override = os.environ.get("AGENT_CLI_PATH")
    if override:
        return override if Path(override).exists() else None

    if platform.system() == "Windows":
        candidates = [
            Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe",  # noqa: E501
            Path.home() / ".local/bin/claude.exe",
            Path.home() / "AppData/Local/Programs/claude/claude.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        found = shutil.which("claude.exe")
        return found

    return shutil.which("claude")


CLI_PATH = find_cli()

# --- Z.AI / GLM ------------------------------------------------------------
# Accept several names so an existing .env keeps working.
GLM_API_KEY = (
    os.environ.get("GLM_API_KEY")
    or os.environ.get("ZAI_API_KEY")
    or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    or os.environ.get("GPT_API_KEY")  # legacy zhipuai key from the old bot
    or ""
)
# BigModel (open.bigmodel.cn) is the platform that serves the *free* GLM tier and
# also exposes an Anthropic-compatible endpoint. z.ai's endpoint
# (https://api.z.ai/api/anthropic) is paid-only — switch GLM_BASE_URL to it if you
# ever take a GLM Coding Plan.
GLM_BASE_URL = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/anthropic")

# Claude Code always asks for one of three aliases; map each onto a GLM model.
# glm-4.7-flash is the current free model (200K context, agentic-tool capable) and
# replaced glm-4.5-flash / glm-4-flash. Everything points at it so no run can
# silently fall onto a paid model.
FREE_MODEL = os.environ.get("GLM_FREE_MODEL", "glm-4.7-flash")
GLM_MODEL_OPUS = os.environ.get("GLM_MODEL_OPUS", FREE_MODEL)
GLM_MODEL_SONNET = os.environ.get("GLM_MODEL_SONNET", FREE_MODEL)
GLM_MODEL_HAIKU = os.environ.get("GLM_MODEL_HAIKU", FREE_MODEL)

# The model the main loop runs on. An alias ("sonnet") resolves through the map
# above; a concrete id ("glm-4.7-flash") is sent verbatim.
AGENT_MODEL = os.environ.get("AGENT_MODEL", FREE_MODEL)

API_TIMEOUT_MS = os.environ.get("API_TIMEOUT_MS", "600000")

# The chat model has no vision, so image questions are routed to a separate
# free vision model via the describe_image tool.
VISION_MODEL = os.environ.get("GLM_VISION_MODEL", "glm-4.6v-flash")

# The free vision models get saturated and answer 429 ("该模型当前访问量过大")
# rather than queueing, so describe_image walks this list until one responds.
# The configured model is always tried first.
VISION_FALLBACKS = [
    m.strip()
    for m in os.environ.get(
        "GLM_VISION_FALLBACKS", "glm-4v-flash,glm-4.1v-thinking-flash"
    ).split(",")
    if m.strip()
]
VISION_MODELS = list(dict.fromkeys([VISION_MODEL, *VISION_FALLBACKS]))

# --- Sandbox ---------------------------------------------------------------
# The agent NEVER runs with the repo as cwd. Everything happens under this root.
WORKSPACE_ROOT = Path(
    os.environ.get("AGENT_WORKSPACE_ROOT", str(PROJECT_ROOT / "workspace"))
).resolve()

# Read-only material mounted inside the workspace root (skills, shared datasets).
SHARED_DIRNAME = "_shared"
SKILLS_DIRNAME = "_skills"

# Where skills are copied from into <workspace_root>/_skills (read-only for the agent).
SKILLS_SOURCE = Path(os.environ.get("AGENT_SKILLS_SOURCE", str(PROJECT_ROOT / "skills")))

# --- Run limits ------------------------------------------------------------
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "30"))
RUN_TIMEOUT_S = float(os.environ.get("AGENT_RUN_TIMEOUT", "300"))
MAX_BUDGET_USD = float(os.environ.get("AGENT_MAX_BUDGET_USD", "0") or 0) or None
ALLOW_BASH = os.environ.get("AGENT_ALLOW_BASH", "true").lower() not in ("0", "false", "no")
EFFORT = os.environ.get("AGENT_EFFORT") or None  # low|medium|high|xhigh|max

# Extended thinking is off by default. On a free flash model it can spend a
# minute reasoning about "你好" before answering, which is unusable for chat.
# Set AGENT_THINKING=true if you want it back for harder work.
THINKING = os.environ.get("AGENT_THINKING", "false").lower() in ("1", "true", "yes")

# How many tools to expose. Every tool's schema is re-sent on every request, and
# the free tier meters tokens per minute — so a fat tool list doesn't just cost
# context, it eats your rate limit and gets you throttled sooner.
#   full  — everything (Task, Skill, TodoWrite, notebooks, grep/glob)
#   lean  — files + Glob + the built-in Bash tool (expensive, see README)
#   files — Read/Write/Edit only            ← default; shell comes from run_shell
#   chat  — no filesystem or shell at all, just search and media
# Note the default deliberately omits the built-in Bash: the bot's own run_shell
# MCP tool gives the same capability for about a tenth of the schema size.
TOOL_PROFILE = os.environ.get("AGENT_TOOL_PROFILE", "files").lower()

# Explicit override, e.g. AGENT_TOOLS="Read,Write,Edit". Wins over TOOL_PROFILE.
TOOL_OVERRIDE = [t.strip() for t in os.environ.get("AGENT_TOOLS", "").split(",") if t.strip()]

# Which of the bot's own MCP tools to expose. Each one's schema is small, but
# they add up; drop the ones this deployment doesn't need.
MCP_TOOLS = [
    t.strip()
    for t in os.environ.get(
        "AGENT_MCP_TOOLS",
        "run_shell,web_search,fetch_url,generate_image,search_image,"
        "describe_image,generate_video",
    ).split(",")
    if t.strip()
]

# Where we persist "our session key -> claude session id" so conversations resume.
SESSION_INDEX = WORKSPACE_ROOT / "_sessions.json"


@dataclass(frozen=True)
class GlmProfile:
    """Resolved provider configuration for one agent run."""

    api_key: str = GLM_API_KEY
    base_url: str = GLM_BASE_URL
    model: str = AGENT_MODEL
    opus: str = GLM_MODEL_OPUS
    sonnet: str = GLM_MODEL_SONNET
    haiku: str = GLM_MODEL_HAIKU
    extra_env: dict = field(default_factory=dict)

    def env(self) -> dict:
        """Environment handed to the spawned `claude` process.

        Note ANTHROPIC_API_KEY is explicitly blanked: if it leaks in from the host
        shell the CLI would authenticate against Anthropic instead of Z.AI.
        """
        env = {
            "ANTHROPIC_BASE_URL": self.base_url,
            "ANTHROPIC_AUTH_TOKEN": self.api_key,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": self.opus,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": self.sonnet,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": self.haiku,
            "API_TIMEOUT_MS": API_TIMEOUT_MS,
            # Keep the sandboxed run quiet and self-contained.
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_AUTOUPDATER": "1",
        }
        env.update(self.extra_env)
        return env


DEFAULT_PROFILE = GlmProfile()


def missing_config() -> list[str]:
    """Return a list of human-readable config problems, empty when healthy."""
    problems = []
    if not GLM_API_KEY:
        problems.append(
            "GLM_API_KEY is not set — create one at https://open.bigmodel.cn "
            "(free tier) and put it in .env"
        )
    if not find_cli():
        problems.append(
            "no runnable `claude` binary found. Install it "
            "(`npm i -g @anthropic-ai/claude-code`, or on Windows "
            "`irm https://claude.ai/install.ps1 | iex`) or set AGENT_CLI_PATH to a "
            "native claude executable — the npm .cmd shim is not usable."
        )
    return problems

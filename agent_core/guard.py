"""
Guard — keeps the agent inside its workspace.

Two layers, because they fail differently:

  1. PreToolUse hook  — runs before every tool call, before deny/allow rules and
     before the permission mode. A hook deny holds even under
     `permission_mode="bypassPermissions"`, which is what makes it usable as a
     sandbox rather than a suggestion.
  2. can_use_tool     — final fallback for anything the hook let through and no
     rule resolved. Denies by default.

Scope:
  WRITE  -> the session directory only
  READ   -> the session directory, workspace/_shared, workspace/_skills

Honest limitation: the Bash guard is string analysis over a shell command, so it
is best-effort, not a kernel boundary. It blocks absolute paths outside the
workspace, `..` escapes, `~`/env-var expansion and a destructive-command
denylist. For a hard boundary run the whole API in a container (see README) or
set AGENT_ALLOW_BASH=false.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from claude_agent_sdk import HookContext, PermissionResultAllow, PermissionResultDeny

from agent_core import workspace

# Tool -> which input keys carry a filesystem path, and whether it is a write.
_PATH_ARGS: dict[str, tuple[tuple[str, ...], bool]] = {
    "Read": (("file_path",), False),
    "Glob": (("path",), False),
    "Grep": (("path",), False),
    "Write": (("file_path",), True),
    "Edit": (("file_path",), True),
    "MultiEdit": (("file_path",), True),
    "NotebookEdit": (("notebook_path",), True),
}

# Commands we refuse outright, regardless of the paths they mention.
_BANNED_COMMANDS = re.compile(
    r"(?<![\w-])("
    r"sudo|doas|shutdown|reboot|mkfs|diskpart|format|fdisk"
    r"|reg\s+(add|delete|import)|net\s+user|schtasks|sc\s+(create|delete|config)"
    r"|useradd|usermod|passwd|chown|chmod\s+777"
    r"|Set-ExecutionPolicy|Invoke-WebRequest\s+-OutFile"
    r"|npm\s+(publish|login)|pip\s+config|git\s+push|gh\s+(pr|release)"
    r")(?![\w-])",
    re.IGNORECASE,
)

# Shell constructs that let a path escape our static analysis.
_EXPANSIONS = re.compile(r"~[/\\]|\$HOME|\$env:|\$\{?USERPROFILE|%USERPROFILE%|%APPDATA%|%TEMP%")

_URLISH = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")


class Sandbox:
    """Resolved read/write roots for one agent run."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.session_dir = workspace.session_dir(session_id)
        self.write_roots = [self.session_dir]
        self.read_roots = [
            self.session_dir,
            workspace.shared_dir(),
            workspace.skills_dir(),
        ]

    # -- path checks --------------------------------------------------------
    def check_path(self, raw: Any, write: bool) -> str | None:
        """Return a denial reason, or None when the path is acceptable."""
        if raw in (None, ""):
            return None
        text = str(raw)
        if _EXPANSIONS.search(text):
            return f"path uses shell/home expansion which is not allowed here: {text}"
        roots = self.write_roots if write else self.read_roots
        if workspace.resolve_within(text, roots, base=self.session_dir) is None:
            zone = "writable" if write else "readable"
            return (
                f"'{text}' is outside the agent {zone} workspace. "
                f"You may only work inside {self.session_dir} "
                f"(plus read-only {workspace.shared_dir().name}/ and "
                f"{workspace.skills_dir().name}/). Use relative paths."
            )
        return None

    def check_tool(self, tool_name: str, tool_input: dict) -> str | None:
        """Return a denial reason for a tool call, or None to let it proceed."""
        keys, is_write = _PATH_ARGS.get(tool_name, ((), False))
        for key in keys:
            reason = self.check_path(tool_input.get(key), write=is_write)
            if reason:
                return reason

        # Our own run_shell replaces the built-in Bash tool, and gets exactly the
        # same command vetting — the guard must not care which one the model used.
        if tool_name == "Bash" or tool_name.endswith("__run_shell"):
            return self.check_bash(str(tool_input.get("command", "")))

        return None

    # -- bash ---------------------------------------------------------------
    def check_bash(self, command: str) -> str | None:
        if not command.strip():
            return None

        banned = _BANNED_COMMANDS.search(command)
        if banned:
            return f"command '{banned.group(1)}' is blocked in the sandboxed workspace"

        if _EXPANSIONS.search(command):
            return "command expands ~ or an environment path, which could escape the workspace"

        try:
            tokens = shlex.split(command, posix=False)
        except ValueError:
            tokens = command.split()

        for token in tokens:
            candidate = token.strip("'\"")
            if not candidate or _URLISH.match(candidate):
                continue
            is_absolute = candidate.startswith(("/", "\\")) or bool(_WIN_ABS.match(candidate))
            has_traversal = ".." in Path(candidate).parts
            if not (is_absolute or has_traversal):
                continue  # relative and non-escaping — cwd already confines it
            if workspace.resolve_within(candidate, self.read_roots, base=self.session_dir) is None:
                return (
                    f"'{candidate}' points outside the agent workspace. "
                    f"Work with paths relative to {self.session_dir}."
                )
        return None


# ---------------------------------------------------------------------------
# SDK adapters
# ---------------------------------------------------------------------------

def make_pre_tool_use_hook(sandbox: Sandbox, on_event=None):
    """PreToolUse hook that denies any tool call leaving the workspace."""

    async def _hook(input_data: dict, tool_use_id: str | None, context: HookContext) -> dict:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input") or {}
        reason = sandbox.check_tool(tool_name, tool_input)
        if reason:
            if on_event:
                on_event({"type": "blocked", "tool": tool_name, "reason": reason})
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"🔒 Sandbox: {reason}",
                }
            }
        return {}

    return _hook


def make_can_use_tool(sandbox: Sandbox):
    """Final fallback: allow only if the sandbox check passes."""

    async def _can_use_tool(tool_name: str, tool_input: dict, context) -> Any:
        reason = sandbox.check_tool(tool_name, tool_input)
        if reason:
            return PermissionResultDeny(message=f"🔒 Sandbox: {reason}")
        return PermissionResultAllow()

    return _can_use_tool

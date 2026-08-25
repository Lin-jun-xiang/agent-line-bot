"""
Sandbox containment tests — no API key or network required.

These assert the guard rejects every path that leaves the session workspace,
which is the property the whole design rests on.

    python -m pytest tests/test_sandbox.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_core import settings, tools, workspace  # noqa: E402
from agent_core.guard import Sandbox  # noqa: E402

SESSION = "pytest-sandbox"


@pytest.fixture(scope="module")
def sandbox() -> Sandbox:
    workspace.shared_dir()
    workspace.skills_dir().mkdir(parents=True, exist_ok=True)
    return Sandbox(SESSION)


# --- writes ----------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "notes/report.md",
        "./data.csv",
        "sub/dir/deep/file.txt",
    ],
)
def test_writes_inside_session_are_allowed(sandbox, path):
    assert sandbox.check_tool("Write", {"file_path": path}) is None


@pytest.mark.parametrize(
    "path",
    [
        "../other-session/steal.txt",
        "../../main.py",
        "../../../Windows/System32/drivers/etc/hosts",
        str(settings.PROJECT_ROOT / "config.py"),
        str(settings.PROJECT_ROOT / "line_bot" / "routes.py"),
        "/etc/passwd",
        "C:\\Windows\\System32\\evil.dll",
        "~/.ssh/id_rsa",
    ],
)
def test_writes_outside_session_are_denied(sandbox, path):
    assert sandbox.check_tool("Write", {"file_path": path}) is not None


def test_write_into_readonly_shared_is_denied(sandbox):
    target = workspace.shared_dir() / "x.txt"
    assert sandbox.check_tool("Write", {"file_path": str(target)}) is not None


# --- reads -----------------------------------------------------------------

def test_read_of_shared_is_allowed(sandbox):
    target = workspace.shared_dir() / "notes.md"
    assert sandbox.check_tool("Read", {"file_path": str(target)}) is None


def test_read_of_skills_is_allowed(sandbox):
    target = workspace.skills_dir() / "finance" / "SKILL.md"
    assert sandbox.check_tool("Read", {"file_path": str(target)}) is None


@pytest.mark.parametrize(
    "path",
    [
        str(settings.PROJECT_ROOT / "config.py"),
        str(settings.PROJECT_ROOT / ".env"),
        "../../agent_core/settings.py",
    ],
)
def test_read_of_source_tree_is_denied(sandbox, path):
    assert sandbox.check_tool("Read", {"file_path": path}) is not None


def test_grep_outside_workspace_is_denied(sandbox):
    assert sandbox.check_tool("Grep", {"pattern": "KEY", "path": str(settings.PROJECT_ROOT)}) is not None


# --- bash ------------------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "python analyze.py",
        "ls -la",
        "python -c \"print(sum(range(100)))\"",
        "mkdir -p out && python gen.py > out/result.txt",
    ],
)
def test_relative_bash_is_allowed(sandbox, command):
    assert sandbox.check_bash(command) is None


@pytest.mark.parametrize(
    "command",
    [
        "cat ../../config.py",
        "cp ../../.env ./stolen.env",
        f"cat {settings.PROJECT_ROOT / 'config.py'}",
        "python C:\\junxiang\\chatgpt-line-bot\\main.py",
        "cat ~/.ssh/id_rsa",
        "cat $HOME/.aws/credentials",
        "type %USERPROFILE%\\.gitconfig",
        "sudo rm -rf /",
        "git push origin main",
        "reg add HKLM\\Software\\Evil",
        "ls /etc",
    ],
)
def test_escaping_bash_is_denied(sandbox, command):
    assert sandbox.check_bash(command) is not None


# --- tools that open files themselves --------------------------------------
# describe_image and generate_video read files directly, so the PreToolUse hook
# cannot vet them (it only sees `path` as an opaque string). They call
# tools._resolve_in_workspace instead, which must be just as strict.

@pytest.fixture(scope="module")
def tool_cwd() -> str:
    return str(workspace.session_dir(SESSION))


@pytest.mark.parametrize(
    "path",
    [
        "uploads/photo.png",
        "./chart.png",
        "sub/dir/frame.jpg",
    ],
)
def test_tool_paths_inside_session_are_allowed(tool_cwd, path):
    assert tools._resolve_in_workspace(path, tool_cwd) is not None


@pytest.mark.parametrize(
    "path",
    [
        "../../config.py",
        "../../.env",
        "../other-session/uploads/photo.png",
        r"C:\Windows\win.ini",
        "/etc/passwd",
        str(settings.PROJECT_ROOT / "main.py"),
    ],
)
def test_tool_paths_outside_session_are_refused(tool_cwd, path):
    assert tools._resolve_in_workspace(path, tool_cwd) is None


def test_tool_can_read_shared_and_skills(tool_cwd):
    assert tools._resolve_in_workspace(str(workspace.shared_dir() / "a.png"), tool_cwd)
    assert tools._resolve_in_workspace(str(workspace.skills_dir() / "b.png"), tool_cwd)


# --- slug safety -----------------------------------------------------------

@pytest.mark.parametrize("raw", ["../../escape", "..", "/", "_shared", "C:\\Windows"])
def test_hostile_session_ids_stay_inside_workspace(raw):
    d = workspace.session_dir(raw, create=False)
    assert workspace.is_within(d, workspace.workspace_root())
    assert d.parent == workspace.workspace_root()
    assert d.name not in ("_shared", "_skills", "..", "")

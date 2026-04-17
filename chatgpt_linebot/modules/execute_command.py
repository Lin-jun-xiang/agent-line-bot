"""
Execute Command — Sandboxed shell command executor.
Only allows execution of registered skill binaries.
Blocks arbitrary shell commands for security.
"""

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set

from chatgpt_linebot.modules.skill_loader import discover_skills

# ---------------------------------------------------------------------------
# Whitelist — built once from registered skills
# ---------------------------------------------------------------------------
_allowed_binaries: Optional[Set[str]] = None


def _get_allowed_binaries() -> Set[str]:
    """Build set of allowed binary paths from registered skills (cached)."""
    global _allowed_binaries
    if _allowed_binaries is None:
        _allowed_binaries = set()
        for skill in discover_skills():
            bin_path = skill.get("bin")
            if bin_path:
                # Normalise to resolve symlinks / case on Windows
                _allowed_binaries.add(str(Path(bin_path).resolve()))
    return _allowed_binaries


# Dangerous shell patterns (even if binary is allowed, block chaining)
_DANGEROUS_PATTERNS = re.compile(
    r"[;&|`$]"       # shell chaining / expansion
    r"|>\s*"          # output redirection
    r"|<\s*"          # input redirection
    r"|\bsudo\b"
    r"|\brm\b"
    r"|\bdel\b"
    r"|\bformat\b"
    r"|\bmkfs\b"
    r"|\bdd\b"
    r"|\bcurl\b"
    r"|\bwget\b"
    r"|\bnc\b"
    r"|\bpowershell\b"
    r"|\bcmd\.exe\b"
)


def _validate_command(command: str) -> Optional[str]:
    """
    Validate a command string against the whitelist.

    Returns:
        None if valid, or an error message string if blocked.
    """
    if not command or not command.strip():
        return "❌ 空指令"

    # Block dangerous shell patterns
    if _DANGEROUS_PATTERNS.search(command):
        return "❌ 指令包含不允許的字元或關鍵字，僅能執行已註冊的技能指令"

    # Parse command tokens
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "❌ 指令格式錯誤"

    if not tokens:
        return "❌ 空指令"

    # The command must be:  python <allowed_binary> [args...]
    # or:                   <allowed_binary> [args...]
    allowed = _get_allowed_binaries()

    # Find the binary path in the tokens
    binary_path = None
    if tokens[0].lower() in ("python", "python3", "py"):
        if len(tokens) < 2:
            return "❌ 缺少要執行的腳本路徑"
        binary_path = tokens[1]
    else:
        binary_path = tokens[0]

    # Resolve and check
    try:
        resolved = str(Path(binary_path).resolve())
    except Exception:
        return f"❌ 無法解析路徑：{binary_path}"

    if resolved not in allowed:
        skill_names = ", ".join(
            s["name"] for s in discover_skills() if s.get("bin")
        )
        return (
            f"❌ 不允許執行此程式：{binary_path}\n"
            f"僅可執行已註冊的技能：{skill_names}"
        )

    return None  # valid


def execute_command(
    command: str,
    timeout: int = 30,
    cwd: str = None,
    env_override: dict = None,
) -> str:
    """
    Execute a validated shell command and return its output.

    Security:
    - Only registered skill binaries can be executed
    - Shell chaining (;, |, &&, ``, $()) is blocked
    - Dangerous commands (rm, curl, sudo, ...) are blocked
    """
    # --- Validate first ---
    error = _validate_command(command)
    if error:
        return error

    env = os.environ.copy()
    if env_override:
        env.update(env_override)

    try:
        # Use shell=False with token list for safety
        tokens = shlex.split(command)        # Auto-detect skill root from binary path in tokens
        # e.g. python /path/skills/finance-tracker/bin/finance.py
        #   → skill_root = /path/skills/finance-tracker
        #   → PYTHONPATH += skill_root/lib
        if cwd is None:
            for token in tokens:
                try:
                    p = Path(token)
                    if p.suffix == ".py" and p.parent.name == "bin":
                        # bin/finance.py → parent is bin/, parent.parent is skill root
                        skill_root = p.parent.parent
                        if not skill_root.is_absolute():
                            skill_root = (Path.cwd() / skill_root).resolve()
                        else:
                            skill_root = skill_root.resolve()
                        cwd = str(skill_root)
                        lib_dir = str(skill_root / "lib")
                        existing = env.get("PYTHONPATH", "")
                        if lib_dir not in existing:
                            env["PYTHONPATH"] = (
                                f"{lib_dir}{os.pathsep}{existing}"
                                if existing
                                else lib_dir
                            )
                        print(f"  [execute_command] cwd={cwd}, PYTHONPATH={env.get('PYTHONPATH', '')}")
                        break
                except Exception as ex:
                    print(f"  [execute_command] path detection error for '{token}': {ex}")

        result = subprocess.run(
            tokens,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )

        output = result.stdout.strip()
        if result.returncode != 0 and result.stderr:
            output = (
                output + "\n" + result.stderr.strip()
                if output
                else result.stderr.strip()
            )

        return output if output else "✅ 完成（無輸出）"

    except subprocess.TimeoutExpired:
        return f"❌ 指令超時（{timeout}秒）"
    except Exception as e:
        return f"❌ 執行錯誤：{e}"

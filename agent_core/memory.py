"""
Long-term memory — one MEMORY.md per conversation.

Every session directory owns a MEMORY.md holding durable facts about that person
or group: name, preferences, ongoing projects, running jokes. It is injected into
the system prompt on every turn (so recall costs nothing extra) and the agent
edits it with its normal Write/Edit tools when it learns something worth keeping.

Short-term turn-to-turn context is handled separately by the CLI's own session
resume; this file is only for what should survive `@init` of the chat thread.
"""

from __future__ import annotations

from agent_core import workspace

MEMORY_FILENAME = "MEMORY.md"

_TEMPLATE = """# 關於這個對話

<!-- 長期記憶。只寫「以後還用得到」的事實，不要寫流水帳。 -->

## 對象
- （還不知道對方是誰）

## 偏好與習慣
-

## 進行中的事
-

## 不要再做的事
-
"""

# This whole file is injected into the system prompt on every single turn, and on
# the free tier prompt size drives latency hard. Keep it tiny: ~1200 chars is
# roughly 600 tokens, enough for a few dozen facts about one person.
MAX_CHARS = 1200
MAX_LINES = 30


def memory_path(session_id: str):
    return workspace.session_dir(session_id) / MEMORY_FILENAME


def ensure(session_id: str) -> str:
    """Create MEMORY.md if missing and return its contents."""
    path = memory_path(session_id)
    if not path.exists():
        path.write_text(_TEMPLATE, encoding="utf-8")
        return _TEMPLATE
    return read(session_id)


def read(session_id: str) -> str:
    path = memory_path(session_id)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    # Drop blank lines and comments before measuring — they cost tokens too.
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("<!--")]
    truncated = len(lines) > MAX_LINES
    text = "\n".join(lines[:MAX_LINES])
    if len(text) > MAX_CHARS:
        text, truncated = text[:MAX_CHARS], True
    if truncated:
        text += "\n<!-- 記憶太長已截斷，請精簡 -->"
    return text


def write(session_id: str, content: str) -> None:
    memory_path(session_id).write_text(content, encoding="utf-8")


def clear(session_id: str) -> None:
    path = memory_path(session_id)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Per-conversation persona override (LINE's `@prompt`)
# ---------------------------------------------------------------------------
# Stored on disk rather than in a dict so a redeploy or restart doesn't silently
# reset everyone's customisation back to the default personality.

PERSONA_FILENAME = "PERSONA.md"
PERSONA_MAX_CHARS = 1500


def persona_path(session_id: str):
    return workspace.session_dir(session_id) / PERSONA_FILENAME


def read_persona(session_id: str) -> str | None:
    path = persona_path(session_id)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return text[:PERSONA_MAX_CHARS] or None


def write_persona(session_id: str, text: str) -> None:
    persona_path(session_id).write_text(text.strip()[:PERSONA_MAX_CHARS], encoding="utf-8")


def clear_persona(session_id: str) -> bool:
    path = persona_path(session_id)
    if path.exists():
        path.unlink()
        return True
    return False

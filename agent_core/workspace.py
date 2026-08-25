"""
Workspace — the only place on disk the agent is allowed to touch.

Layout:

    workspace/
      _sessions.json          session-key -> claude session id index
      _shared/                read-only material every session can read
      _skills/                read-only copy of ./skills
      <session-slug>/         per-session working dir  (agent cwd, read+write)

`resolve_within` is the single containment primitive: everything in guard.py
funnels through it, so the rule lives in exactly one place.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Iterable

from agent_core import settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_RESERVED = {"_shared", "_skills", "_sessions.json"}


def slugify_session(session_id: str) -> str:
    """Turn an arbitrary id (LINE user id, uuid, free text) into a safe dir name.

    Anything that isn't obviously safe gets hashed, so `../../etc` can never
    become a directory name.
    """
    raw = (session_id or "").strip()
    if not raw:
        raw = "anonymous"
    slug = _SAFE.sub("-", raw).strip("-.")[:64]
    if not slug or slug in _RESERVED or slug != raw:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        slug = f"{slug[:32] or 'sess'}-{digest}" if slug else f"sess-{digest}"
    return slug


def workspace_root() -> Path:
    root = settings.WORKSPACE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def shared_dir() -> Path:
    d = workspace_root() / settings.SHARED_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def skills_dir() -> Path:
    return workspace_root() / settings.SKILLS_DIRNAME


def sync_skills() -> Path | None:
    """Mirror ./skills into workspace/_skills so the agent can read them.

    We copy rather than expose the repo directory: the agent gets skills without
    ever holding a path that points at source code.
    """
    src = settings.SKILLS_SOURCE
    if not src.exists():
        return None
    dst = skills_dir()
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    return dst


def session_dir(session_id: str, create: bool = True) -> Path:
    """Working directory for one conversation."""
    d = workspace_root() / slugify_session(session_id)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def list_sessions() -> list[dict]:
    root = workspace_root()
    out = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in _RESERVED:
            continue
        files = [p for p in child.rglob("*") if p.is_file()]
        out.append(
            {
                "slug": child.name,
                "files": len(files),
                "bytes": sum(p.stat().st_size for p in files),
                "modified": child.stat().st_mtime,
            }
        )
    return out


def list_files(session_id: str, limit: int = 500) -> list[dict]:
    d = session_dir(session_id, create=False)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out.append(
                {
                    "path": p.relative_to(d).as_posix(),
                    "bytes": p.stat().st_size,
                    "modified": p.stat().st_mtime,
                }
            )
        if len(out) >= limit:
            break
    return out


def read_file(session_id: str, rel_path: str, max_bytes: int = 200_000) -> str:
    """Read a file out of a session workspace, refusing anything outside it."""
    base = session_dir(session_id, create=False)
    target = resolve_within(rel_path, [base], base=base)
    if target is None:
        raise PermissionError(f"path escapes the session workspace: {rel_path}")
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    data = target.read_bytes()[:max_bytes]
    return data.decode("utf-8", errors="replace")


def clear_session(session_id: str) -> bool:
    d = session_dir(session_id, create=False)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------

def _normalize(path: Path) -> Path:
    """Absolute, symlink-free-as-possible, case-normalised on Windows."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = Path(os.path.abspath(str(path)))
    if os.name == "nt":
        return Path(os.path.normcase(str(resolved)))
    return resolved


def is_within(candidate: Path, root: Path) -> bool:
    """True when `candidate` is `root` or lives underneath it."""
    c, r = _normalize(candidate), _normalize(root)
    try:
        c.relative_to(r)
        return True
    except ValueError:
        return False


def resolve_within(
    raw_path: str | Path,
    roots: Iterable[Path],
    base: Path | None = None,
) -> Path | None:
    """Resolve `raw_path` (relative to `base`) and return it only if it lands
    inside one of `roots`. Returns None when it escapes — callers treat None as
    "deny".
    """
    if raw_path is None:
        return None
    p = Path(str(raw_path))
    if not p.is_absolute() and base is not None:
        p = base / p
    try:
        resolved = p.resolve()
    except OSError:
        resolved = Path(os.path.abspath(str(p)))
    for root in roots:
        if is_within(resolved, root):
            return resolved
    return None


def contained(raw_path: str | Path, roots: Iterable[Path], base: Path | None = None) -> bool:
    """Boolean form of resolve_within — clearer at call sites that only test."""
    return resolve_within(raw_path, roots, base) is not None


# ---------------------------------------------------------------------------
# Session id index (our key -> claude sdk session id)
# ---------------------------------------------------------------------------

def _load_index() -> dict:
    path = settings.SESSION_INDEX
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_claude_session(session_id: str) -> str | None:
    return _load_index().get(slugify_session(session_id))


def set_claude_session(session_id: str, claude_session_id: str) -> None:
    workspace_root()
    index = _load_index()
    index[slugify_session(session_id)] = claude_session_id
    settings.SESSION_INDEX.write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def forget_claude_session(session_id: str) -> None:
    index = _load_index()
    if index.pop(slugify_session(session_id), None) is not None:
        settings.SESSION_INDEX.write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
        )

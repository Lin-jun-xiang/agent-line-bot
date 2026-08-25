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


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

def uploads_dir(session_id: str, create: bool = True) -> Path:
    d = session_dir(session_id, create=create) / settings.UPLOADS_DIRNAME
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def upload_files(session_id: str) -> list[Path]:
    """Every stored upload, newest first.

    The one place that knows the manifest is not itself an upload — pruning and
    the prompt listing both go through here so neither can leak or delete it.
    """
    folder = uploads_dir(session_id, create=False)
    if not folder.is_dir():
        return []
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.name != settings.UPLOADS_MANIFEST),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _manifest_path(session_id: str) -> Path:
    return uploads_dir(session_id, create=False) / settings.UPLOADS_MANIFEST


def read_upload_senders(session_id: str) -> dict:
    """filename -> {"sender": str, "at": str, "caption": str}.

    Missing or corrupt reads as empty — a broken sidecar must not take the whole
    prompt down with it.
    """
    path = _manifest_path(session_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_manifest(session_id: str, data: dict) -> None:
    folder = uploads_dir(session_id)
    (folder / settings.UPLOADS_MANIFEST).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def set_upload_caption(session_id: str, filename: str, caption: str) -> bool:
    """Cache a description of an upload. False if the file is already gone.

    Cached so that asking about the same photo twice costs one vision call, not
    two. The existence check matters because rotation may have removed the file
    between the question and the answer, and writing the caption back then would
    resurrect a manifest entry for a file that no longer exists.
    """
    caption = (caption or "").strip()
    if not caption:
        return False
    if not (uploads_dir(session_id, create=False) / filename).is_file():
        return False

    manifest = read_upload_senders(session_id)
    entry = manifest.get(filename) or {}
    entry["caption"] = caption[: settings.DESCRIBE_MAX_CHARS]
    manifest[filename] = entry
    _write_manifest(session_id, manifest)
    return True


def prune_uploads(session_id: str, keep: int | None = None) -> int:
    """Delete all but the newest `keep` uploads. Returns how many were removed.

    Nothing else in the system ever deletes an upload, so without this a single
    chatty group fills the disk — and on a paid deployment that disk is shared
    with every conversation's MEMORY.md.
    """
    if keep is None:
        keep = settings.MAX_UPLOADS
    if keep < 0:
        return 0
    files = upload_files(session_id)
    removed = 0
    for stale in files[keep:]:
        try:
            stale.unlink()
            removed += 1
        except OSError:
            # A file we cannot delete is not worth failing the upload over.
            pass

    if removed:
        surviving = {p.name for p in upload_files(session_id)}
        manifest = read_upload_senders(session_id)
        trimmed = {k: v for k, v in manifest.items() if k in surviving}
        if len(trimmed) != len(manifest):
            _write_manifest(session_id, trimmed)
    return removed


def store_upload(
    session_id: str, data: bytes, filename: str, sender: str | None = None, at: str = ""
) -> tuple[Path, int]:
    """Write an uploaded file into uploads/, record who sent it, then prune.

    Recording the sender is what stops the agent inventing one. A LINE group
    shares a single workspace, so without this the agent sees a pile of
    anonymous jpgs and, asked to "back up the photos 李中 sent", has no
    honest answer available — it confabulates an attribution instead.

    Returns the path written and how many older uploads were evicted.
    """
    folder = uploads_dir(session_id)
    path = folder / filename
    path.write_bytes(data)

    if sender:
        manifest = read_upload_senders(session_id)
        manifest[filename] = {"sender": sender, "at": at}
        _write_manifest(session_id, manifest)

    return path, prune_uploads(session_id)


# ---------------------------------------------------------------------------
# Who is in this conversation
# ---------------------------------------------------------------------------
# LINE will not tell us. get_group_member_ids is restricted to verified accounts
# and 403s otherwise, and even when it works it returns opaque ids whose profiles
# need friendship to resolve. So the roster is built from people we have actually
# heard from — addressed the bot, or sent it a photo.
#
# Deliberately NOT built by reading every group message: that would mean
# processing conversations the bot was not part of, which is exactly the
# surveillance this deployment chose not to do. The cost is that a silent member
# is invisible, and the prompt says so rather than letting the agent round up.

def _members_path(session_id: str) -> Path:
    return session_dir(session_id, create=False) / settings.MEMBERS_FILENAME


def read_members(session_id: str) -> dict:
    """user_id -> {"name": str, "last": str, "count": int}. Corrupt reads empty."""
    path = _members_path(session_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def note_member(session_id: str, user_id: str, name: str, at: str = "") -> None:
    """Record that `user_id` interacted with the bot in this conversation."""
    if not user_id:
        return
    members = read_members(session_id)
    entry = members.get(user_id) or {"count": 0}
    entry["name"] = name or entry.get("name") or "（不明成員）"
    entry["count"] = int(entry.get("count", 0)) + 1
    if at:
        entry["last"] = at
        entry.setdefault("first", at)
    members[user_id] = entry

    session_dir(session_id).mkdir(parents=True, exist_ok=True)
    _members_path(session_id).write_text(
        json.dumps(members, indent=2, ensure_ascii=False), encoding="utf-8"
    )


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

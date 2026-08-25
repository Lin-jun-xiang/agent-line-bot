"""Signed, expiring public URLs for files inside a session workspace.

Why this exists: LINE renders an image only if *its* servers can fetch the file
over public HTTPS. A photo the agent produced or backed up lives on our disk with
no URL at all, so before this module the agent could create a file and then had no
way whatsoever to send it back — `_messages_for` only ever saw artifacts carrying
remote URLs from the media-generation tools.

Two problems had to be solved together:

Which host are we?
    The public URL differs per deployment (every Render service gets its own
    `*.onrender.com` name) so it cannot be a constant, and asking each operator to
    set it by hand is a step people forget until images silently break. Resolution
    order, most to least specific:

      1. PUBLIC_BASE_URL      — explicit, for a custom domain or a proxy.
      2. the learned host     — LINE can only reach /callback at our real public
                                address, so a verified webhook tells us exactly
                                what it is. Correct on Render, Railway, Fly, or an
                                ngrok tunnel with zero configuration.
      3. RENDER_EXTERNAL_URL  — injected by Render; bootstraps the window between
                                boot and the first webhook.

Who may read the file?
    Session slugs are derived from LINE ids, and a bare /files/<slug>/<path> route
    would expose every conversation's workspace to anyone who obtained or guessed
    one. So each link carries an expiry and an HMAC over (slug, path, expiry),
    keyed on a server secret. A link cannot be forged and stops working shortly
    after it was handed out — LINE fetches the image within seconds, so a short
    TTL costs nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from agent_core import settings, workspace

# Only formats LINE will actually display. Handing it a .txt produces a broken
# image bubble with no error, which is worse than refusing here.
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
VIDEO_SUFFIXES = {".mp4"}

_learned_base_url = ""


# ---------------------------------------------------------------------------
# Base URL
# ---------------------------------------------------------------------------

def remember_base_url(url: str) -> None:
    """Record the public origin we were reached at.

    Call this only for requests whose authenticity is already established (a
    signature-verified LINE webhook). The Host / X-Forwarded-Host headers are
    caller-controlled, so trusting them on an unauthenticated request would let
    anyone point our image links at a host of their choosing.
    """
    global _learned_base_url
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return
    origin = f"{parts.scheme}://{parts.netloc}".rstrip("/")
    if origin != _learned_base_url:
        _learned_base_url = origin
        print(f"[filelinks] public base url = {origin}")


def base_url() -> str:
    """Our public origin, or "" if we do not know it yet."""
    return settings.PUBLIC_BASE_URL or _learned_base_url or settings.RENDER_EXTERNAL_URL


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def _secret() -> bytes:
    """Key for the link HMAC.

    Reuses an existing deployment secret rather than adding another required env
    var — one more mandatory setting is one more way for a fresh deploy to come up
    broken. Any of these is already secret and already present.
    """
    for value in (
        settings.FILE_LINK_SECRET,
        _line_channel_secret(),
        settings.GLM_API_KEY,
    ):
        if value:
            return value.encode("utf-8")
    return b""


def _line_channel_secret() -> str:
    try:
        import config

        return getattr(config, "LINE_CHANNEL_SECRET", "") or ""
    except Exception:  # noqa: BLE001 - config is optional for agent-only runs
        return ""


def _signature(slug: str, rel_path: str, expires: int) -> str:
    # Newline-delimited so a slug ending in a digit cannot be confused with a
    # path beginning with one — without a separator, ("a", "1", 2) and ("a1", "",
    # 2) would hash identically and one signature would authorise both.
    message = f"{slug}\n{rel_path}\n{expires}".encode("utf-8")
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()[:32]


def verify(slug: str, rel_path: str, expires: int, signature: str) -> str | None:
    """Return a rejection reason, or None when the link is good."""
    if not _secret():
        return "file links are not configured on this server"
    if expires < int(time.time()):
        return "this link has expired"
    if not hmac.compare_digest(_signature(slug, rel_path, expires), signature):
        return "bad signature"
    return None


# ---------------------------------------------------------------------------
# Building links
# ---------------------------------------------------------------------------

def kind_for(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return None


def content_type_for(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def public_url(session_id: str, target: Path, ttl: int | None = None) -> str | None:
    """Signed URL for `target`, which must live inside `session_id`'s workspace.

    Returns None when we cannot make a usable link — no known public host, no
    secret, or the file is outside the session. Callers surface that to the model
    as a plain explanation rather than sending a URL that would 404.
    """
    origin = base_url()
    if not origin or not _secret():
        return None

    session_root = workspace.session_dir(session_id, create=False)
    resolved = workspace.resolve_within(target, [session_root], base=session_root)
    if resolved is None or not resolved.is_file():
        return None

    # Relative to the session dir, not the workspace root: the slug is already in
    # the URL and signed separately, so repeating it would only add a way for the
    # two to disagree.
    try:
        rel_path = resolved.relative_to(session_root.resolve()).as_posix()
    except ValueError:
        return None

    slug = workspace.slugify_session(session_id)
    expires = int(time.time()) + (ttl if ttl is not None else settings.FILE_LINK_TTL_S)
    signature = _signature(slug, rel_path, expires)
    quoted = quote(rel_path, safe="/")
    return f"{origin}/files/{quote(slug)}/{quoted}?exp={expires}&sig={signature}"

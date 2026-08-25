"""
/files — serves one file out of a session workspace over a signed, expiring link.

    GET /files/{slug}/{path}?exp=&sig=

This route exists for LINE, not for people: LINE's servers fetch an image URL
themselves before rendering the bubble, so a file on our disk has to be reachable
over public HTTPS or it cannot be sent at all.

That makes it the one place where workspace containment is exposed to the open
internet, so it is deliberately narrow. Every request must present an unexpired
HMAC over (slug, path, expiry) — see agent_core.filelinks — and the resolved path
must still land inside that session's directory. Order matters: the signature is
checked before anything touches the filesystem, and containment is re-checked
afterwards rather than trusted from the signature, so neither a leaked link nor a
symlink planted in the workspace can reach another conversation's files.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from agent_core import filelinks, workspace

files_api = APIRouter(prefix="/files", tags=["files"])


@files_api.get("/{slug}/{rel_path:path}")
async def serve_file(
    slug: str,
    rel_path: str,
    exp: int = Query(..., description="Unix expiry, part of the signature"),
    sig: str = Query(..., description="HMAC over slug, path and expiry"),
) -> FileResponse:
    problem = filelinks.verify(slug, rel_path, exp, sig)
    if problem:
        # One status for every rejection: distinguishing "expired" from "bad
        # signature" would confirm to a prober which half they got right.
        raise HTTPException(status_code=403, detail=problem)

    # A signature can only exist for a slug we generated, but resolve the root
    # through slugify anyway so a crafted slug cannot become a directory name
    # even if the secret ever leaks.
    root = workspace.workspace_root() / workspace.slugify_session(slug)
    target = workspace.resolve_within(rel_path, [root], base=root)
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")

    return FileResponse(
        target,
        media_type=filelinks.content_type_for(target),
        # Links expire, so telling caches to hold one past its lifetime would
        # leave a publicly readable copy behind after the signature stopped working.
        headers={"Cache-Control": "private, max-age=300"},
    )

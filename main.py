import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from api import agent_api

app = FastAPI(
    title="chatgpt-line-bot",
    description="LINE bot backed by the Claude Code harness running GLM, sandboxed to ./workspace",
    version="2.0.0",
)

app.include_router(agent_api)

# The LINE router needs channel credentials. Keep the agent API usable without them
# so you can develop and test the agent standalone.
try:
    from line_bot.routes import line_app

    app.include_router(line_app)
    LINE_ENABLED = True
except Exception as exc:  # noqa: BLE001
    print(f"[startup] LINE routes disabled: {exc}")
    LINE_ENABLED = False


@app.get("/", response_class=JSONResponse)
async def home() -> JSONResponse:
    """Service index."""
    return JSONResponse(
        content={
            "status": "success",
            "service": "chatgpt-line-bot",
            "line_enabled": LINE_ENABLED,
            "docs": "/docs",
            "agent": {
                "health": "GET /agent/health",
                "run": "POST /agent/run",
                "stream": "POST /agent/stream (SSE)",
                "selftest": "POST /agent/selftest",
            },
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8090))
    # Off by default in hosted environments; opt in locally with RELOAD=true.
    RELOAD = os.getenv("RELOAD", "false").lower() in ("1", "true", "yes")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        # The agent spawns a `claude` subprocess per run and holds it for the whole
        # turn; a single worker with async concurrency is the right shape here.
        workers=1,
        log_level="info",
        access_log=True,
        use_colors=True,
        reload=RELOAD,
        # The agent writes into ./workspace constantly. Without this the reloader
        # restarts the server mid-run, killing the `claude` subprocess.
        reload_excludes=["workspace/*", "workspace/**/*", "*.log"],
    )

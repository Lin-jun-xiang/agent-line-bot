#!/usr/bin/env python
"""
Agent capability test client.

Drives the running API at http://localhost:8090 (override with --base).

    python scripts/test_agent.py chat                 # 互動對話（最常用）
    python scripts/test_agent.py health
    python scripts/test_agent.py run  "用 python 算 2^100 是多少"
    python scripts/test_agent.py stream "產生一張柴犬吃拉麵的圖"
    python scripts/test_agent.py selftest
    python scripts/test_agent.py selftest --cases filesystem,sandbox_escape
    python scripts/test_agent.py files --session api-default
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

TIMEOUT = httpx.Timeout(600.0, connect=10.0)

if os.name == "nt":
    # Turns on ANSI escape handling in the Windows console, so the colour codes
    # below render instead of printing as literal gibberish.
    os.system("")


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_health(args) -> int:
    resp = httpx.get(f"{args.base}/agent/health", timeout=TIMEOUT)
    _print(resp.json())
    return 0 if resp.json().get("status") == "ok" else 1


def cmd_run(args) -> int:
    payload = {"prompt": args.prompt, "session_id": args.session, "resume": not args.fresh}
    resp = httpx.post(f"{args.base}/agent/run", json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    print("─" * 60)
    print(data["text"])
    print("─" * 60)
    print(
        f"turns={data['num_turns']}  {data['duration_ms']}ms  "
        f"cost=${data['cost_usd'] or 0:.4f}"
    )
    for call in data["tool_calls"]:
        mark = "✔" if call["ok"] else ("✘" if call["ok"] is False else "·")
        print(f"  {mark} {call['name']}  {json.dumps(call['input'], ensure_ascii=False)[:110]}")
    for block in data["blocked"]:
        print(f"  🔒 blocked {block['tool']}: {block['reason'][:140]}")
    for art in data["artifacts"]:
        print(f"  🎨 {art['kind']}: {art['url']}")
    if data["files"]:
        print("  files: " + ", ".join(f["path"] for f in data["files"][:12]))
    return 1 if data["is_error"] else 0


def cmd_stream(args) -> int:
    _stream_once(args.base, args.prompt, args.session, verbose=True)
    return 0


def _stream_once(base: str, prompt: str, session: str, verbose: bool) -> None:
    """Stream one turn to stdout. Shared by `stream` and `chat`."""
    payload = {"prompt": prompt, "session_id": session, "resume": True}
    printed_any = False
    with httpx.stream("POST", f"{base}/agent/stream", json=payload, timeout=TIMEOUT) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            kind = event.get("type")
            if kind == "text":
                print(event["text"], end="", flush=True)
                printed_any = True
            elif kind == "tool_use" and verbose:
                name = event["name"].replace("mcp__botkit__", "")
                arg = json.dumps(event["input"], ensure_ascii=False)[:90]
                print(f"\n  \033[90m· {name} {arg}\033[0m", flush=True)
            elif kind == "tool_result" and verbose:
                tag = "error" if event["is_error"] else "ok"
                print(f"  \033[90m  → {tag}: {event['preview'][:90]}\033[0m", flush=True)
            elif kind == "blocked":
                print(f"\n  \033[33m🔒 {event['tool']}: {event['reason'][:150]}\033[0m", flush=True)
            elif kind == "result":
                for art in event.get("artifacts", []):
                    print(f"\n  \033[36m{art['kind']}: {art['url']}\033[0m")
                if verbose:
                    u = event.get("usage") or {}
                    cached = u.get("cache_read") or 0
                    # A cache miss on a big prompt is the single biggest cause of
                    # a slow turn on the free tier — worth showing every time.
                    print(
                        f"\n  \033[90m[{event['num_turns']} turns · "
                        f"{event['duration_ms']}ms（模型 {(event.get('api_ms') or 0)}ms）· "
                        f"in={u.get('input')} out={u.get('output')} "
                        f"cached={cached}{' ⚠ 快取沒中' if cached < 100 else ''}]\033[0m"
                    )
                elif not printed_any:
                    print("\033[90m(沒有回覆內容)\033[0m")
            elif kind == "error":
                print(f"\n\033[31m[error] {event['error']}\033[0m", file=sys.stderr)
    print()


def cmd_chat(args) -> int:
    """Interactive REPL against one session — the closest thing to the LINE UX."""
    try:
        health = httpx.get(f"{args.base}/agent/health", timeout=10).json()
    except httpx.HTTPError as exc:
        print(f"連不到 {args.base} — 服務起來了嗎？({exc})", file=sys.stderr)
        return 1
    if health["status"] != "ok":
        for problem in health["problems"]:
            print(f"⚠  {problem}", file=sys.stderr)
        return 1

    verbose = not args.quiet
    print(f"\n和 AI寶寶 對話中 · session={args.session} · {health['provider']['model']}")
    print("指令：/reset 重開對話  /forget 清除記憶  /memory 看她記得什麼")
    print("      /files 看產出檔案  /verbose 切換工具細節  /quit 離開\n")

    while True:
        try:
            message = input("\033[1m你 >\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not message:
            continue

        if message in ("/quit", "/exit", "/q"):
            return 0
        if message == "/verbose":
            verbose = not verbose
            print(f"  工具細節：{'開' if verbose else '關'}\n")
            continue
        if message == "/reset":
            httpx.delete(f"{args.base}/agent/sessions/{args.session}", timeout=30)
            print("  已重開（工作區與記憶一併清除）\n")
            continue
        if message in ("/forget", "/memory", "/files"):
            if message == "/files":
                data = httpx.get(
                    f"{args.base}/agent/sessions/{args.session}/files", timeout=30
                ).json()
                print("  " + (", ".join(f["path"] for f in data["files"]) or "(還沒有檔案)") + "\n")
            else:
                try:
                    data = httpx.get(
                        f"{args.base}/agent/sessions/{args.session}/file",
                        params={"path": "MEMORY.md"},
                        timeout=30,
                    ).json()
                    print("\033[90m" + data["content"] + "\033[0m\n")
                except Exception:
                    print("  (還沒有記憶)\n")
            continue

        print("\033[1mAI寶寶 >\033[0m ", end="", flush=True)
        try:
            _stream_once(args.base, message, args.session, verbose)
        except httpx.HTTPError as exc:
            print(f"\n\033[31m請求失敗：{exc}\033[0m\n", file=sys.stderr)


def cmd_selftest(args) -> int:
    payload = {}
    if args.cases:
        payload["cases"] = [c.strip() for c in args.cases.split(",") if c.strip()]
    if args.session != "api-default":
        payload["session_id"] = args.session

    resp = httpx.post(f"{args.base}/agent/selftest", json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    print(f"\nsession: {data['session_id']}")
    print("=" * 72)
    for row in data["results"]:
        icon = "PASS" if row["passed"] else "FAIL"
        print(f"[{icon}] {row['case']:<16} {row['title']}")
        print(f"       {row['elapsed_ms']}ms · turns={row['turns']} · tools={row['tools_used']}")
        if row["blocked"]:
            print(f"       blocked: {row['blocked'][0]['reason'][:120]}")
        if row["error"]:
            print(f"       error: {row['error'][:200]}")
        print(f"       {row['answer'][:200].strip()}")
        print()
    print("=" * 72)
    print(f"{data['summary']}  ({data['elapsed_ms']}ms total)")
    return 0 if data["passed"] == data["total"] else 1


def cmd_files(args) -> int:
    resp = httpx.get(f"{args.base}/agent/sessions/{args.session}/files", timeout=TIMEOUT)
    _print(resp.json())
    return 0


def cmd_reset(args) -> int:
    resp = httpx.delete(f"{args.base}/agent/sessions/{args.session}", timeout=TIMEOUT)
    _print(resp.json())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://localhost:8090")
    parser.add_argument("--session", default="api-default")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health").set_defaults(func=cmd_health)

    p = sub.add_parser("chat", help="interactive conversation")
    p.add_argument("--quiet", action="store_true", help="hide tool-call details")
    p.set_defaults(func=cmd_chat)

    for name, func in (("run", cmd_run), ("stream", cmd_stream)):
        p = sub.add_parser(name)
        p.add_argument("prompt")
        p.add_argument("--fresh", action="store_true", help="start a new conversation")
        p.set_defaults(func=func)

    p = sub.add_parser("selftest")
    p.add_argument("--cases", default="", help="comma-separated case ids")
    p.set_defaults(func=cmd_selftest)

    sub.add_parser("files").set_defaults(func=cmd_files)
    sub.add_parser("reset").set_defaults(func=cmd_reset)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

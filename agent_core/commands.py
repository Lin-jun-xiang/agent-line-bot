"""
Chat commands — the single source of truth.

Both sides need this list and they must not drift: `line_bot/routes.py` handles
the commands, and `agent_core/prompt.py` tells the agent they exist so it can
answer "你會做什麼" without guessing.
"""

from __future__ import annotations

# (triggers, 說明) — the first trigger is the canonical form shown to users.
COMMANDS: list[tuple[tuple[str, ...], str]] = [
    (("@help", "--help", "@說明"), "列出這張說明"),
    (("@chat",), "在群組裡跟我說話（一對一不需要前綴）"),
    (("@prompt",), "改我的個性，例如 @prompt 你是個嚴肅的助理。不帶內容就還原預設"),
    (("@init",), "重開對話，但記得你的偏好"),
    (("@forget",), "清掉所有關於你的記憶和檔案"),
]

CAPABILITIES = [
    "查即時資訊 — 新聞、股價、天氣、時事，我會上網查過再回答",
    "算數與資料處理 — 寫程式跑出結果，不是心算",
    "產生圖片 — 「生成一隻貓的圖片」",
    "找現成照片 — 「幫我找台北101的照片」",
    "看圖回答 — 傳張圖片給我，再問我圖裡有什麼",
    "產生影片 — 「生成一段貓在走路的影片」，也可以讓你傳的圖片動起來",
    "讀寫檔案 — 整理資料、產報表，檔案留在你的專屬空間",
    "記住你 — 你的稱呼、口味、正在忙什麼，下次不用重講",
]


def help_text() -> str:
    """The message sent when someone asks for help."""
    lines = ["我可以幫你做這些事：", ""]
    lines += [f"・{item}" for item in CAPABILITIES]
    lines += ["", "指令：", ""]
    lines += [f"{triggers[0]} — {desc}" for triggers, desc in COMMANDS]
    lines += ["", "其他就直接跟我說，不用記指令。"]
    return "\n".join(lines)


def help_triggers() -> set[str]:
    return {t.lower() for t in COMMANDS[0][0]} | {"help", "?", "？", "幫助", "功能"}


def command_summary() -> str:
    """Compact form for the system prompt — the agent only needs to know these
    exist so it can point users at them."""
    return "\n".join(f"- {triggers[0]}：{desc}" for triggers, desc in COMMANDS)

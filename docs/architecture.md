# 架構設計

## 為什麼重構

舊版是自己寫的 function-calling 迴圈：8 個硬編碼工具、最多 8 輪、只能讀 `skills/` 底下的檔案、
執行指令要先過白名單、記憶是記憶體裡的 20 則訊息、`cwd` 就是專案根目錄。

新版把 Claude Code CLI 當成 harness 封裝起來，換到完整的 agent 能力：

| | 舊版 | 新版 |
|---|---|---|
| Agent 迴圈 | 手寫約 360 行 | Claude Code harness（`claude-agent-sdk`） |
| 檔案能力 | 只能讀 `skills/` | 完整 Read / Write / Edit |
| 執行能力 | 白名單，只准跑已註冊的 skill CLI | 完整 shell，沙箱限制在工作區內 |
| 多步驟 | 最多 8 輪 | 預設 30 輪，可派子 agent |
| 記憶 | 記憶體裡 20 則訊息，重啟就沒 | CLI session resume + 每人一份 `MEMORY.md` |
| 隔離 | 無，`cwd` 是專案根目錄 | 每個對話一個工作目錄，離開就被擋 |
| 測試 | 無 | `/agent/selftest` + 38 個沙箱測試 |

## 資料流

```
                      LINE                         HTTP client / CI
                        │                                 │
              POST /callback                     POST /agent/run
              POST /broadcast                     POST /agent/stream (SSE)
                        │                        POST /agent/selftest
                        ▼                                 ▼
                line_bot/routes.py                 api/agent_api.py
                        │                                 │
                        └──────────────┬──────────────────┘
                                       ▼
                          agent_core/runner.py   AgentRunner
                                       │
    ┌───────────┬───────────┬──────────┼──────────┬────────────┐
    ▼           ▼           ▼          ▼          ▼            ▼
settings.py  prompt.py  memory.py  guard.py   tools.py   workspace.py
provider     人設        MEMORY.md  沙箱攔截   自訂工具    目錄/session
    │                               │          │
    └───────────┬───────────────────┴──────────┘
                ▼
      claude-agent-sdk → claude 執行檔（subprocess，跑在專屬的 event loop 上）
                ▼
      https://open.bigmodel.cn/api/anthropic  →  glm-4.7-flash
```

LINE webhook 與 REST API 走的是**同一個** `AgentRunner`，所以兩邊的行為和沙箱限制完全一致。

## 檔案地圖

| 檔案 | 職責 |
|---|---|
| `agent_core/settings.py` | 把 Claude Code 指向 GLM：base URL、模型對應、CLI 路徑自動偵測、工具範圍、執行上限 |
| `agent_core/workspace.py` | 工作區目錄結構、session id 消毒、路徑包含判斷（`resolve_within`）、session 索引 |
| `agent_core/guard.py` | 沙箱：`PreToolUse` hook + `can_use_tool` |
| `agent_core/tools.py` | in-process MCP server：`run_shell` / `web_search` / `fetch_url` / `generate_image` / `search_image` / `generate_video` |
| `agent_core/memory.py` | 每個對話一份 `MEMORY.md` 的讀寫與長度上限 |
| `agent_core/prompt.py` | 人設、工作區說明、當前日期、記憶注入 |
| `agent_core/runner.py` | 組裝 `ClaudeAgentOptions`、在專屬 event loop 上執行、把 SDK 訊息轉成事件 dict |
| `agent_core/integrations/` | 外部整合：網路搜尋、圖片搜尋 |
| `api/agent_api.py` | REST 測試介面 + 能力自測套件 |
| `line_bot/routes.py` | LINE webhook 與廣播 |
| `agent_core/commands.py` | 聊天指令與功能清單的單一來源（`routes.py` 與 `prompt.py` 共用） |
| `tests/test_sandbox.py` | 48 個沙箱圍堵測試，不需要 API key |
| `scripts/test_agent.py` | 命令列測試客戶端 |

`workspace/` 與 `skills/` 是執行期目錄，內容不進版控。

依賴方向是單向的：`line_bot` 與 `api` 依賴 `agent_core`，`agent_core` 不知道 LINE 的存在。

## 關鍵設計決策

### 不使用 `claude_code` system prompt preset

SDK 提供的 preset 開頭就是「You are Claude Code, Anthropic's official CLI」，任何人問一句
「你是誰」人設就破了。所以 `prompt.py` 是完全自訂的 system prompt，把 preset 原本提供的
工具使用紀律自己補上。

代價是也失去了 preset 注入的環境資訊（包含當前日期），所以 `prompt.py` 要自己補日期，
否則模型會拿訓練資料裡的年份當「今天」。

### 自製 `run_shell` 取代內建 `Bash`

內建 `Bash` 的工具描述塞了大量 git / 安全性 / 跨平台指示，光它一個的 schema 就比
`Read` + `Write` 加起來還貴。在按 token 限流的免費層，這個差異足以讓每輪從幾秒變成一兩分鐘。

`tools.py` 的 `run_shell` 提供同樣的能力，描述只有十分之一大小，而且 `guard.py` 對它套用與
內建 `Bash` **完全相同**的指令檢查。詳見 [效能調校](performance.md)。

### 專屬的 event loop

`runner.py` 自己開一條執行緒跑專屬的 event loop，所有 agent 執行都送到那上面。

原因是 uvicorn 在 Windows 上只要需要開子行程（`--reload` 或多 worker）就會改用
`SelectorEventLoop`，而它**不支援** `create_subprocess_exec`，會丟一個訊息為空的
`NotImplementedError`，表現出來就是 `Failed to start Claude Code: `（後面空白）。

自己持有 loop 之後，不管服務怎麼啟動、在哪個平台，行為都一樣。這也讓同步呼叫端
（LINE 的背景執行緒）有地方送工作，不必每則訊息都開一個新 loop。

### 記憶分兩層

**短期**是 CLI 自己的 session。`workspace/_sessions.json` 存「對話 key → CLI session id」，
下一則訊息用 `resume=` 接回去。

**長期**是每個對話目錄裡的 `MEMORY.md`，每輪都被注入 system prompt，所以「回想」不花額外的
token。agent 學到以後還用得到的事就自己用 Edit 更新那個檔案。

上限壓在 30 行 / 1,200 字元：`memory.py` 讀取時強制截斷，prompt 裡也要求它自己精簡。
這個檔案每輪都進 prompt，放任長大會拖慢每一次回覆。

`@init` 只斷開對話、保留記憶；`@forget` 才會真的清掉。

### LINE 的回覆策略

LINE 的 reply token 會過期，而 agent 一輪可能跑好幾分鐘。所以 webhook 立刻回 200，
實際工作丟到背景執行緒，回覆時先試 `reply_message`，失敗就改用 `push_message`。

# 設定項

全部走環境變數，`.env` 即可。範本見 [`.env.example`](../.env.example)。

## 模型

| 變數 | 預設 | 說明 |
|---|---|---|
| `GLM_API_KEY` | — | BigModel API key，[免費申請](https://open.bigmodel.cn) |
| `GLM_BASE_URL` | `https://open.bigmodel.cn/api/anthropic` | Anthropic 相容端點 |
| `GLM_FREE_MODEL` | `glm-4.7-flash` | 三個別名共用的模型 |
| `AGENT_MODEL` | `glm-4.7-flash` | 主迴圈模型 |
| `GLM_MODEL_OPUS` / `_SONNET` / `_HAIKU` | 同上 | 個別覆寫 |
| `GLM_VISION_MODEL` | `glm-4.6v-flash` | 圖片理解用的視覺模型 |
| `GLM_VISION_FALLBACKS` | `glm-4v-flash,glm-4.1v-thinking-flash` | 上面那個爆量時的備援 |

聊天模型本身沒有視覺能力，所以圖片問題是由 `describe_image` 工具轉送到獨立的視覺模型處理。

免費的視覺模型爆量時會直接回 **429「该模型当前访问量过大」而不是排隊**，所以
`describe_image` 會依序試 `GLM_VISION_MODEL` → `GLM_VISION_FALLBACKS`，直到有一個回應。
實測 `glm-4.6v-flash` 經常 429，`glm-4v-flash` 穩定得多。

Claude Code 只會要求 opus / sonnet / haiku 三個別名，`settings.py` 把它們全部對應到
`GLM_FREE_MODEL`。三個都指向免費模型是刻意的——這樣就算 harness 內部派子 agent，
也不會偷偷跑到付費模型。

換成付費的 GLM Coding Plan：

```env
GLM_BASE_URL=https://api.z.ai/api/anthropic
GLM_FREE_MODEL=glm-5.2
```

## CLI

| 變數 | 預設 | 說明 |
|---|---|---|
| `AGENT_CLI_PATH` | 自動偵測 | 原生 claude 執行檔路徑 |

`settings.find_cli()` 會依序找：`AGENT_CLI_PATH` → npm 套件內附的原生執行檔 →
`~/.local/bin` → `PATH`。

> **Windows**：SDK 不接受 npm 產生的 `claude.cmd` shim（cmd.exe 有參數注入問題），
> 必須是原生執行檔。自動偵測會找到
> `%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`。
> 找不到的話用 `irm https://claude.ai/install.ps1 | iex` 裝原生版。

## 沙箱與工具

| 變數 | 預設 | 說明 |
|---|---|---|
| `AGENT_WORKSPACE_ROOT` | `./workspace`（Docker 內為 `/data/workspace`） | 沙箱根目錄 |
| `AGENT_TOOL_PROFILE` | `files` | `full` / `lean` / `files` / `chat` |
| `AGENT_TOOLS` | — | 自訂工具清單，蓋過 profile |
| `AGENT_MCP_TOOLS` | 七個全開 | 要開哪些自訂工具 |
| `AGENT_ALLOW_BASH` | `true` | 關掉就完全沒有 shell |
| `AGENT_SKILLS_SOURCE` | `./skills` | 技能來源目錄 |

`AGENT_WORKSPACE_ROOT` 不設也能跑：目錄會自動建立，功能完全正常。它只影響**持久性**——
如果那個路徑不是持久化磁碟，重啟就清空，所有使用者的記憶會消失。
用 `/agent/health` 的 `sandbox.survives_redeploy` 確認，細節見
[部署指南](deployment.md#不設-agent_workspace_root-會怎樣)。

不要把它指向專案原始碼目錄——那等於把沙箱開在原始碼上面。

### 工具 profile

| profile | 內建工具 | tokens |
|---|---|---|
| `full` | Read / Write / Edit / NotebookEdit / Glob / Grep / TodoWrite / Task / Skill + `Bash` | 7,400 |
| `lean` | Read / Write / Edit / Glob + `Bash` | 3,400 |
| `files` | Read / Write / Edit | 2,650 |
| `chat` | 無 | 1,600 |

預設不含內建 `Bash`，shell 能力由 `run_shell` 提供。
理由與實測數據見 [效能調校](performance.md#為什麼不用內建-bash)。

### 自訂工具

`AGENT_MCP_TOOLS` 可選（每個約 70 tokens，預設全開）：

| 工具 | 功能 |
|---|---|
| `run_shell` | 執行指令／跑 Python。取代內建 `Bash` |
| `web_search` | DuckDuckGo 搜尋 + 抓取內文 |
| `fetch_url` | 讀取單一網頁 |
| `generate_image` | 文字生成圖片（CogView） |
| `search_image` | 網路圖片搜尋（iCrawler / SerpAPI） |
| `describe_image` | 看圖回答（視覺模型） |
| `generate_video` | 文字或圖片生成影片（CogVideoX） |

`describe_image` 與 `generate_video` 會自己讀取工作區的檔案，所以它們在工具內部**自行執行
沙箱路徑檢查**——`PreToolUse` hook 只看得到不透明的字串參數，攔不住這一類。

## 行為與上限

| 變數 | 預設 | 說明 |
|---|---|---|
| `AGENT_THINKING` | `false` | 延伸思考。免費模型開了會慢 5 倍 |
| `AGENT_TIMEZONE` | `Asia/Taipei` | agent 的「今天」以此為準 |
| `AGENT_MAX_TURNS` | `30` | 單輪最多幾步 |
| `AGENT_RUN_TIMEOUT` | `300` | 同步呼叫的秒數上限 |
| `AGENT_MAX_BUDGET_USD` | — | 單輪成本上限 |
| `AGENT_EFFORT` | — | `low` / `medium` / `high` / `xhigh` / `max` |

> 回傳的 `cost_usd` 是 CLI 按 Anthropic 價目表估的，**不是實際花費**。
> 走免費模型時這個欄位沒有意義。

## LINE

| 變數 | 說明 |
|---|---|
| `LINE_CHANNEL_SECRET` | LINE Developers Console 取得 |
| `LINE_CHANNEL_ACCESS_TOKEN` | 同上 |
| `SERPAPI_API_KEY` | 選用，圖片搜尋的備援 |

沒填 LINE 憑證服務照樣啟動，只有 LINE 路由會停用。

## 服務

| 變數 | 預設 | 說明 |
|---|---|---|
| `PORT` | `8090` | 監聽埠 |
| `RELOAD` | `false` | 本機開發設 `true`；`workspace/` 已排除在監看之外 |

## 人設與指令

不是環境變數——個性、語氣、行為規則都寫在 [`agent_core/prompt.py`](../agent_core/prompt.py)
的 `PERSONA` 常數裡，直接改那個字串即可。

單一對話的額外設定用 LINE 的 `@prompt` 指令，或 API 的 `persona` 欄位。
`@prompt` 的內容會存成該對話工作目錄下的 `PERSONA.md`，重啟或重新部署都不會消失。

指令清單（`@help` / `@chat` / `@prompt` / `@init` / `@forget`）與功能說明集中在
[`agent_core/commands.py`](../agent_core/commands.py)。那裡是唯一的來源：
`line_bot/routes.py` 負責處理指令，`prompt.py` 讓 agent 知道有這些指令存在，
所以兩邊不會各說各話。要新增或改字，只改那一個檔案。

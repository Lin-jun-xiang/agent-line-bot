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

### 「這張圖片是啥」為什麼要特別處理

聊天模型不但沒有視覺，**也不會自己決定去用 `describe_image`**。同一張圖：

| 使用者說 | 結果 |
|---|---|
| 這張圖片是啥 | 「我看不到圖片」，完全沒呼叫工具 |
| 用 describe_image 看 uploads/phone.jpg | 正確答出內容 |

工具本身沒問題，缺的是那個決定。修法分兩半，**都不去比對使用者的措辭**：

**1. prompt 直接把話講死。** `_recent_uploads` 在有圖時附上：

> **你看得到圖片。** 你自己讀不了圖檔，但 describe_image 可以，等於你的眼睛。
> 只要對方的話跟圖片有關，第一步就呼叫 describe_image，path 填完整相對路徑
> （例如 `uploads/xxx.jpg`）。**絕對不要回答「我看不到圖片」。**

一定要帶**完整相對路徑**：只給檔名的話模型會猜 `photo.jpg`，而檔案在 `uploads/` 底下，一猜就落空。

**2. 工具失敗時給得出下一步。** `describe_image` 找不到檔案時，不再只回 `no such image`，
而是一併列出實際存在的圖片路徑（最新的在前），讓 agent 自己重試：

```
no such image: phone.jpg
These images DO exist here (newest first) — call describe_image again with one of these exact paths:
  uploads/phone.jpg
```

實測「這是什麼」這種問法就走到了這條路：第一次路徑猜錯 → 拿到清單 → 自己重呼叫一次 → 答對。

> 這裡原本寫過一版關鍵字比對（訊息含「圖片」「這張」「是什麼」就先幫它看圖）。
> 已經拿掉：中文問法窮舉不完，「這是什麼」整句沒有任何圖片相關詞，卻正是最常見的問法。
> 把判斷交還給模型、再讓工具的錯誤訊息可以自我修正，涵蓋率反而更好，程式也更少。

一次視覺呼叫只在**真的有人問**的時候發生——群組裡照片一直進來不會有任何額外開銷，
因為決定權在模型手上，沒人問它就不會呼叫。看過的描述會快取在 `uploads/_senders.json`。

視覺模型的限制是單張 **5MB / 6000×6000**，而手機照片兩項都可能超過。所以
`describe_image` 送出前會先用 Pillow 把最長邊縮到 1568px、重新編碼成 JPEG——
實測 4032×3024 的照片 base64 從 349KB 降到 36KB，回應也快得多。
失敗時真正的錯誤會印在 log 的 `[describe_image]` 開頭那行（模型會把錯誤包裝成
「我看不了這張圖片」，所以不看 log 是查不出原因的）。

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
| `AGENT_MCP_TOOLS` | 八個全開 | 要開哪些自訂工具 |
| `AGENT_ALLOW_BASH` | `true` | 關掉就完全沒有 shell |
| `AGENT_SKILLS_SOURCE` | `./skills` | 技能來源目錄 |
| `AGENT_MAX_UPLOADS` | `8` | 每個對話最多留幾張圖，超過就刪最舊的 |
| `AGENT_MAX_MEMBERS_SHOWN` | `20` | 群組名冊在 prompt 裡最多列幾個人 |
| `AGENT_SEARCH_BACKENDS` | `google,brave,bing,yandex,mullvad_brave,duckduckgo` | 搜尋引擎的嘗試順序 |

`AGENT_SEARCH_BACKENDS` 依序試，第一個有回應的就採用。預設把 DuckDuckGo 放在
最後是因為它現在對伺服器端呼叫幾乎一律回 `202 Ratelimit`——舊版程式碼只用它，
結果每次搜尋都空手而回，而 agent 分不出「搜尋壞了」和「查不到」，就跟使用者說
查無此事。現在這兩種情況回傳的字串不同，agent 會照實說搜尋壞掉。

群組名冊記的是「跟 bot 說過話或傳過圖的人」，不是群組全體成員——LINE 的
`get_group_member_ids` 對未認證帳號會 403，拿不到完整名單。沒開口的人不會被記錄，
prompt 也明講這件事，避免 agent 把名冊人數當成群組人數。

`AGENT_MAX_UPLOADS` 是磁碟的保險絲。使用者傳的圖存在 `<session>/uploads/`，
沒有這個上限的話一個常傳圖的群組就會把磁碟塞滿，而那顆磁碟同時也放著
所有人的 `MEMORY.md`——塞滿就連記憶都寫不進去。實際上只有最新那張用得到
（prompt 只列最新 5 張），所以預設留 8 張已經很寬鬆。設成 `0` 表示存完就刪。

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
| `search_image` | 網路圖片搜尋（Bing → Wikimedia Commons → Openverse → SerpAPI） |
| `describe_image` | 看圖回答（視覺模型） |
| `generate_video` | 文字或圖片生成影片（CogVideoX） |
| `send_file` | 把工作區裡的 `.jpg` `.png` `.mp4` 傳給對方看 |

`describe_image`、`generate_video` 與 `send_file` 會自己讀取工作區的檔案，所以它們在工具內部
**自行執行沙箱路徑檢查**——`PreToolUse` hook 只看得到不透明的字串參數，攔不住這一類。

`send_file` 是 agent 唯一能把「自己產出的檔案」給對方看的途徑。LINE 只認公開 HTTPS 網址，
所以在這之前 agent 可以把照片備份好、卻完全沒辦法傳回去——把路徑寫在文字裡對方是看不到的。
細節見下面的[公開檔案連結](#公開檔案連結)。

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

## 公開檔案連結

`send_file` 要把工作區的檔案交給 LINE，而 LINE 是**自己去抓那個網址**才顯示圖片的，
所以必須知道本服務的公開位址。這個位址每個部署都不一樣（每個 Render service 各有自己的
`*.onrender.com`），沒辦法寫死。

| 變數 | 預設 | 說明 |
|---|---|---|
| `PUBLIC_BASE_URL` | — | 公開位址。只有自訂網域或前面掛代理才需要設 |
| `AGENT_FILE_LINK_TTL` | `3600` | 連結有效秒數 |
| `AGENT_FILE_LINK_SECRET` | — | 連結簽章金鑰。不設就沿用 LINE channel secret |

**一般部署這三個都不用設。** 解析順序由具體到通用：

1. `PUBLIC_BASE_URL`
2. **從 webhook 學到的位址**——LINE 只可能打到真正的公開網址才進得到 `/callback`，
   所以第一個驗章通過的請求就告訴我們答案了。Render、Railway、Fly、本機 ngrok 都自動正確。
3. `RENDER_EXTERNAL_URL`——Render 自動注入，補上開機到第一個 webhook 之間的空窗。

只在**簽章驗證通過後**才採信請求裡的 host：`Host` 和 `X-Forwarded-Host` 是呼叫端可以自己填的，
未驗證就相信等於讓任何人把我們的圖片連結指向他選的主機。

連結本身帶到期時間和一組 HMAC（簽 slug + 路徑 + 到期時間）。沒有這層的話
`/files/<slug>/<path>` 等於任何人猜到或拿到一個 session slug，就能讀那個對話的整個工作區。
簽章偽造不出來，而且很快就失效——LINE 抓圖是幾秒內的事，短效期沒有任何代價。

`/files` 是工作區唯一對外開放的路由，所以它刻意做得很窄：先驗簽章才碰檔案系統，
之後**重新**檢查路徑是否仍落在該 session 目錄內（而不是憑簽章就信），
所以連結外流或工作區裡被放了 symlink 都到不了別的對話。

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

# 使用方式

## LINE 指令

| 指令 | 行為 |
|---|---|
| （直接講話） | 一對一聊天直接回 |
| `@help` | 列出功能與指令。也吃 `--help`、`help`、`功能`、`?` |
| （在群組 tag 機器人） | 用 LINE 的 `@` 提及機器人，剩下的內容就是問題 |
| `@chat <訊息>` | 群組裡的替代寫法，不 tag 也能叫它 |
| `@prompt <說明>` | 改這個對話的個性。不帶內容就還原預設 |
| `@init` | 重開對話，保留長期記憶與個性設定 |
| `@forget` | 連長期記憶、個性、工作區一起清掉 |

`@help` 由程式直接回答，不經過模型——立即回覆，也不消耗額度。

群組裡的觸發條件是「有沒有被叫到」：webhook 的 `message.mention.mentionees` 裡出現本機器人
（比對 `get_bot_info()` 拿到的 user id，拿不到 id 時退而比對顯示名稱），就把那段 tag 從訊息裡
切掉，剩下的當成問題。`@chat` 前綴保留，因為不是每個客戶端都會產生 mention 物件（例如貼上
的文字）。只 tag 不講話會得到一句招呼，不會浪費一次 agent 回合。`@All` 不算叫它。

`@prompt` 的設定存成工作目錄下的 `PERSONA.md`，重啟不會消失。系統 prompt 也會告訴 agent
這些指令存在，所以使用者問「你會做什麼」時它會引導對方打 `@help`。

上傳圖片會存進該對話的 `uploads/`，最近幾個檔名會自動出現在 agent 的 system prompt 裡，
所以可以直接問「剛剛那張圖裡有什麼」，不需要告訴它檔名。

## 命令列客戶端

`scripts/test_agent.py` 不需要 LINE 就能完整測試 agent。

```bash
python scripts/test_agent.py chat                      # 互動對話
python scripts/test_agent.py health                    # 檢查設定
python scripts/test_agent.py run "用 python 算 2^100"
python scripts/test_agent.py stream "產生一張柴犬吃拉麵的圖"
python scripts/test_agent.py files                     # 看產出檔案
python scripts/test_agent.py reset                     # 清掉這個 session
python scripts/test_agent.py --session user-b chat     # 模擬另一個使用者
```

`chat` 模式裡的指令：

| 指令 | 行為 |
|---|---|
| `/memory` | 印出 `MEMORY.md`，看它記了什麼 |
| `/files` | 列出產出檔案 |
| `/reset` | 清掉這個 session |
| `/verbose` | 切換工具細節顯示 |
| `/quit` | 離開 |

每輪結尾會顯示耗時、模型耗時與快取命中，方便判斷延遲來源：

```
[1 turns · 6300ms（模型 1900ms）· in=2909 out=36 cached=5379]
```

## 能力自測

```bash
python scripts/test_agent.py selftest
python scripts/test_agent.py selftest --cases sandbox_escape,long_memory
```

| case | 測什麼 |
|---|---|
| `filesystem` | 建檔、讀回 |
| `code_execution` | 寫 Python、執行、拿到正確答案 |
| `multi_step` | 產資料 → 分析 → 產出報告檔 |
| `web_search` | 真的有去查網路 |
| `sandbox_escape` | 要求讀 `../../main.py` 和 `config.py`，**必須被擋** |
| `memory` | session resume 有接上 |
| `long_memory` | 講的偏好有寫進 `MEMORY.md` |
| `persona` | 問它用什麼模型，回答不能洩漏底層 |

所有 case 共用同一個 session 並依序執行，所以 `memory` 測的是真正的 resume。

## REST API

Swagger 在 `/docs`。

| Endpoint | 用途 |
|---|---|
| `GET /agent/health` | 設定檢查：key、CLI 路徑、模型對應、沙箱設定 |
| `POST /agent/run` | 跑一輪，回傳文字、工具紀錄、被擋紀錄、產物、token 用量 |
| `POST /agent/stream` | 同上，以 SSE 串流 |
| `POST /agent/selftest` | 能力自測套件 |
| `GET /agent/sessions` | 列出所有工作區 |
| `GET /agent/sessions/{id}/files` | 列出產出檔案 |
| `GET /agent/sessions/{id}/file?path=` | 讀某個產出檔案（同樣受沙箱保護） |
| `DELETE /agent/sessions/{id}` | 清掉整個工作區與記憶 |
| `POST /callback` | LINE webhook |
| `POST /broadcast` | 讓 agent 生成訊息並推播給所有好友 |

### `POST /agent/run`

```bash
curl -X POST localhost:8090/agent/run \
  -H "Content-Type: application/json" \
  -d '{
        "prompt": "幫我算台北到高雄開車的油錢，油價 31.5，車子 12km/L",
        "session_id": "demo",
        "resume": true
      }'
```

同一個 `session_id` 會共用工作目錄與對話歷史。

### `POST /agent/stream`

SSE，事件型別：`init`、`system`、`thinking`、`text`、`tool_use`、`tool_result`、
`blocked`、`result`、`error`、`done`。

`blocked` 是沙箱擋下的呼叫，`result` 帶完整的 token 用量與成本。

### `POST /broadcast`

取代舊版寫死的 `/recommend` 與 `/cwsChannel`：不再為每種推播各寫一條 LLM 呼叫路徑，
而是把要做什麼寫在 prompt 裡，由 agent 用跟聊天相同的工具和人設去查、去寫。

```bash
curl -X POST localhost:8090/broadcast \
  -H "Content-Type: application/json" \
  -d '{"prompt":"查今天台股收盤，寫成三句話的摘要","dry_run":true}'
```

`dry_run: true` 只回傳文字不真的推播，排程上線前先用它確認內容。
接 cron 服務（例如 cron-job.org）定時呼叫即可。

## 技能

把技能放進 `skills/<name>/`，裡面要有 `SKILL.md` 說明怎麼用。啟動時會唯讀複製到
`workspace/_skills/`，agent 會先讀 `SKILL.md` 再照著執行，不會自己猜指令格式。

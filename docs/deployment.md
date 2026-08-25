# 部署到 Render

從零到一個能用的 LINE Bot。照著做完就會通。

**流程**：取得金鑰 → Fork 專案 → 連到 Render → 設環境變數 → 設 Webhook → 加好友

---

## 步驟 1：取得三個金鑰

### 1-1. GLM API Key（AI 大腦，免費）

1. 前往 [BigModel 智譜開放平台](https://open.bigmodel.cn/) 註冊
2. 進入 [API Keys 管理頁](https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys)
3. 點「新增 API Key」，複製產生的字串

<img src="images/bigmodel-apikey.png" width="70%" />

免費、不需要信用卡。這把金鑰同時用於：對話（`glm-4.7-flash`）、圖片生成（`cogview-3-flash`）、
影片生成（`cogvideox-flash`）、圖片理解（`glm-4v-flash`）。

### 1-2. LINE Channel 金鑰（兩個）

1. 登入 [LINE Developers](https://developers.line.biz/en/)
2. 建立 `Provider` → 點 **Create**
3. 建立 `Channel` → 選 **Create a Messaging API channel** → 填基本資料
4. 進入 **Basic settings** → 找到 `Channel secret` → 這是 `LINE_CHANNEL_SECRET`
5. 進入 **Messaging API** → `Channel access token` 點 **Issue** → 這是 `LINE_CHANNEL_ACCESS_TOKEN`

順便在 **Messaging API** 頁面關掉「自動回應訊息」，否則官方罐頭訊息會蓋掉 bot 的回覆。

### 1-3. SerpAPI Key（選用）

只影響圖片搜尋的備援。免費爬蟲找不到圖時才會用到，可以先跳過。
需要的話去 [serpapi.com](https://serpapi.com/) 註冊。

---

## 步驟 2：Fork 專案

1. 登入 [GitHub](https://github.com/)
2. 打開本專案頁面，點右上角 **Fork**
3. 這樣你就有一份自己的副本，Render 會從它部署

---

## 步驟 3：在 Render 建立服務

> **Runtime 一定要選 Docker。** Render 原生的 Python 環境沒有 Node，而這個 agent 的核心是
> `claude` CLI（Node 程式），裝不起來。專案根目錄已經備好 `Dockerfile`。

最省事的方式是用專案根目錄的 `render.yaml`（Blueprint）——runtime、health check、
環境變數都寫在裡面，不用在網頁上一項一項填。

### 方式 A：用 Blueprint（建議）

1. 前往 [Render](https://render.com/) 註冊並登入
2. 點 **New +** → **Blueprint**
3. 選你 fork 的 repo，Render 會讀取 `render.yaml`
4. 它會提示你輸入標了 `sync: false` 的密鑰（`GLM_API_KEY`、兩個 LINE token）
5. **Apply**

### 方式 B：手動建立 Web Service

1. **New +** → **Web Service** → **Build and deploy from a Git repository**
2. 選你 fork 的 repo
3. **Language** 選 **Docker**（選了之後 Build / Start Command 欄位會消失，那些由
   `Dockerfile` 決定）
4. Branch `main`，Instance Type 先用 `Free`
5. 往下設[環境變數](#步驟-4設定環境變數)

### 已經有 Python runtime 的服務怎麼轉

> **Render 的 Dashboard 不能改 runtime。** 官方文件寫得很明確：
> *"Changing a service's runtime in the Render Dashboard is not currently supported."*
> Settings → Build → Source → Edit 那個 **Update Source** 對話框只能換 repo 和 branch，
> 沒有 Runtime 欄位。

如果你之前用原生 Python runtime 建過服務（Build Command 是 `poetry install`、
Start Command 是 `python main.py`），三個選擇：

**B-1. 用 Blueprint 接管（不會掉網址）**

1. Dashboard → **Blueprints** → **New Blueprint Instance**
2. 選你 fork 的 repo
3. 確認 `render.yaml` 裡的 `name` 與現有服務同名（預設是 `agent-line-bot`），
   Render 就會接管既有服務而不是另開一個
4. **Apply**

**B-2. 用 API 改**

```bash
curl -X PATCH https://api.render.com/v1/services/<SERVICE_ID> \
  -H "Authorization: Bearer <RENDER_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"serviceDetails":{"runtime":"docker"}}'
```

**B-3. 砍掉重建**

最直接，但服務網址會變，Webhook URL 要重新填一次。

---

## 步驟 4：設定環境變數

在建立畫面的 **Environment Variables** 區塊（或建立後到 **Environment** 分頁）加入：

### 必填

| Key | Value | 來源 |
|---|---|---|
| `GLM_API_KEY` | `你的金鑰` | 步驟 1-1 |
| `LINE_CHANNEL_SECRET` | `你的 secret` | 步驟 1-2 |
| `LINE_CHANNEL_ACCESS_TOKEN` | `你的 token` | 步驟 1-2 |

### 建議填

| Key | 建議值 | 說明 |
|---|---|---|
| `AGENT_TIMEZONE` | `Asia/Taipei` | 影響 bot 認為的「今天」 |
| `AGENT_WORKSPACE_ROOT` | `/data/workspace` | 持久化磁碟的掛載點，見步驟 5 |

#### 不設 `AGENT_WORKSPACE_ROOT` 會怎樣？

**不會壞掉，但記憶不會留下來。**

`Dockerfile` 裡已經預設 `AGENT_WORKSPACE_ROOT=/data/workspace`，所以不設也會用那個路徑；
本機執行（沒有 Docker）則落在專案目錄下的 `workspace/`。目錄不存在會自動建立，
所以功能一切正常。

差別在**持久性**：如果 `/data` 沒有掛持久化磁碟，那個目錄只存在於容器裡，
**每次部署或重啟就清空**——所有使用者的 `MEMORY.md`、`PERSONA.md`、產出的檔案全部消失，
bot 會忘記所有人。

判斷方法是打開 `/agent/health` 看 `sandbox` 區塊：

```json
"sandbox": {
  "workspace_root": "/data/workspace",
  "workspace": "writable",
  "survives_redeploy": true
}
```

- `workspace` 不是 `writable` → 路徑設錯或磁碟沒掛好，`problems` 會直接說明
- `survives_redeploy` 是 `false` → 工作區在應用程式目錄裡，部署一次就全沒了

### 選填

| Key | 預設 | 說明 |
|---|---|---|
| `SERPAPI_API_KEY` | — | 圖片搜尋備援 |
| `AGENT_TOOL_PROFILE` | `files` | 工具範圍，影響速度 |
| `AGENT_THINKING` | `false` | 開了會慢 5 倍 |
| `AGENT_MAX_TURNS` | `30` | 單輪最多幾步 |
| `GLM_FREE_MODEL` | `glm-4.7-flash` | 換模型用 |

完整清單見 [設定項](configuration.md)。

> **不要**把 `.env` commit 進 repo。`.gitignore` 已經擋掉了，金鑰一律用 Render 的
> Environment Variables 提供。

按 **Create Web Service**，等 build 完成（第一次約 5-10 分鐘，要裝 Node 和相依套件）。

---

## 步驟 5：掛持久化磁碟（強烈建議）

Render 的檔案系統是暫時的——**每次部署或重啟都會清空**，所有使用者的 `MEMORY.md` 和
`PERSONA.md` 會消失。

1. 進入服務的 **Disks** 分頁 → **Add Disk**
2. Mount Path 填 `/data`，大小 1GB 就夠
3. 確認 `AGENT_WORKSPACE_ROOT` 是 `/data/workspace`

用 Blueprint 的話，把 `render.yaml` 的 `plan` 改成 `starter`，並把最下面的 `disk:` 區塊
取消註解即可。

持久化磁碟需要付費方案。免費方案的替代方案見[下方](#免費方案的三個限制)。

順帶一提：**掛了磁碟就不能零停機部署**，Render 會先停舊的再起新的。對聊天機器人來說
影響不大。

---

## 步驟 6：設定 Webhook

1. 部署完成後，Render 會給你一個網址，例如 `https://your-app.onrender.com`
2. 先開 `https://your-app.onrender.com/agent/health` 確認狀態是 `ok`
   （如果是 `degraded`，`problems` 欄位會直接告訴你缺什麼）

   順便把 **Settings → Health Checks → Health Check Path** 填成 `/agent/health`，
   Render 才知道服務是不是真的活著。用 Blueprint 的話這項已經設好了。
3. 回到 LINE Developers → 你的 Channel → **Messaging API**
4. Webhook URL 填 `https://your-app.onrender.com/callback`
5. 打開 **Use webhook**，點 **Verify** 應該顯示 Success

<img src="images/line-webhook.png" width="70%" />

---

## 步驟 7：加好友開始用

到 [LINE Official Account Manager](https://manager.line.biz/account) → 選你的 bot →
**加好友工具** → 產生 QR code，掃描加入。

傳一句 `@help` 就會看到功能清單。

---

## 步驟 8：防止服務休眠（免費方案）

免費方案閒置 15 分鐘會休眠，冷啟動要幾十秒，第一則訊息很可能等到 LINE webhook timeout。

用 [cron-job.org](https://console.cron-job.org/jobs)（免費）每 10 分鐘打一次
`https://your-app.onrender.com/agent/health` 保持喚醒。

<img src="images/cronjob.png" width="70%" />

同一個服務也可以用來定時推播——把 `POST /broadcast` 排程即可，詳見 [使用方式](usage.md#post-broadcast)。

---

## 免費方案的三個限制

**1. 記憶會隨部署消失**

沒有持久化磁碟，`workspace/` 每次重啟就清空。三個選擇：

- 升級到付費方案 + Persistent Disk（最簡單）
- 把 `MEMORY.md` 改存到外部（Supabase / Firestore），要改 `agent_core/memory.py`
- 接受記憶會消失，只當一般問答 bot 用

**2. 會休眠**

見步驟 8。

**3. 512MB RAM 很緊**

實測單個 `claude` 子行程約 300–390MB RSS，加上 Python 服務會接近上限。同時多人使用可能
OOM。真的要穩定就升級 instance。

---

## 本機開發

不部署也能跑，方便改人設和測功能。

```bash
npm install -g @anthropic-ai/claude-code   # agent harness 本體
poetry install
cp .env.example .env                       # 填入 GLM_API_KEY
python main.py
```

```bash
python scripts/test_agent.py health        # 確認設定
python scripts/test_agent.py chat          # 互動對話
```

沒填 LINE 憑證也能跑，`/agent/*` 照常運作。

要接真的 LINE 測試，webhook 需要公開 HTTPS：

```bash
ngrok http 8090
```

<img src="images/ngrok.png" width="70%" />

把 `https://xxxx.ngrok-free.app/callback` 填進 LINE 的 Webhook URL。

> **Windows**：本機開發若要開熱重載請用 `RELOAD=true`。不開的話預設是關閉的——
> uvicorn 在 Windows 上啟用 reload 會換成不支援子行程的 event loop，
> `runner.py` 已經繞過這個問題，但保持關閉還是比較快。

---

## 容器化的額外好處

除了讓 CLI 裝得起來，容器也是[沙箱](sandbox.md#已知限制)真正硬化的方式：只掛載
`workspace/`，其他檔案系統對 agent 根本不存在，就不需要依賴指令字串分析。

如果安全性是主要考量，容器化的價值比部署便利性更高。

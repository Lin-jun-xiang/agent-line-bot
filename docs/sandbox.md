# 沙箱機制

agent 有完整的檔案讀寫與 shell 能力，所以「它能碰到什麼」是整個設計的核心限制。

## 目錄結構

```
workspace/                        ← AGENT_WORKSPACE_ROOT，不進版控
├── _sessions.json                對話 key → CLI session id
├── _shared/                      共用資料（唯讀）
├── _skills/                      ./skills 的副本（唯讀）
├── U1a2b3c.../                   某個 LINE 使用者的工作目錄  ← cwd，可讀可寫
│   ├── MEMORY.md
│   └── uploads/
└── C9x8y7z.../                   某個 LINE 群組的工作目錄
```

`skills/` 是唯讀複製進 `_skills/` 而不是直接掛載——這樣 agent 拿得到技能，卻從來不會持有
一個指向專案原始碼的路徑。

## 權限範圍

| 範圍 | 路徑 |
|---|---|
| 可寫 | 只有該對話自己的目錄 |
| 可讀 | 自己的目錄 + `_shared/` + `_skills/` |
| 拒絕 | 其他一切：專案原始碼、`.env`、`C:\Windows`、別人的 session |

session id 會先過 `workspace.slugify_session()`：只留 `[A-Za-z0-9._-]`，其他一律雜湊，
所以 `../../etc` 這種輸入不可能變成目錄名。

## 四層防線

**1. `cwd` 就在工作目錄裡**

相對路徑天生跑不出去。這是最便宜也最可靠的一層。

**2. `tools=` 白名單**

只開明確列出的工具。harness 內建的 `CronCreate`、`Workflow`、`EnterWorktree`、`SendMessage`
這些完全不會出現在模型眼前——`tools=` 是 availability 白名單，不是 allow 規則，所以以後
CLI 新增工具也不會漏進來。

**3. `PreToolUse` hook（主要防線）**

每次工具呼叫都先過 `guard.Sandbox.check_tool()`。這一層之所以是主力，是因為 hook 跑在所有
allow / deny 規則和 permission mode **之前**，而且 hook 的 deny 連 `bypassPermissions`
都蓋不掉。它是真的擋得住，不是建議。

**4. `can_use_tool` 後備**

白名單沒涵蓋到的呼叫，預設拒絕。

另外 `setting_sources=[]`，不載入主機的 `~/.claude/settings.json`——否則使用者本機的權限
設定和 Anthropic 金鑰會被繼承進來。

## shell 指令怎麼擋

`guard.check_bash()` 對指令字串做靜態分析：

- 絕對路徑必須落在可讀範圍內
- `..` 逃逸擋掉
- `~` / `$HOME` / `%USERPROFILE%` / `%APPDATA%` 等展開擋掉
- 破壞性指令黑名單：`sudo`、`git push`、`reg add`、`shutdown`、`mkfs`、`diskpart`…

自製的 `run_shell` 與內建 `Bash` 走**完全相同**的檢查，子行程的 `cwd` 也釘在 session 目錄。

不含 `..`、也不是絕對路徑的相對路徑直接放行——`cwd` 已經確保它跑不出去，多檢查只會製造
誤判（例如 `python -c "print('a/b')"`）。

## 自行讀檔的工具

`describe_image`、`generate_video` 和 `send_file` 會自己打開工作區裡的檔案，而 `PreToolUse`
hook 只看得到 `path` 這種不透明的字串參數，攔不住它們。所以這幾個工具在**函式內部**呼叫
`tools._resolve_in_workspace()` 做同一套包含判斷，測試裡也單獨驗證這條路徑。

新增任何會自己碰檔案系統的工具時，記得走同一個函式，不要以為 hook 會幫你擋。

`send_file` 還多一層：它把檔案交給 LINE 去抓，所以那個路徑會變成一個對外的網址。
`filelinks.public_url()` 因此**再做一次**包含判斷（對 session 目錄），
`/files` 路由收到請求時**又做一次**——簽章只證明連結是我們發的，不證明它現在還指向界內。
一次都不能省：symlink 可以在發連結之後才被放進工作區。

## 已知限制

> **這是字串分析，不是核心層邊界。**
>
> 目前的設計擋得住模型「不小心」跑出去，擋不住一個蓄意的越獄 prompt 加上夠刁鑽的 shell 技巧。

要真正硬隔離，兩個選擇：

- 把整個服務跑在容器裡，只掛載 `workspace/`
- 設 `AGENT_ALLOW_BASH=false`，完全關掉 shell（會失去計算與資料處理能力）

## 驗證

```bash
python -m pytest tests/test_sandbox.py -v
```

48 個測試，不需要 API key 或網路，涵蓋：

- 寫入 session 目錄內／外
- 寫入唯讀的 `_shared/`、`_skills/`
- 讀取專案原始碼與 `.env`
- `Grep` 指向工作區外
- shell 的相對路徑（放行）／絕對路徑逃逸／`..` 逃逸／家目錄展開／破壞性指令
- 惡意 session id（`../../escape`、`..`、`/`、`_shared`、`C:\Windows`）
- 自行讀檔的工具（`describe_image`、`generate_video`、`send_file`）的路徑範圍
- 檔案連結：過期、簽章錯、改路徑、`..` 逃逸、未知公開位址（`tests/test_filelinks.py`）

另外 `/agent/selftest` 裡的 `sandbox_escape` 案例會實際叫 agent 去讀 `../../main.py`
和 `config.py`，確認端對端真的被擋。

# 效能調校

免費層按 token 限流，所以 **prompt 大小直接決定延遲**。這份文件記錄實測數據與調校方式。

測試環境：`glm-4.7-flash` on BigModel 免費層。

## 工具數量的成本

harness 內建工具的 schema 很肥。實測同一句「hi」，只改工具數量：

| profile | 開放工具 | prompt tokens |
|---|---|---|
| `full` | 全部（含 Task / Skill / TodoWrite / Grep） | 7,400 |
| `lean` | `files` 再加 Glob + 內建 `Bash` | 3,400 |
| **`files`（預設）** | Read / Write / Edit + 自訂工具 | **2,650** |
| `chat` | 只有搜尋與媒體工具，不能碰檔案 | 1,600 |

個別成本：

| 工具 | tokens |
|---|---|
| `Task` + `Skill` + `Grep` + `TodoWrite` | ~4,000 |
| 內建 `Bash` | ~740 |
| `Read` + `Write` | ~615 |
| `Edit` | ~270 |
| 自訂 MCP 工具（每個） | ~70 |

## 為什麼不用內建 `Bash`

**內建 `Bash` 一個就比 `Read` + `Write` 加起來還貴**——它的描述塞了大量 git / 安全性 /
跨平台指示，這個專案完全用不到。

所以預設 profile 不含它，改用 `tools.py` 裡自製的 `run_shell`：同樣能跑指令、跑 Python、
產檔案，但描述只有十分之一大小，[沙箱檢查完全一樣](sandbox.md#shell-指令怎麼擋)。

推論一下就會發現，「只留 Bash，用 cat / echo 讀寫檔案」反而更花 token，沙箱也更難擋
（字串分析 vs 精確路徑比對），而且沒有 `Edit` 就只能整檔重寫。

實測 `lean`（含內建 `Bash`）與 `files` 的差距：

| 輪次 | `files` | `lean` |
|---|---|---|
| 你好 | 12.7s | 126.7s |
| 我叫阿翔 | 10.9s | 87.0s |
| 寫檔案 | 3.9s | 115.7s |

## 快取比 token 總數更重要

同樣 2,300 tokens 的 prompt，**快取沒中要 60 秒，命中只要 7 秒**。

所以 `prompt.py` 刻意分成兩段，用 `--- 本次對話 ---` 分隔：

- **前面**：不會變的人設與規則 → 每個 session、每一輪都是 byte-identical，可以被快取
- **後面**：每次都不同的日期、工作區路徑、`MEMORY.md`

前綴一致的好處是，新 session 的第一句就能吃到別的 session 暖好的快取。實測全新 session
第一輪的快取命中從 42 tokens 提升到 2,060 tokens，耗時從 70.8 秒降到 12.7 秒。

同一個道理：

- **日期只精確到「日」**。精確到分鐘會讓每一輪都快取失效。要幾點幾分 agent 會自己跑 `run_shell`。
- **`MEMORY.md` 上限 30 行 / 1,200 字元**。它每輪都進 prompt，放任長大會拖慢每一次回覆。

## 延伸思考預設關閉

`AGENT_THINKING` 預設 `false`。開著的話，模型回「收到」兩個字之前會先花一分半在腦內獨白。
實測同一句話：**109 秒 → 20 秒**。

需要處理複雜任務時再開。

## 模型選擇

不要退回舊的 `glm-4-flash`：

| | `glm-4-flash` | `glm-4.7-flash` |
|---|---|---|
| 你好 | 3.2s | 3.2s |
| 算 1~100 質數總和 | 7.1s → 答 **5177（錯）** | 10.2s → 答 **1060（對）** |
| 寫進檔案 | 5.6s | 6.8s |
| prompt caching | **完全沒有** | 有（實測命中 7,888） |

速度差不多，但答案錯了，而且拿不到快取。

## 目前的實測基準

預設設定、`glm-4.7-flash`、全新 session：

| 輪次 | 耗時 | 用到的工具 |
|---|---|---|
| 你好 | 3.2s | — |
| 算 1~100 質數總和，跑 python 確認 | 10.2s | `run_shell` ×2（答 1060） |
| 把答案寫進 primes.txt | 6.8s | `Write` |

## 還沒解決的問題

免費層仍有伺服器端變異：快取沒中的那一輪偶爾會跳到 60~90 秒，即使 prompt 只有幾百個新
token。這個沒有程式面的解法，要穩定延遲就得換付費端點（`GLM_BASE_URL` 指向
`https://api.z.ai/api/anthropic`）。

`scripts/test_agent.py chat` 每輪結尾會顯示耗時、模型耗時與快取命中數，卡住時可以直接
判斷是快取沒中還是真的在做事：

```
[1 turns · 6300ms（模型 1900ms）· in=2909 out=36 cached=5379]
```

# Dashboard Log Uploader 實作計劃書
*Web-based Log Uploader with LLM-guided Confirmation — Implementation Plan*

| 項目 | 內容 |
|------|------|
| 文件版本 | **v2.0**（已和需求方完成 28 題對齊，見附錄 A） |
| 建立日期 | 2026-10-19 |
| 目標系統 | Solo PIXI MP Monitor Dashboard（`solo-pixi-essential/`） |
| 部署目標 | `sit@10.20.31.40:/mnt/md127/pixi-solo-dashboard`（port 8001，**只部署這一套**） |
| 整合來源 | `log_splitter_app.py`、`log_uploader_app.py`（桌面版**繼續並行使用**） |
| LLM | `http://10.20.30.23:8000/v1`，`Qwen3.8-27B-UD-Q6_K_L`（llama.cpp） |
| 狀態 | **第一階段已實作，本機驗證完成**（分支 `feature/log-uploader-web`）；尚未部署到正式環境 |

---

## 0. 第一階段實作結果（2026-10-19，本機 hermesrunner）

**測試環境**：`docker compose -p pixi-local`（http://10.88.90.136:8001），DB 由正式環境當日 `pg_dump` 還原（69,872 筆、23 張工單）。正式環境只做唯讀操作，沒有寫入。

| 驗證 | 結果 |
|------|------|
| 單元測試 | 新增 25 項全部通過；套件共 89 項，另有 20 項是**既有**的失敗，與修改前完全相同 |
| 整合測試 E1–E8 | **29/29 通過** |
| **E3 逐欄比對** | 用網頁版上傳 005 的 4,335 筆，**81 個欄位全部**和正式環境桌面版上傳的資料一致（SHA-256 相同） |
| 效能 | 005（4,335 筆）約 16 秒；007（2,041 筆）約 8.5 秒 |
| 瀏覽器 E2E | 23 項中 22 項通過。唯一未通過的是 `/favicon.ico` 404，這是既有問題 |
| AI Report（E11） | 中文報告 48.6 秒 → **15.1 秒**；英文報告 25.4 秒 → **20.9 秒** |

**實作中發現並修正的問題**
- **Python 版本造成的數值差異**：桌面 exe 用 Python 3.11 打包，容器是 Python 3.12。3.12 的 `sum()` 改用補償加總，導致約 0.6% 的 WiFi 功率平均值在小數第 3 位差 0.001（26 個值）。現已在 parser 改用固定順序加總（`_plain_sum`），結果不再受 Python 版本影響，逐欄比對完全一致
- **排程輪播會強制換頁**：平日每 10 分鐘的強制輪播會打斷上傳頁面。依需求已**取消整個排程輪播**，側欄的「AI Report — Latest WO」按鈕保留，改為手動開啟。閒置 5 分鐘的換頁輪播保留，但上傳尚未完成時會暫停
- **單檔 bind mount**：修改 `app.py`、parser 或 HTML 後，必須重啟 api 容器才會生效（已寫入 AGENTS.md）

**和第 6 節設計的差異**：`upload_log` 把站別和無效明細等合併存放在 `facts JSONB`，並新增 `prev_target`（撤銷時用來還原目標數）、`failures`、`reverted`、`elapsed_sec` 欄位。

**手動測試前的重置（依需求）**：本機 DB 已清除 005、007 兩張工單的所有資料、目標數和上傳紀錄（剩 65,537 筆、22 張工單）。兩個 zip 保留在 `rawlogs/`，供手動上傳測試。

---

## 1. 目的與背景

### 1.1 現況
一張工單的 log 要寫入 Dashboard 資料庫，必須在 Windows 上用兩支桌面程式：

| 步驟 | 工具 | 動作 |
|------|------|------|
| 1 | 手動 | 解開工單 zip，例如 `5101-260630005.zip` |
| 2 | `log_splitter_app.py` | 對 10/20/30/40 每一站的 `Log_All.txt` 各執行一次，輸入工單號，切成單筆 `.txt` |
| 3 | `log_uploader_app.py` | 選擇切好的資料夾，直接連 PostgreSQL（port 5433）上傳 |

### 1.2 問題
- 每張工單要操作 4 次 Splitter 加 1 次 Uploader，容易漏站或打錯工單號
- 打錯工單號後，因為 hash 去重，**重傳也無法修正**（見第 8.2 節）
- 每台電腦都要安裝 exe 並設定 DB 連線，產線電腦還必須能直接連到 DB port
- 沒有上傳紀錄，事後無法追查是誰、何時、上傳了什麼

### 1.3 目標
在 Dashboard 側欄 **Management** 區新增 **📤 Log Uploader** 頁：
1. 使用者選擇工單 zip，伺服器先分析內容
2. **AI 助理用繁體中文對話**，引導使用者輸入**工單名稱**和**工單計畫生產數**
3. 伺服器用規則比對使用者輸入和 zip 的實際內容；AI 用口語說明差異，有警告時需要使用者二次確認
4. 確認後自動切檔、解析、寫入 DB，並即時顯示進度
5. 留下完整上傳紀錄（包含對話），並可以依批次撤銷

---

## 2. 範圍

| 包含（In Scope） | 不包含（Out of Scope） |
|------------------|-------------------------|
| Log Uploader 頁（Management 區），包含表單模式和 AI 對話模式 | 停用桌面 exe（繼續並行使用） |
| API：分析、驗證、對話、寫入、紀錄、撤銷 | Replace Mode（TRUNCATE 整個資料表） |
| 新增 `upload_log` 資料表，`module_test` 新增 `upload_id` 欄位 | 在伺服器保存切好的 `.txt`（raw log 已存在 `raw_log` 欄位） |
| 計畫生產數自動寫入 `alignment_targets` | 更新或匯入 `PIXI_WO_MAC_TABLE.csv` |
| **現有 AI Report 的 LLM 效能調整** | 部署到 `iw-solo-pixi-essential`（port 8004） |
| 本機完整驗證（用遠端 DB 的最新 dump） | |

---

## 3. 輸入規格

### 3.1 Zip 結構（依 `rawlogs/` 範例確認）
```
5101-260630005.zip
└─ 5101-260630005/                 ← 頂層資料夾（可能帶 log 前綴，例如 log5101-260630007/）
   ├─ 10/                          ← 站別資料夾（任意純數字資料夾都算站別）
   │  └─ log5101-260630005/
   │     ├─ Log_All.txt            ← ★ 處理目標（不分大小寫）
   │     ├─ Log_all.csv / log_summary.txt / *.jpg   ← 忽略，也不讀取
   ├─ 20/…/Log_All.txt
   ├─ 30/…/Log_All.txt
   └─ 40/…/Log_All.txt
```

### 3.2 規則（已對齊）
| 項目 | 規則 |
|------|------|
| 找檔 | 檔名不分大小寫等於 `log_all.txt`，而且路徑中有純數字資料夾（Q14） |
| 站別號 | 路徑中第一個純數字資料夾名稱 |
| 缺站 | **不提示**，有幾站就處理幾站（Q15） |
| 工單號格式 | `^\d{4}-\d{8,}$`（Q13），由使用者輸入（必填） |
| 工單號推斷 | 從 zip 檔名或頂層資料夾找 `\d{4}-\d{8,}`（可去掉 `log` 前綴），只用來比對和提示 |
| 無效段落 | 切出的檔名不符 `FILENAME_RE`（例如 MAC 是 `^%`），**跳過並列出明細**：站別、測試時間、原因（Q16） |

---

## 4. 實測驗證（已完成，唯讀）

### 4.1 切檔正確性
| 工單 zip | 段落數 | 有效 | PASS | FAIL | STOP | 無效 | 不重複 MAC1 |
|----------|--------|------|------|------|------|------|-------------|
| `5101-260630005.zip`（10.3 MB） | 4,339 | **4,335** | 3,976 | 333 | 26 | 4 | **3,977** |
| `log5101-260630007.zip`（5.0 MB） | 2,044 | **2,041** | 1,889 | 133 | 19 | 3 | **1,895** |

- 005 的 4,335 筆和桌面版輸出 `rawlogs/5101-260630005_S/` **檔名完全一致，sha256 也 4,335/4,335 全部相同**
- 遠端正式 DB 目前有 `5101-260630005` 共 **4,335 筆**（用桌面版上傳），筆數和這裡完全相符，可以拿來當逐欄比對的基準（第 10 節）
- `007` 不在遠端 DB，可以拿來測「新工單上傳」

### 4.2 效能
| 階段 | 4,335 筆耗時 |
|------|--------------|
| 讀 zip + 切檔 | 約 2.4 秒 |
| 解析 | 約 8.6 秒 |
| 寫入 DB（每筆 raw_log 約 25 KB） | 預估 20 至 60 秒（本機驗證時實測） |

### 4.3 LLM 實測（Qwen3.8-27B，擷取「工單 5101-260630005，共 4000 台」）
| 設定 | 耗時 | 結果 |
|------|------|------|
| 預設（thinking 開啟） | 20.7 秒 | 正確，但先產生約 330 字的思考內容 |
| thinking 開啟 + `max_tokens 64` | 88 秒 | ❌ content 是空的（token 全部用在思考上） |
| **thinking 關閉** | **1.3 秒** | ✅ |
| **thinking 關閉 + `json_schema`** | **1.1 秒** | ✅，並保證輸出是合法 JSON |

伺服器規格：llama.cpp `b10752`，`n_ctx 65536`，**4 個平行 slot**（每個約 16K），生成速度約 30 tokens/s，有 prompt cache。

---

## 5. 系統流程

```
使用者                         瀏覽器 (Log Uploader)                 FastAPI                          PostgreSQL / LLM
──────                        ─────────────────────                ───────                          ────────────────
選 zip ───────────────────►  POST /api/log-upload/analyze ───────► 存 /tmp/pixi_uploads/<id>.zip
                                                                    找 Log_All、切檔（不寫 DB）
                                                                    算事實：站別、筆數、MAC1、日期、
                                                                    無效明細、推斷工單號
                                                                    查 DB：已存在的 hash 和所屬工單 ─► SELECT
                              ◄──── facts ────────────────────────
                              ┌─ LLM 可用 → 對話模式 ─────────────────────────────────────────────┐
AI：「請輸入工單名稱」         │ POST /api/log-upload/chat (串流)                                    │
使用者自由回答 ─────────────►  │   ① LLM 擷取 {work_order, planned_qty}（json_schema，約 1 秒） ──► LLM
                              │   ② 伺服器規則驗證（第 7 節）                                        │
AI：「計畫生產數是多少？」◄─── │   ③ LLM 依驗證結果用口語回覆（串流，thinking 關閉） ─────────────► LLM
                              └─ LLM 不可用或超時 → 自動改用表單模式（兩個輸入框 + 同一套規則） ──┘
有警告就勾選「我確認無誤」
按「確認上傳」 ────────────►  POST /api/log-upload/commit (NDJSON 串流)
                                                                    取得全域上傳鎖 (advisory lock)
                                                                    寫 upload_log (status=running)
                                                                    逐筆 parse → INSERT (帶 upload_id) ► INSERT …
                              ◄──── progress (每 25 筆) ──────────  每 50 筆 commit
                                                                    寫 alignment_targets、更新 upload_log
                              ◄──── done ─────────────────────────  刪除暫存 zip
```

**設計理由**
- **兩階段（analyze → commit）**：對話時需要先知道 zip 的事實，才能比對。兩次請求之間的狀態存在容器內的 `/tmp/pixi_uploads/`；API 的 2 個 worker 在同一容器，共用檔案系統，任一 worker 都讀得到
- **commit 用 NDJSON 串流**：數十秒的處理過程可以即時顯示進度，也不會碰到 nginx 300 秒的 read timeout（nginx 已設 `proxy_buffering off`）
- **規則有最終決定權，LLM 負責對話**（Q1）：數字和格式由程式判斷，LLM 只負責引導、擷取、說明，避免幻覺造成誤判

---

## 6. 資料庫變更（由 `run_migrations()` 在啟動時自動套用）

```sql
-- 上傳批次紀錄（Q10：含完整對話）
CREATE TABLE IF NOT EXISTS upload_log (
    id              TEXT PRIMARY KEY,            -- upload_id (uuid4 hex)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    client_ip       TEXT,
    tweak_user      TEXT,
    zip_name        TEXT,
    zip_size        BIGINT,
    work_order      TEXT,
    inferred_wo     TEXT,
    planned_qty     INTEGER,
    unique_mac1     INTEGER,
    stations        JSONB,                       -- 各站筆數與 PASS/FAIL/STOP/無效
    invalid_items   JSONB,                       -- 無效段落明細
    validation      JSONB,                       -- 規則判定結果和使用者確認的警告
    chat_transcript JSONB,                       -- AI 對話全文（表單模式為 NULL）
    mode            TEXT,                        -- 'chat' | 'form'
    status          TEXT,                        -- analyzed | running | done | failed | reverted
    uploaded        INTEGER, skipped INTEGER, failed INTEGER,
    error           TEXT
);
-- 依批次撤銷（Q25）
ALTER TABLE module_test ADD COLUMN IF NOT EXISTS upload_id TEXT;
CREATE INDEX IF NOT EXISTS idx_module_test_upload_id ON module_test(upload_id);
```
- `upload_id` 可為 NULL，對桌面版完全沒有影響：桌面版的 `insert_record` 只寫明確列出的欄位，寫入的資料 `upload_id` 為 NULL
- 撤銷時只刪除 `upload_id = 該批` 的資料，**本來就存在而被 skip 的資料不受影響**
- `alignment_targets` 資料表已經存在，不需要變更

---

## 7. 驗證規則（伺服器端，表單和對話共用）

| 代碼 | 規則 | 等級 | 對齊題號 |
|------|------|------|----------|
| R1 | 工單號必填，格式為 `^\d{4}-\d{8,}$` | ❌ error（必須修正） | Q13 |
| R2 | 計畫生產數必填，必須是正整數 | ❌ error（必須修正） | Q2 |
| R3 | 工單號和 zip 推斷值不同 | ⚠ warn（需要二次確認） | Q11、Q4 |
| R4 | 部分資料已經存在，而且屬於**其他工單** | ⚠ warn（需要二次確認；那些資料會 skip） | Q12 |
| R5 | 計畫生產數和實測不重複 MAC1 的差異 | ℹ info（**只顯示差異，不判定對錯**） | Q3 |
| R6 | 部分資料已經存在於**同一工單** | ℹ info（會 skip，視同重傳） | Q12 |
| R7 | `alignment_targets` 已有目標數而且和輸入不同 | ℹ info（「將更新 A → B」，上傳成功後自動覆寫） | Q9 |
| R8 | 有無效段落 | ℹ info（列出明細） | Q16 |

- 有任何 ❌ 就不能上傳；有 ⚠ 時要勾選「我確認無誤」才能上傳（Q4）；ℹ 只顯示
- commit 時伺服器會**重新跑一次規則**，不信任前端傳來的判定結果

---

## 8. 模組設計

### 8.1 新增 `solo-pixi-essential/log_splitter.py`（純 Python，不依賴 Qt）
| 函式 / 常數 | 說明 |
|-------------|------|
| `SEGMENT_PATTERN`、`START_PATTERN`、`split_log_content`、`parse_segment` | 從 `log_splitter_app.py:169-209` 原樣搬出，演算法不變 |
| `WO_PATTERN`、`infer_work_order(*names)` | 推斷工單號（處理 `log` 前綴） |
| `find_log_all_entries(zf) -> [(station, member)]` | 依第 3.2 節規則找檔，依站別排序 |
| `split_to_records(content, work_order) -> (records, invalid_items)` | 每筆回傳 `{filename, content, result, mac1, start}`，內容為 `seg.rstrip() + "\n\n"`（確保 hash 相容） |

### 8.2 修改 `solo-pixi-essential/module_log_parser.py`
- 新增 `parse_log_content(content, filename)`，承接 `parse_log_file` 目前的主體
- `parse_log_file(filepath)` 改成讀檔後呼叫它，**對外行為不變**
- `insert_record(conn, rec, extra=None)`：新增選填的 `extra` 欄位（例如 `{"upload_id": ...}`）。桌面版呼叫方式不變

> ⚠ **工單號打錯的風險**：`file_hash` 只根據內容計算，**不包含工單號**。資料掛錯工單後，重傳會被 skip。對策是 R3 和 R4 的二次確認、AI 對話中的明確提示，以及事後用「依批次撤銷」移除錯誤批次後重傳。

### 8.3 API（`solo-pixi-essential/api/app.py`，全部需要 `X-Tweak-Token`，Q20）

| 端點 | 說明 |
|------|------|
| `POST /api/log-upload/analyze` | multipart `file`（.zip）。檢查 zip 合法性、Log_All 總大小上限 500 MB（防 zip bomb）。切檔並計算事實，查詢 DB 中已存在的 hash，寫入 `upload_log`（status=analyzed），回傳 `upload_id` 和 facts |
| `POST /api/log-upload/validate` | `{upload_id, work_order, planned_qty}` → `[{code, level, message}]`（表單模式和對話模式共用） |
| `POST /api/log-upload/chat` | `{upload_id, messages}` → 串流回覆，加上擷取出的 `{work_order, planned_qty}` 和規則結果（第 9 節） |
| `POST /api/log-upload/commit` | `{upload_id, work_order, planned_qty, ack_warnings, chat_transcript}` → NDJSON 串流（start / progress / done / error） |
| `GET /api/log-upload/history?limit=20` | 列出最近的上傳紀錄 |
| `DELETE /api/log-upload/{upload_id}` | 依批次撤銷：刪除這批新增的資料，status 改為 reverted（需要二次確認） |

**錯誤碼**：401（token）、400（不是 zip、沒有 Log_All、超過大小上限、驗證有 error、upload_id 不存在或已過期）、409（**已有其他上傳進行中**，Q26）。

**共通實作**
- **全域上傳鎖**：`pg_try_advisory_lock(<固定 key>)`，兩個 worker 都有效；拿不到鎖就回 409（Q26）
- 暫存 zip 放在 `/tmp/pixi_uploads/<id>.zip`。commit 完成後刪除；未 commit 的暫存檔超過 1 小時，就在下次 analyze 時清除
- 一次只把一站的 Log_All 解碼進記憶體（約 28 MB）；只讀 zip 內容、不解壓到磁碟，所以沒有路徑穿越問題
- 單筆寫入失敗時，用 savepoint rollback 那一筆並計入 failed，繼續處理下一筆
- `upload_log.chat_transcript` 存完整對話（Q10）

### 8.4 前端（`solo_pixi_dashboard.html`）
- 側欄 Management：`🔧 DB Tweak`、`⚖️ Data Alignment` 下方新增 **`📤 Log Uploader`**（`data-page="logupload"`）
- 登入：和 DB Tweak 共用 `tweakToken`，任一頁登入或登出，兩頁同步
- 介面全部使用**繁體中文**（Q7）

```
┌─ 📤 Log Uploader ──────────────────────────────────────────────────────┐
│ ① 選擇工單 ZIP  [ 拖放或點選 ]   5101-260630005.zip  10.3 MB  ✓ 已分析 │
│    站別 10/20/30/40 ｜ 有效 4,335 筆 ｜ 實測 3,977 台 ｜ 無效 4 筆 ▸明細 │
├────────────────────────────────────────────────────────────────────────┤
│ ② 確認資訊                         🟢 AI 助理（離線時自動改為表單）     │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ 🤖 已讀取 4 站共 4,335 筆測試。請輸入這批的工單名稱。               │  │
│  │                         👤 5101-260630005                         │  │
│  │ 🤖 工單 5101-260630005 和壓縮檔名稱一致 ✓。請問工單計畫生產數？     │  │
│  │                         👤 4000 台                                 │  │
│  │ 🤖 計畫 4,000 台，log 實測 3,977 台，差 23 台（僅供參考）。          │  │
│  │    目標數將寫入 Data Alignment。確認後即可上傳。                    │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  [ 輸入訊息…                                              ] [送出]     │
│  擷取結果：工單 5101-260630005 ｜ 計畫 4,000 台   ℹ×2  ⚠×0  ❌×0        │
│  ☐ 我確認無誤（有 ⚠ 時才出現，且必須勾選）                              │
│  [ 確認上傳 ]                                                          │
├────────────────────────────────────────────────────────────────────────┤
│ ③ 進度  ████████████████░░░░  3,100 / 4,335                            │
│ 結果  [新增 4,335] [略過 0] [無效 4] [失敗 0]   [撤銷此批]              │
├────────────────────────────────────────────────────────────────────────┤
│ 最近上傳紀錄：時間 ｜ 工單 ｜ 計畫數 ｜ 新增/略過/失敗 ｜ 狀態 ｜ 操作    │
└────────────────────────────────────────────────────────────────────────┘
```
- 表單模式（第一階段的預設模式，也是 LLM 的備援）：② 改成「工單名稱」和「計畫生產數」兩個輸入框，失焦時呼叫 `/validate` 並顯示同樣的 ℹ⚠❌
- 對話時 AI 回覆以串流方式逐字出現；處理期間按鈕 disabled
- 上傳完成後清除 filter-options 快取，Work Orders 等頁面就看得到新工單

---

## 9. LLM 設計（第二階段）

### 9.1 每輪對話的處理流程
1. **擷取**（非串流，thinking 關閉，`json_schema`，`max_tokens 128`，`temperature 0`）：從整段對話擷取 `{work_order: string|null, planned_qty: integer|null}`
2. **規則驗證**：伺服器用第 7 節規則判定，這一步不經過 LLM
3. **回覆**（串流，thinking 關閉，`max_tokens 512`，`temperature 0.3`）：system prompt 包含「事實摘要 + 規則結果 + 下一個要問的欄位」，要求 LLM 用繁體中文簡短回覆，**只能轉述規則結果，不可自行判定對錯**
4. 兩個欄位都取得、而且沒有 ❌ 時，前端出現「確認上傳」

### 9.2 Prompt 原則
- system prompt 固定放在最前面（命中 llama.cpp 的 prompt cache）；事實摘要放在後面
- 只帶最近 10 輪對話，總長度控制在 4K tokens 以內（遠低於每個 slot 的 16K）
- 防 prompt injection：使用者輸入只當資料，最終判定由規則決定，LLM 的輸出不會被當成指令執行

### 9.3 降級（Q8）
- 進入頁面時查 `/api/llm-status`；離線就直接使用表單模式
- 單次 LLM 呼叫逾時 60 秒，或擷取連續失敗 2 次 → 自動切換為表單，並保留已經擷取到的值，提示「AI 助理暫停，請改用表單」

### 9.4 現有設定與 AI Report 一起調整（Q22）
| 項目 | 現況 | 調整 |
|------|------|------|
| `LLM_MODEL` 預設值 | `Qwen3.6-35B-A3B-Q6_K`（已經不存在） | `Qwen3.8-27B-UD-Q6_K_L`，並寫進 `docker-compose.yml` 和 `.env.example` |
| AI Report thinking | 開啟（dense 27B 會明顯變慢） | 關閉（`chat_template_kwargs.enable_thinking=false`），約快 15 倍 |
| AI Report `max_tokens` | 未設定 | 依報告長度設定（例如 1,024） |
| `/api/llm-status` 輪詢 | 每 30 秒打 `/models` | 不變 |
> 注意：關閉 thinking 後，報告的用詞和推論層次可能略有不同。上線前會用 2 到 3 張工單對照新舊報告。

---

## 10. 測試計畫（在本機實際執行，Q17、Q18、Q19）

### 10.1 本機測試環境
1. **取得遠端最新資料（對遠端只讀）**：
   `ssh sit@10.20.31.40 "docker exec pixi-test-db pg_dump -U pixi -Fc pixi_test" > dbbackup/pixi_test_<日期>.dump`
   直接串流到本機，**遠端不會產生任何檔案**。DB 目前約 443 MB（69,872 筆，23 張工單），dump 檔放在 `dbbackup/`，並確認已經被 `.gitignore` 排除
2. **本機獨立 stack**：`docker compose -p pixi-local up -d`（port 5433/8001 本機目前沒人用；volume 獨立，不影響本機的 sitaskai 容器）
3. 用 `pg_restore` 還原 dump，然後啟動 API，讓 migration 自動套用

### 10.2 單元測試（加進 `test_dashboard_suite.py`，不需要 DB）
| # | 項目 | 預期 |
|---|------|------|
| U1 | `infer_work_order` 處理 `5101-260630005.zip` 和 `log5101-260630007` | 正確 |
| U2 | `find_log_all_entries(005.zip)` | 4 站 |
| U3 | 切檔結果和 `_S` 資料夾比對 | 4,335 個檔名和 hash 完全一致 |
| U4 | `parse_log_content` 和 `parse_log_file` | 結果相同 |
| U5 | 規則 R1 到 R8 的各種邊界情況 | 正確分級 |
| U6 | HTML：nav 位於 Management 區、有 `#page-logupload` 和 pageLoader | 通過 |
| U7 | 既有 74 個測試 | 全部通過 |

### 10.3 整合比對測試（在本機還原的 DB 上執行）
| # | 情境 | 預期 |
|---|------|------|
| E1 | 直接上傳 005 zip（DB 內已有桌面版上傳的 4,335 筆） | 新增 0，略過 4,335；R6 顯示「已存在於同一工單」 |
| E2 | 用錯誤工單號上傳 005 | R3 和 R4 出現 ⚠，未勾選確認時不能上傳 |
| E3 | **逐欄比對**：在本機刪除 005，再用網頁上傳 005 | 4,335 筆的**所有欄位**（排除 `id`、`created_at`、`upload_id`）和遠端用桌面版上傳的資料完全一致。比對基準：從遠端唯讀匯出 005 的資料（`COPY … TO STDOUT`） |
| E4 | 上傳 007（新工單），計畫數 2,000 | 新增 2,041 筆；`alignment_targets` 寫入 2,000；`upload_log` 完整 |
| E5 | 撤銷 E4 這一批 | 只刪除 007 的 2,041 筆，status 改為 reverted；005 不受影響 |
| E6 | 兩個上傳同時送出 | 第二個收到 409 |
| E7 | 非 zip、沒有 Log_All、工單號格式錯誤、沒有 token | 400 或 401 |
| E8 | 記錄 005 從 commit 到 done 的實際耗時 | 應在 300 秒內 |
| E9 | 第二階段：AI 對話完整流程（自由輸入「工單是5101-260630007，2000台」） | 正確擷取並顯示規則結果 |
| E10 | 第二階段：把 `LLM_API_BASE` 指向無效位址 | 自動改用表單，流程照常完成 |
| E11 | AI Report 新舊設定對照（2 到 3 張工單） | 速度提升，內容品質可以接受 |
| E12 | 瀏覽器 E2E（Playwright）：登入 → 選 zip → 對話或表單 → 上傳 → Work Orders 頁出現新工單 | 通過 |

---

## 11. 部署（10.20.31.40，只部署 pixi-solo，Q21）

**前置作業（Q23）**
1. 檢視本機尚未 commit 的修改（`solo_pixi_dashboard.html` 整份換行格式變更、`api/app.py`、`nginx/default.conf`、`AGENTS.md` 等），列出差異請你確認後 commit
2. `api/Dockerfile` **保留** `libpq-dev gcc` 那行，和遠端及 git HEAD 一致
3. 開新分支 `feature/log-uploader-web` 進行實作

**部署步驟（需要你同意才執行）**
1. 遠端備份：`app.py`、`module_log_parser.py`、`solo_pixi_dashboard.html`、`requirements.txt`、`docker-compose.yml`、`Dockerfile`，複製到 `oldbackup/<日期>/`。並先用 pg_dump 備份 DB，因為這次有 schema 變更
2. scp 上傳新檔，以及新增的 `log_splitter.py`
3. `docker compose up -d --build api`（requirements 有新增 `python-multipart`，需要 rebuild）。migration 會自動新增資料表和欄位
4. 驗證：`/health`；用小 zip 走一次流程，然後撤銷，確認正式 DB 沒有殘留

**回復（Rollback）**：放回備份檔並 rebuild。新增的資料表和欄位不影響舊版程式，可以保留；需要時再手動 `DROP`。

---

## 12. 工作分解與時程

### 第一階段：基本 Uploader（表單模式）與本機比對驗證（Q24）
| # | 工作項目 | 預估 |
|---|----------|------|
| P1-0 | 前置作業：檢視並 commit 現況、開分支 | 0.25 天 |
| P1-1 | `log_splitter.py` 和單元測試 U1 到 U3 | 0.5 天 |
| P1-2 | Parser 重構（`parse_log_content`、`insert_record(extra)`）和 U4 | 0.25 天 |
| P1-3 | Migration（`upload_log`、`upload_id`） | 0.25 天 |
| P1-4 | API：analyze / validate / commit / history / delete、鎖、暫存檔清理 | 1.5 天 |
| P1-5 | 前端：Log Uploader 頁（表單模式、進度、結果、紀錄、撤銷） | 1 天 |
| P1-6 | LLM 設定修正和 AI Report 調整（Q22） | 0.25 天 |
| P1-7 | 本機環境：dump、還原，執行 E1 到 E8、E11、E12 | 0.75 天 |
| | **第一階段小計** | **約 4.75 天** |

### 第二階段：AI 對話確認
| # | 工作項目 | 預估 |
|---|----------|------|
| P2-1 | `/api/log-upload/chat`：擷取、規則、串流回覆、降級 | 1 天 |
| P2-2 | 前端對話介面（串流顯示、擷取結果列、模式切換） | 1 天 |
| P2-3 | Prompt 調校和 E9、E10 測試 | 0.5 天 |
| P2-4 | 文件（AGENTS.md、使用說明）和部署 | 0.5 天 |
| | **第二階段小計** | **約 3 天** |

每個階段完成並在本機驗證後，都會先回報結果，經你同意才部署。

---

## 13. 可行性與完整性分析

### 13.1 可行性
| 面向 | 評估 | 依據 |
|------|------|------|
| 切檔邏輯移植 | ✅ 高 | 已實測和桌面版 100% 一致（4,335 筆檔名和 hash） |
| 解析與寫入 | ✅ 高 | 沿用既有 parser；正式 DB 已有同批 4,335 筆可以比對 |
| 大檔與逾時 | ✅ 高 | zip 約 5 至 10 MB；nginx 512 MB、300 秒；commit 用串流 |
| 多 worker 狀態 | ✅ 高 | 暫存檔放在容器共用的 `/tmp`，上傳鎖用 DB advisory lock |
| 撤銷與稽核 | ✅ 高 | 只新增 nullable 欄位和新資料表，桌面版相容 |
| LLM 對話 | ⚠ 中，可控 | 關閉 thinking 後擷取約 1 秒、回覆約 2 至 5 秒；有表單備援；4 個 slot 和 AI Report 共用 |
| 數量比對 | ✅（依 Q3 只顯示差異） | 不需要 WO-MAC 表 |

**結論：可行。** 第一階段屬於低風險移植，並以正式資料逐欄比對驗證；第二階段的 LLM 只負責對話，規則有最終決定權，而且有表單備援，風險可控。

### 13.2 完整性（原缺口的處理狀態）
| # | 項目 | 狀態 |
|---|------|------|
| C1 | 上傳紀錄／稽核 | ✅ `upload_log`（含對話） |
| C2 | 撤銷錯誤上傳 | ✅ 依批次撤銷 |
| C3 | 工單號打錯 | ✅ R3、R4 二次確認，加上撤銷 |
| C4 | 同時有多人上傳 | ✅ 全域鎖，回 409 |
| C5 | 遠端有兩套 stack | ✅ 只部署 pixi-solo |
| C6 | 介面語言 | ✅ 繁體中文 |
| C7 | WO-MAC 對照表 | ➖ 這次不處理 |
| C8 | LLM 設定不一致 | ✅ 一起修正，AI Report 一起調整 |
| C9 | 測試資料殘留 | ✅ 只在本機還原的 DB 測試；正式環境的 smoke test 會撤銷 |
| C10 | 桌面工具並存 | ✅ 繼續並行使用，hash 相容，桌面版寫入的資料 `upload_id` 為 NULL |

---

## 14. 風險與對策

| 風險 | 影響 | 對策 |
|------|------|------|
| 工單號打錯後重傳被 skip | 資料掛錯工單 | R3、R4 二次確認；依批次撤銷後重傳 |
| 桌面版和網頁版同時上傳同一工單 | 兩邊各自寫入部分資料 | hash 去重保證不重複；全域鎖只管網頁版。文件提醒同一工單不要兩邊同時操作 |
| LLM 回覆慢或亂答 | 體驗不佳 | thinking 關閉、json_schema、規則有最終決定權、逾時就降級 |
| LLM 被 AI Report 輪播佔用 | 對話延遲 | 4 個 slot；逾時 60 秒就降級為表單 |
| AI Report 關閉 thinking 後品質變化 | 報告內容變淺 | E11 新舊對照，必要時針對報告保留 thinking，但設定足夠的 max_tokens |
| schema 變更 | 部署風險 | 只新增 nullable 欄位和新資料表；部署前先 pg_dump |
| 上傳到一半中斷 | 部分寫入 | 已 commit 的資料保留，upload_log 標記 failed；可以重傳（自動 skip）或撤銷 |
| 本機有 847 MB 或 443 MB 的 dump 檔 | 誤 commit 進 git | 放在 `dbbackup/`，確認已加入 `.gitignore` |

---

## 附錄 A：需求對齊紀錄（2026-10-19，共 28 題）

| # | 問題 | 決定 |
|---|------|------|
| Q1 | LLM 角色 | 規則判定，LLM 負責對話 |
| Q2 | 「總產品數量」定義 | 工單計畫生產數 |
| Q3 | 數量判定 | 只顯示差異（和實測不重複 MAC1 比較） |
| Q4 | 判定不通過時 | 二次確認後可以上傳 |
| Q5 | 流程順序 | 先選 zip → 分析 → 再對話 |
| Q6 | 對話形式 | 引導式兩題（工單名稱、計畫生產數） |
| Q7 | 對話語言 | 繁體中文 |
| Q8 | LLM 不可用時 | 自動改用表單 |
| Q9 | 計畫生產數寫入 alignment_targets | 自動寫入 |
| Q10 | 上傳紀錄表 | 新增，含完整對話 |
| Q11 | 工單號和 zip 不一致 | 警告並二次確認 |
| Q12 | 資料已存在（屬於別的工單） | 跳過並提示 |
| Q13 | 工單號格式 | `^\d{4}-\d{8,}$` |
| Q14 | 站別規則 | 任意純數字資料夾 |
| Q15 | 缺站 | 不提示 |
| Q16 | 無效段落 | 跳過並列出明細 |
| Q17 | 本機測試資料 | 現在重新 dump 遠端 |
| Q18 | 比對方法 | 完整逐欄比對 |
| Q19 | 本機測試環境 | 獨立 docker compose |
| Q20 | 權限 | 沿用 DB Tweak 帳密 |
| Q21 | 部署目標 | 只部署 pixi-solo（port 8001） |
| Q22 | 現有 AI Report | 一起調整（模型名稱、thinking、max_tokens） |
| Q23 | 本機未 commit 的修改 | 先檢視並 commit，Dockerfile 保留 libpq 那行 |
| Q24 | 交付節奏 | 分兩階段（表單版本 → AI 對話） |
| Q25 | 撤銷 | 提供依批次撤銷 |
| Q26 | 並行控管 | 同時只允許一個上傳 |
| Q27 | 桌面 exe | 繼續並行使用 |
| Q28 | WO-MAC 對照表 | 這次不處理 |
| Q29 | 取消「最近工單 AI Report 輪播」 | 取消整個排程輪播（含背景預先產生報告），保留側欄手動按鈕 |
| Q30 | 「完成部署」的環境 | 本機測試環境（正式環境等手動測試通過後再部署） |
| Q31 | 「刪除這兩個檔案」 | 只清除 DB 中的資料，保留 zip |
| Q32 | 手動測試前的 DB 狀態 | 清空 005、007 兩張工單（資料、目標數、上傳紀錄） |

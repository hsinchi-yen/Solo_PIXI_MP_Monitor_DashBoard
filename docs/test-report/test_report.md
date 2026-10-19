# Solo PIXI MP Monitor Dashboard v3.0.0 — 測試報告

| 項目 | 內容 |
|---|---|
| 範圍 | [improve_plan.md](../../improve_plan.md) 的 14 項改善＋ 10 個既有 bug（B1–B10） |
| 日期 | 2026-10-19 |
| 分支 | `feature/log-uploader-web`（commit `4a762975` 起共 6 個 commit，最後一個含本報告） |
| 環境 | 本機 hermesrunner（10.88.90.136），**正式環境模式**：程式碼包進 api image，nginx 提供 `web/`，沒有原始碼掛載 |
| 資料 | 正式環境資料庫快照：`module_test` 65,537 筆、22 張工單 |
| 正式環境 | **尚未部署**（10.20.31.40 完全沒有動過，需要你同意後才執行） |

---

## 1. 結論

- **14 項改善全部完成**。10 個既有 bug 都已在本機逐一驗證，不再發生。
- **全部測試通過**：

| 測試 | 數量 | 結果 |
|---|---|---|
| 單元測試 Python（本機） | 71 | ✅ 全過（5 項跳過：本機沒有裝 FastAPI，這 5 項在 api image 內執行） |
| 單元測試 Python（api image 內） | 71 | ✅ 全過（1 項跳過：image 內沒有 Node，JS 測試在本機執行） |
| 單元測試 JavaScript（`node --test`） | 13 | ✅ 全過 |
| 改寫前後數字比對（基準） | 210 | ✅ **210/210 完全相同** |
| 整合測試（API＋資料庫） | 21 | ✅ 全過 |
| 瀏覽器端對端測試（Playwright） | 12 | ✅ 全過；同時產生本報告的截圖 |
| 個別驗證（B7、B10、索引、桌面版寫入、Reparse、部署演練、每日批次） | 7 | ✅ 見第 4–9 節 |

- **主要效能變化**（同一台機器、同一份資料；改善前的看板預設「今年」＝全部資料，下表改善後也用全部資料比較）：

| 項目 | 改善前 | 改善後 |
|---|---|---|
| WiFi 量測資料 | 3.1 秒、18 MB | 14 KB（含 BT 與其他）：第一次 0.39 秒，之後 4 ms |
| 舊 Advanced Analytics 頁（新「量測分析」） | 5 個 API，約 26 MB | 1 個 API：全部資料 14 KB，預設的最新工單 8 KB |
| 首頁資料 | 6 個 API，0.37 秒 | 1 個 API：第一次 0.20 秒，之後 4 ms |
| 瀏覽器開啟看板 | 2.3 秒 | 0.66 秒 |
| 10 人同時開首頁 | （改善前 2 人同時開就要 0.67 秒） | 0.33–0.36 秒 |

- 舊的單元測試有 20 項長期失敗；新的測試全部通過，並已加進 GitHub Actions。

---

## 2. 測試環境

| 項目 | 內容 |
|---|---|
| 主機 | hermesrunner，Docker Compose project `pixi-local`；容器 `pixi-test-db / api / nginx`；看板 <http://10.88.90.136:8001> |
| 版本 | API 3.0.0，2 個 uvicorn worker；PostgreSQL 16；nginx |
| LLM | 本機 llama.cpp，`Qwen3.8-27B-UD-Q6_K_L`（thinking 關閉） |
| 瀏覽器 | Chrome headless，透過 Playwright 1.55；1920×1080（產線電視），390×844（手機） |
| 上傳測試檔 | `rawlogs/5101-260630005.zip`（4,335 筆）、`rawlogs/log5101-260630007.zip`（2,041 筆，1,893 台）。測完都已撤銷，資料庫回到 65,537 筆 |

重現方式：

```bash
python tests/test_unit.py                                   # 單元測試（含 node --test "tests/js/*.test.mjs"）
docker run --rm --user root -v "$PWD":/repo:ro pixi-dashboard-api:3.0.0 \
  sh -c "pip install -q httpx && python /repo/tests/test_unit.py"   # FastAPI 相關測試
python tests/test_integration.py                            # 需要啟動中的 stack
python tests/test_e2e.py                                    # 需要 playwright；截圖寫到 docs/test-report/screenshots/
```

---

## 3. 14 項改善的驗收

| # | 項目 | 驗證方式 | 結果 | 截圖 |
|---|---|---|---|---|
| A1 | 首頁精簡 | E2E `test_01`：7 個數字（每個只出現一次）、4 張圖、預設最新工單、1920×1080 不需捲動 | ✅ | 01、05、14 |
| A2 | 只留 Dark／Light 兩種主題 | E2E `test_03`：預設深色，切換後重新整理仍維持；單元測試：只有兩套主題、`theme.css` 以外沒有寫死的顏色 | ✅ | 01、05 |
| A3 | 繁體中文介面 | 單元測試：每個 `t('key')`、`data-i18n` 都有對應字串，字典沒有重複 key；JS 測試：介面語言是繁中 | ✅ | 全部 |
| A4 | 讀取失敗與空資料提示 | E2E `test_11`：讓首頁 API 失敗，各區塊顯示「讀取失敗：無法連線到伺服器」，左下 API 燈變紅；API 恢復後自動回復 | ✅ | 18 |
| A5 | 篩選列、合併量測分析、輪播 | E2E `test_04`（全部／今天／本週／本月／自訂）、`test_05`（BT／WiFi／其他頁籤）、`test_11`（輪播） | ✅ | 06、07、08 |
| B1 | 伺服器端分箱 | E2E `test_05`：量測分析回應 < 50 KB（實測 8.0 KB）；平均、直方圖、百分位數和改寫前完全相同 | ✅ | 07、08 |
| B2 | 日期範圍查詢＋索引 | 舊參數（年／月／週／日）與新範圍參數結果相同；EXPLAIN 確認使用新索引（第 6.3 節） | ✅ | — |
| B3 | 統計集中到 materialized view | 210 項比對完全相同；網頁上傳後立即可見；桌面版直接寫入的資料在更新後可見 | ✅ | 14 |
| B4 | 連線池、錯誤格式、nginx | 整合測試：錯誤請求不會留下連線（B8）、錯誤統一為 `{error, code}` 且不回傳例外原文、gzip 生效 | ✅ | — |
| C1 | 單一 AI 報告視窗 | E2E `test_02`：中／英切換、可選取文字、複製、下載 Markdown、和上一份比較、追問、Esc 關閉 | ✅ | 02、03、04 |
| C2 | 事實表＋固定欄位輸出 | 單元測試：事實表、數字檢查、Markdown；7 份報告實際產生（第 8 節） | ✅ | 02、03 |
| C3 | 保存、自動產生、比較 | 整合測試：上傳後自動排入報告、資料沒變時沿用舊報告、每日批次；18:00 批次實際執行一次 | ✅ | 04 |
| C4 | 串流與快取一致性 | 整合測試：NDJSON 串流產生；B7 實測：兩個 worker 對「報告已過期」的判斷一致 | ✅ | — |
| D1 | 前端拆檔 | 單檔 5,774 行 → `index.html`＋3 個 CSS＋`main.js`＋7 個共用模組＋9 個頁面模組（共 4,087 行），不需要 build；單元測試確認 JS 用到的 id 都存在、沒有 inline style | ✅ | — |
| D2 | 後端分模組 | `app.py` 2,366 行 → `routers/`（7 個）＋`services/`（14 個）；pydantic 輸入驗證；`/health` 回報 DB 延遲、LLM、統計更新時間、版本 | ✅ | — |
| D3 | 測試修復＋整合測試＋CI | 本報告的全部測試；`.github/workflows/tests.yml` 每次 push 執行單元測試 | ✅ | — |
| D4 | Repo 清理 | git 追蹤的檔案 29,063 → 121（含本報告與 27 張截圖）；`.gitattributes` 統一換行；舊計畫移到 `docs/archive/` | ✅ | — |
| D5 | 部署流程 | `scripts/deploy.sh` 本機演練 3 種情境（第 9 節）；`scripts/backup_db.sh` 每日備份 | ✅ | — |
| E | 其他小修 | B9、B10、Reparse 背景化、分頁上限、錯誤訊息不外洩（見第 4、9 節） | ✅ | 15 |

---

## 4. 既有 bug（B1–B10）

| # | 問題 | 驗證 | 結果 |
|---|---|---|---|
| B1 | 點「AI 報告」後主畫面變空白 | E2E `test_02`：開啟報告時首頁仍顯示，關閉後也正常 | ✅ 已修正 |
| B2 | 報告點一下就關閉、文字無法選取、30 秒自動關閉 | E2E `test_02`：點報告內容不會關閉，可以選取文字，只有 Esc 或 ✕ 會關閉 | ✅ 已修正 |
| B3 | `year=2024&week=1` 混入 2024-12-30、31 | 單元測試 `test_iso_week_bug_b3_fixed`：`year=2024&week=1` 只涵蓋 2024-01-01～01-07；整合測試 `test_b3_iso_week`：`week=34` 與 08-17～08-23 的結果相同 | ✅ 已修正 |
| B4 | API 失敗時畫面沒有提示 | E2E `test_11`：各區塊顯示「讀取失敗：…」，API 燈變紅 | ✅ 已修正 |
| B5 | 輪播遇到 API 失敗就停住 | E2E `test_11`：首頁 API 失敗時，閒置 5 分鐘後仍切到工單頁；API 恢復後 30 秒切回首頁並正常顯示 | ✅ 已修正 |
| B6 | 「API: checking…」永遠不更新 | E2E `test_01`、`test_11`：燈號依實際請求結果變綠或變紅；右上角顯示「資料更新於 hh:mm」 | ✅ 已修正 |
| B7 | 刪除或撤銷後仍顯示舊報告，兩個 worker 不一致 | 實測：把 006 的計畫數改為 3991 後，連續 6 次查詢（兩個 worker 都有）都回傳「已過期」；改回 3990 後 6 次都回傳「最新」 | ✅ 已修正 |
| B8 | 查詢出錯時連線不關閉 | 整合測試 `test_b8`：連續送錯誤參數後，資料庫連線數不增加 | ✅ 已修正 |
| B9 | 不登入就能修改計畫數 | 整合測試 `test_b9`（未登入回 401）；E2E `test_09`：未登入時計畫數欄位唯讀 | ✅ 已修正 |
| B10 | 啟動時對 `module_test` 執行 ALTER，遇長查詢會卡住 | 實測：另開一個交易持有 `module_test` 的鎖，重新啟動 API，**2.2 秒後 health 正常**，沒有等鎖 | ✅ 已修正 |

---

## 5. 資料正確性

### 5.1 改寫前後比對（210 項）
改寫前先用舊版 API 把下列結果存成基準，改寫後逐項比對，**210/210 完全相同**：
摘要、PASS/FAIL/STOP 分布、每月統計、工單摘要（含平均測試秒數）、重測、失敗分析、未識別原因分析、失敗清單、單日每小時趨勢、BT／WiFi 的平均值、直方圖與百分位數。範圍包含全部資料、每張工單和多種日期條件。

### 5.2 資料庫結構沒有改動
- 整合測試 `test_module_test_columns_unchanged`：`module_test` 的欄位和 `schema.sql` 完全一致。
- 新增的都是獨立物件：`mv_unit_day`、`mv_unit`（統計 view）、`idx_mt_eff_date`（索引）、`ai_reports`、`job_runs`、`app_meta`（`test_new_objects_are_separate`）。
- 桌面版路徑：整合測試 `test_direct_insert_visible_after_refresh` 用和桌面 Uploader 相同的方式直接 INSERT，按「立即更新」後立即出現在統計中。

### 5.3 桌面版寫入速度（新增索引的影響）
寫入 4,335 筆（`5101-260630005`）：**有新索引 4.02 秒，沒有新索引 4.21 秒**。差異在量測誤差內，符合計畫「< 5%」的條件。

---

## 6. 效能

### 6.1 API（65,537 筆）
「第一次」是統計 view 更新後的第一個請求（快取是空的）；「之後」是接著 5 次中最快的一次。

| 改善前端點 | 改善前 | 改善後端點 | 第一次 | 之後 | 大小 |
|---|---|---|---|---|---|
| `/api/wifi-metrics` | 3.1 s ／ 18 MB | `/api/measurements`（BT＋WiFi＋其他一次回傳）單一工單 | 35 ms | 2.4 ms | 8.0 KB |
| `/api/bt-metrics`、`/api/test-duration`、`/api/calibration` | 0.61–0.69 s ／ 1.1–2.7 MB | `/api/measurements` 全部資料 | 386 ms | 4.2 ms | 14.1 KB |
| 首頁 6 個 API | 0.37 s | `/api/overview` 單一工單 | 160 ms | 1.9 ms | 2.2 KB |
| — | — | `/api/overview` 全部資料 | 200 ms | 3.7 ms | 14.9 KB |
| `/api/work-orders` | 0.6–0.7 s ／ 20 KB | `/api/work-order-summary` | 43 ms | 2.6 ms | 5.8 KB |
| `/api/summary` | 0.31 s | `/api/summary?year=2026` | 94 ms | 1.8 ms | 0.2 KB |
| `/api/retries` | 0.47 s ／ 1.3 MB | `/api/retries` | 18 ms | 5.8 ms | 49.8 KB |
| — | — | `/api/fails`（伺服器端分頁） | 6 ms | 5.4 ms | 22.0 KB |
| — | — | `/api/fail-analysis` | 7 ms | 6.9 ms | 2.0 KB |
| — | — | `/api/alignment` | 39 ms | 2.3 ms | 7.4 KB |
| — | — | `/api/filter-options` | 40 ms | 1.7 ms | 2.9 KB |

- 10 個人同時開首頁：331–357 ms（改善前 2 人同時開就要 0.67 秒）。
- nginx 開啟 gzip。另外移除了 upstream keepalive：它會讓小回應多等約 40 ms。

### 6.2 瀏覽器（Chrome，1920×1080，同一支量測腳本）
兩次量測都是「切換頁面 → 等網路靜止 → 再固定等 1.2 秒」，所以表中時間都包含這 1.2 秒。

| 頁面 | 改善前 | 改善後 | 改善後 API 回應大小 |
|---|---|---|---|
| 開啟看板 | 2.3 s | 0.66 s | — |
| 儀表板 | 2.0 s | 1.21 s | 2.2 KB |
| 工單 | 1.7 s | 1.21 s | 0.3 KB |
| 失敗清單 | 1.2 s | 1.21 s | 22.0 KB |
| BT／WiFi／Advanced → 量測分析 | 1.9／4.4／5.6 s | 1.20 s | 8.0 KB |
| 失敗分析 | 1.2 s | 1.21 s | 2.0 KB |
| 計畫數對齊 | 1.8 s | 1.21 s | 0.5 KB |

扣掉固定的 1.2 秒後，改善後每一頁的實際載入時間都在 0.01 秒左右；改善前 WiFi 頁約 3.2 秒，Advanced 頁約 4.4 秒。

### 6.3 日期篩選與索引

| 查詢（單日 2026-08-21） | 執行計畫 | 時間 |
|---|---|---|
| 舊寫法 `EXTRACT(YEAR/MONTH/DAY …) = …` | 全表平行掃描 | 16.9 ms |
| 新寫法 `COALESCE(unit_date, start_time::date) >= … AND < …` | `idx_mt_eff_date` 索引掃描 | 0.25 ms |

整個月的範圍（約全表的 14%）由 PostgreSQL 自行選用既有的 `(unit_date, start_time)` 覆蓋索引，5.6 ms。

### 6.4 版面
1920×1080、1600×900、2560×1440 的首頁都不需要捲動。1366×768 需要捲動，這不是主要目標尺寸。手機寬度（390 px）沒有橫向捲軸。

---

## 7. 網頁上傳

| 項目 | 結果 |
|---|---|
| 分析 `log5101-260630007.zip` | 4 個站別、2,041 筆有效測試、1,893 台、3 筆無效段落略過 |
| AI 對話 | 輸入「工單是 5101-260630007，2000 台」，AI 回覆「工單 5101-260630007 與計畫 2,000 台已確認，實測 1,893 台，差 107 台僅供參考。」 |
| 寫入 | 新增 2,041 筆，約 7.5–7.8 秒；完成後首頁立即可以選到新工單（截圖 14） |
| 重傳 | 2,041 筆全部略過（`file_hash` 去重，和桌面版相容） |
| 撤銷 | 2,041 筆刪除，統計立即更新 |
| 同時上傳 | 第二個上傳回 409（一次只允許一個上傳） |
| 帳號 | 整合測試 A1–A11：上傳者打不開、也撤銷不了別人的批次（403），上傳紀錄只列出自己的，管理者看全部；上傳者不能使用管理功能；停用後立即登出、無法登入；重設與修改密碼後其他登入失效；8 小時逾時；帳號異動與登入紀錄 |

---

## 8. AI 報告

| 項目 | 結果 |
|---|---|
| 產生時間（中英並行） | 9 份報告 8.9–19.2 秒（平均約 12 秒） |
| 數字檢查 | 9 份中 1 份（中文）被標記：模型把 3 個錯誤碼的次數相加寫成「70」（41＋16＋13），事實表沒有這個數字。重新產生後仍被標記，所以保留並在報告上方顯示黃色提示，這是設計好的行為。其餘 8 份中英文都通過 |
| 資料沒變 | 再按產生時直接沿用舊報告，不呼叫 LLM（`test_unchanged_data_reuses_report`） |
| 每日 18:00 批次 | 手動觸發一次：`5101-260630004` 產生報告（10.2 秒，中英文數字檢查都通過）；`5101-260630006` 資料沒變，略過 |
| 追問 | 「ERR_5 主要發生在哪個步驟？」→「ERR_5 主要發生在 BT_TX_BDR 步驟。」（截圖 04） |
| 比較 | 「和上一份比較」顯示兩份事實表的差異；資料相同時顯示「兩份報告的數據相同」 |
| LLM 離線 | 已產生的報告照常可看；產生和追問按鈕停用並顯示提示；上傳自動改用表單 |

---

## 9. 維運功能

| 項目 | 結果 |
|---|---|
| Reparse（背景重新解析失敗原因） | 全部 65,537 筆約 2.5 分鐘，0 失敗，結果和原本的資料相同；期間看板照常使用。整合測試 `test_start_progress_cancel`：啟動、進度、中途停止（停在 1,500 筆，截圖 15） |
| `deploy.sh --local` 演練 1：第一次部署 | 12.5 秒完成，health 正常 |
| 演練 2：更新 | 先備份檔案（`files.tgz`）和資料庫（`pg_dump`），再更新，health 正常 |
| 演練 3：故意讓 build 失敗 | 自動還原成上一版，舊版本恢復正常 |
| 健康檢查 | `/health` → `{"status":"ok","version":"3.0.0","db_ms":4.7,"stats_refreshed_at":…,"llm":{"connected":true,…}}` |

---

## 10. 截圖

全部由 `tests/test_e2e.py` 在本機正式環境模式下自動產生（`docs/test-report/screenshots/`）。

### 10.1 儀表板（A1、A2）
**01 深色主題（預設）**：7 個數字＋4 張圖，預設為最新工單 5101-260630006，1920×1080 不需捲動。
![01](screenshots/01_overview_dark.png)

**05 淺色主題**：圖表顏色跟著主題切換。
![05](screenshots/05_overview_light.png)

**改善前的首頁**（對照）：
![before overview](screenshots/before_overview.png)

### 10.2 AI 報告（C1–C3，B1、B2）
**02 中文報告**：上方數字由系統計算，AI 只寫總評、3 個發現、3 個建議；文字可以選取（藍色反白處）。
![02](screenshots/02_ai_report_zh.png)

**03 英文報告**：中英兩版同時產生，切換不需要等待。
![03](screenshots/03_ai_report_en.png)

**04 和上一份比較＋追問**
![04](screenshots/04_ai_report_compare_ask.png)

### 10.3 工單與篩選列（A5）
**06 自訂日期範圍（2026-08-01～08-31）**：3 張工單；良率和重測率依門檻上色，每列都可以開啟報告。
![06](screenshots/06_workorders_range.png)

改善前（對照）：
![before workorders](screenshots/before_workorders.png)

### 10.4 量測分析（A5、B1）：BT、WiFi、Advanced 三頁合併成一頁
**07 BT**
![07](screenshots/07_measurements_bt.png)

**08 WiFi**
![08 wifi](screenshots/08_measurements_wifi.png)

**08 其他（重測、測試時間、Xtal）**
![08 other](screenshots/08_measurements_other.png)

改善前的 BT、WiFi、Advanced 三頁（對照）：
![before bt](screenshots/before_bt.png)
![before wifi](screenshots/before_wifi.png)
![before advanced](screenshots/before_advanced.png)

### 10.5 失敗清單與失敗分析
**09 失敗清單**：伺服器端分頁（第 2 / 124 頁，共 6,176 筆），每筆都可以下載原始 log。
![09](screenshots/09_fails.png)

**10 失敗分析**：前 10 名失敗步驟與錯誤碼、未識別原因的 FAIL／STOP、明細表。
![10](screenshots/10_failanalysis.png)

改善前（對照）：
![before fails](screenshots/before_fails.png)
![before failanalysis](screenshots/before_failanalysis.png)

### 10.6 帳號與上傳
**11 帳號管理**：管理者、Dummy 測試帳號、E2E 測試帳號（測試結束後自動刪除）。
![11](screenshots/11_accounts.png)

**12 上傳 Log：分析結果＋AI 對話確認**
![12](screenshots/12_upload_ai_chat.png)

**13 上傳完成**：新增 2,041 筆，約 7.8 秒；上傳紀錄可以撤銷此批。
![13](screenshots/13_upload_done.png)

**14 上傳後首頁立即出現新工單**：Aligned 良率 94.45%，計畫缺口 107 台。
![14](screenshots/14_overview_new_wo.png)

### 10.7 管理功能
**15 資料庫維護＋背景 Reparse**（停在 1,500 筆是整合測試的「中途停止」）
![15](screenshots/15_dbtweak_reparse.png)

**16 計畫數對齊**：修改計畫數需要登入。
![16](screenshots/16_dataalign.png)

改善前（對照）：
![before dataalign](screenshots/before_dataalign.png)

### 10.8 手機與錯誤狀態
**17 手機寬度（390 px）**
![17](screenshots/17_phone_overview.png)

**18 API 失敗時的提示（B4、B6）**：各區塊顯示「讀取失敗」，左下 API 燈變紅。
![18](screenshots/18_api_error.png)

---

## 11. 與計畫不同之處與注意事項

| 項目 | 說明 |
|---|---|
| 首頁 API 數量 | 計畫寫 6 → 4 個，實作為 6 → **1** 個（`/api/overview`） |
| 統計 view | 計畫寫 `mv_unit_latest`；實作為 `mv_unit_day`（日期篩選用）＋`mv_unit`（不篩日期時用），結果和原本的 window function 相同 |
| 直方圖 | 計畫寫 `width_bucket()`；實作為 `FLOOR(值 / 間距)`，並把 0.5%–99.5% 以外的極端值併入兩端，效果相同 |
| 每月兩張圖 | 顯示所選範圍那一年的**全部工單**（圖上標註「2026 年・全部工單」），不跟著工單篩選 |
| 良率趨勢 | 範圍只有一天時每小時一點，否則每天一點（取代原本寫死的 07:00–19:00） |
| marked＋DOMPurify | AI 文字一律以純文字顯示，不轉成 HTML，所以不需要 DOMPurify |
| 一次性腳本與舊備份 | 沒有直接刪除，移到 `_local_archive/`（git 忽略），確認不需要後可以自行刪除 |
| 桌面工具 | `log_splitter_app.py`、`log_uploader_app.py`、`log_concatenate_app.py` 沒有修改，照常使用 |
| 桌面版上傳的顯示延遲 | 最多 5 分鐘，或按右上角「立即更新」 |
| 部署後 | 所有人需要重新登入一次 |

### 這次測試中發現並已修正的問題
| 問題 | 修正 |
|---|---|
| 失敗分析圖 20 條長條只顯示一半的標籤，同一錯誤碼重複出現 | 圖改為前 10 名，合併相同的錯誤碼與步驟，每條都有標籤；下方明細表不變 |
| 首頁「Aligned 良率」後面的「計畫缺口 107 台」在字中間斷行 | 計畫缺口改成獨立一行 |
| 手機寬度下 PASS/FAIL/STOP 圖例被截斷 | 窄螢幕改為圖例在下方 |
| 截圖時機：圖表動畫還沒結束、整頁截圖的側欄位置錯誤 | 測試程式等動畫結束後再截圖，整頁截圖前先捲回頂端 |

---

## 12. 尚未完成

1. **正式環境部署（10.20.31.40）**：需要你同意。執行 `scripts/deploy.sh`，它會先備份檔案與資料庫，失敗時自動還原。步驟見 [deployguide.md](../../deployguide.md)。
2. 部署時在正式機加上每日備份的 cron（`scripts/backup_db.sh`，保留 14 天）。
3. 計畫第 3 階段的驗收項目「在產線螢幕實看」需要你在現場確認。
4. Dummy 測試帳號（`dummy` / `dummy1234`）保留中。介面只能停用，需要時可以從資料庫刪除。

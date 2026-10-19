# Solo PIXI MP Monitor Dashboard v3.1.0 — 測試報告（英文為主、中文為輔）

| 項目 | 內容 |
|---|---|
| 日期 | 2026-10-20 |
| 範圍 | [bilingual_ui_plan.md](../../../bilingual_ui_plan.md)（Q45–Q52）：介面預設英文，可一鍵切換繁體中文 |
| 環境 | 本機 hermesrunner，compose 專案 `pixi-local`，production 模式；API 3.1.0、2 個 uvicorn worker、PostgreSQL 16、nginx；資料為正式環境 DB 快照（65,537 筆、22 張工單） |
| 正式環境 | 10.20.31.40 **未變更**，部署需要另外同意 |

## 1. 結論

所有測試都通過，資料庫架構沒有變更，統計數字和改版前完全相同。

| 測試 | 結果 | 本輪新增 |
|---|---|---|
| Python 單元測試（本機，無 FastAPI） | 80 項通過，5 項略過 | 9 項 |
| Python 單元測試（api image 3.1.0 內） | 80 項通過，1 項略過（Node.js 未安裝） | 同上 |
| JS 單元測試（Node） | 18/18 | 由 13 項增為 18 項（語言測試改寫並擴充） |
| 改版前後數字比對 | 210/210 相同 | — |
| 整合測試（API＋資料庫＋LLM） | 24/24 | 3 項（T8Language） |
| 瀏覽器端對端測試（Playwright，Chrome） | 15/15 | 3 項（test_12–14），其餘 12 項改為以英文驗證 |

## 2. 需求對照（附錄 A Q45–Q52）

| # | 決定 | 實作 | 驗證 |
|---|---|---|---|
| Q45 | 單一語言切換 | 整個畫面同時只顯示一種語言；資料內容（工單號、錯誤碼、步驟、姓名）照原樣 | E2E test_13：9 頁英文模式的介面文字沒有任何中文字 |
| Q46 | 側欄底部、主題鈕旁 | 英文畫面顯示「中文」，中文畫面顯示「EN」 | 截圖 19、E2E test_12 |
| Q47 | 瀏覽器記住＋網址參數 | `localStorage`（`pixi-lang`）；`?lang=en` / `?lang=zh` 優先；預設英文 | JS 測試 `initialLang`；E2E test_12：重新整理後保留、`?lang=en` 覆蓋 |
| Q48 | 全部，含錯誤與檢查訊息 | 9 頁、登入、彈出視窗、錯誤訊息（約 45 則）、上傳檢查 R1–R8、無效段落原因 | 單元：所有錯誤都有英文（AST 檢查）；整合：7 種實際錯誤回應都帶 `error_en` |
| Q49 | AI 全部跟著介面語言 | 報告視窗預設介面語言那一版；上傳 AI 助理用英文提示詞與英文回覆；追問帶 `lang` | E2E test_02／08／14；整合 T8：英文提問與上傳對話的回覆不含中文 |
| Q50 | 日期維持數字格式 | `10/20`、`2026/10/20 (Tue)`；只有每月圖表標籤為 `Jan … Dec` | JS 測試 `fmtClockDate`；截圖 01 |
| Q51 | 只改介面 | 操作手冊維持中文，新增「介面語言」一段 | — |
| Q52 | 直接實作並本機驗證 | 本報告 | — |

## 3. 語言切換的行為

| 情境 | 結果 | 依據 |
|---|---|---|
| 第一次開啟 | 英文、`<html lang="en">` | E2E test_01、test_12 |
| 按「中文」 | 約 40–130 ms 內整頁換成中文：側欄、篩選列、狀態燈、登入框、目前頁面和圖表都會重畫 | 實測 6 次：67／38／47／52／49／132 ms |
| 切換前後的數字 | 良率、台數、PASS／FAIL／STOP、重測率完全相同；工單篩選保留 | E2E test_12 |
| 重新整理 | 保留上次選擇 | E2E test_12 |
| `/?lang=en` | 優先於記住的語言 | E2E test_12 |
| AI 報告開著時切換 | 報告分頁跟著切換（中文介面顯示中文報告） | E2E test_14 |
| 計畫數對齊有未儲存的修改，或上傳進行中 | 只換文字，不重新載入資料，輸入內容不會遺失；對話紀錄維持原本的語言 | 程式邏輯（頁面的 `isBusy`）；這個情境沒有另外寫 E2E |
| 9 頁中文截圖 | 都在切換後即時重畫，沒有重新載入頁面 | 截圖 21_zh_01–09 |

## 4. 伺服器訊息（新增欄位，舊欄位不變）

| 類型 | 中文欄位（原有） | 英文欄位（新增） | 範例 |
|---|---|---|---|
| 錯誤回應 | `error` | `error_en` | `{"error":"帳號或密碼錯誤","code":"bad_credentials","error_en":"Wrong username or password."}` |
| 串流事件 | `message`、`reason` | `message_en`、`reason_en` | 報告產生失敗、上傳中斷、AI 助理離線 |
| 上傳檢查 R1–R8 | `message` | `message_en` | R5：`Planned 2,000 units; the logs have 1,893 unique MAC1, a difference of +107 (for reference).` |
| 無效段落原因 | `reason` | `reason_en` | `Invalid MAC (not hexadecimal)` |
| AI 助理開場白 | `chat_opening` | `chat_opening_en` | `Read 4 stations (10 / 20 / 30 / 40): 2,041 valid tests, 1,893 units tested; …` |

- 寫法：`Msg("中文", "English")`。單元測試會掃描 `api/` 的所有錯誤呼叫，只有中文的寫法會失敗。
- 保留原樣：AI 報告事實表中空白類別的「未分類」，因為它是已存報告 `data_hash` 的一部分；英文介面顯示時改為 “Uncategorized”。

## 5. 資料正確性與相容性

| 檢查 | 結果 |
|---|---|
| 改版前後的統計數字比對（summary、trend、分布、工單、量測等 10 種篩選） | 210/210 相同 |
| `module_test` 欄位 | 未變更（整合 `test_module_test_columns_unchanged`） |
| 新增或修改的資料表 | 無；只有 API 回應多了欄位 |
| 桌面版直接寫入 DB 的路徑 | 整合 T7 通過 |
| Web 上傳 → 統計 → 撤銷 | 整合 T4、E2E test_08 通過（2,041 筆新增後撤銷） |
| 測試結束後的資料庫 | 65,537 筆、22 張工單；測試建立的上傳紀錄與暫存檔已清除；帳號只剩 dummy |

## 6. 效能

| 項目 | v3.0.0 | v3.1.0 |
|---|---|---|
| 瀏覽器首次開啟看板（Playwright，含 4 張圖） | 0.66 s | 0.69–0.93 s（3 次測量） |
| 介面字典（gzip） | 中文字典＋邏輯 7.6 KB | 英文 6.8 KB＋中文 7.5 KB＋邏輯 1.4 KB |
| 語言切換 | — | 38–132 ms |
| 量測頁傳輸量 | 8.0 KB | 8.0 KB |

字典多了約 8 KB（gzip），首次開啟的差距在測量誤差範圍內，以後的開啟都會用瀏覽器快取。

## 7. 截圖（`screenshots/`，1920×1080，Dark）

英文是預設畫面；中文截圖是在同一個頁面按下切換鈕後即時拍攝。

| 檔案 | 內容 |
|---|---|
| 01_overview_dark | 儀表板（英文）：Yield、Aligned Yield、Units Tested、Retry Rate；月份 `Jan … Dec`；側欄日期 `2026/10/20 (Tue)` |
| 02_ai_report_en | AI 報告：英文介面開啟時預設 English 分頁；標題保持一行 |
| 03_ai_report_zh | 同一份報告切到中文分頁（介面仍是英文） |
| 04_ai_report_compare_ask | 和上一份比較；用英文追問，AI 用英文回答 |
| 05_overview_light | Light 主題（英文） |
| 06_workorders_range | 工單頁，自訂日期範圍 |
| 07_measurements_bt／08_measurements_wifi／08_measurements_other | 量測分析三個分頁 |
| 09_fails | 失敗清單，第 2 頁（`Page 2 / …`） |
| 10_failanalysis | 失敗分析 |
| 11_accounts | 帳號管理 |
| 12_upload_ai_chat | 上傳：英文開場白、英文提問、AI 英文回覆、R5／R7／R8 英文檢查訊息 |
| 13_upload_done | 上傳完成（`Upload complete in … s`），新增／略過／無效／失敗四個數字 |
| 14_overview_new_wo | 上傳後新工單立即出現在儀表板 |
| 15_dbtweak_reparse | 資料庫維護與 Reparse 狀態 |
| 16_dataalign | 計畫數對齊 |
| 17_phone_overview | 手機寬度（390 px）：篩選按鈕一行、沒有橫向捲動 |
| 18_api_error | API 失敗時顯示 `Failed to load: …`、狀態燈變紅 |
| 19_lang_button_en／19_lang_button_zh | 切換鈕：英文畫面顯示「中文」，中文畫面顯示「EN」 |
| 20_overview_zh | 切換後的儀表板（中文），數字和 01 相同 |
| 21_zh_01–09 | 9 頁的中文畫面（儀表板、工單、失敗清單、量測、失敗分析、資料庫維護、計畫數對齊、上傳 Log、帳號管理） |

v3.0.0 的截圖保留在 `../screenshots/`，作為上一版的紀錄。

## 8. 測試中發現並修正的問題

| 問題 | 原因 | 修正 |
|---|---|---|
| 側欄「AI Report (Latest WO)」折成兩行 | 英文較長 | 改為 “AI Report (Latest)” |
| 報告標題「AI Quality Report — 5101-260630006」在工單號的連字號處斷行 | 標題區塊會被右側按鈕擠窄 | 標題不再縮到比文字還窄，改讓按鈕換到下一行（`.modal-title { flex: 1 0 auto; max-width: 100% }`） |
| 報告「前 3 張工單平均良率」數字框擠成 4 行 | 「100.00% (-0.03)」放不進窄框 | 差值改成下方小字 “This WO -0.03 pts”，標籤改為 “Prev 3 WOs Yield” |
| 手機上「This Week／This Month」折成兩行 | 英文按鈕較寬 | 900 px 以下縮小按鈕內距、不換行 |
| api image 內 `pip install httpx` 失敗 | image 以非 root 使用者執行 | 測試指令改為 `--user root`（AGENTS.md 已更新） |

## 9. 尚未完成

1. **部署到正式環境 10.20.31.40**：需要你同意後才執行 `scripts/deploy.sh`。v3.0.0 和 v3.1.0 會一起部署。部署前會備份，失敗時自動還原。
2. 產線大電視上的實際觀看確認。如果某台電視要固定顯示中文，網址加上 `?lang=zh`。
3. 名詞對照（`bilingual_ui_plan.md` 第 2.6 節）如果主管有偏好的用詞，只要修改 `web/js/core/strings.en.js`，不需要重建 image。

## 附錄：重跑方式

```bash
python tests/test_unit.py                                   # 本機（含 node --test）
docker run --rm --user root -v "$PWD":/repo:ro pixi-dashboard-api:3.1.0 \
  sh -c "pip install -q --root-user-action=ignore httpx && python /repo/tests/test_unit.py"
python tests/test_integration.py                            # 需要 stack 與 docker
python tests/test_e2e.py                                    # 需要 playwright；截圖寫到本資料夾
```

# 測試 Log 自動分拆執行計畫書
*Production Line Test Log Splitting Execution Plan*

| 項目 | 內容 |
|------|------|
| 文件版本 | v1.0 |
| 建立日期 | 2024-12-05 |
| 原始檔案 | Log_All.txt |
| 原始檔案大小 | 5,636,695 bytes（約 5.4 MB） |
| 總行數 | 142,834 行 |
| 涵蓋時間 | 2024/12/05 08:31:01 ～ 17:32:42 |
| 總測試筆數 | 235 筆 |
| 執行環境 | Ubuntu 24 / Python 3 |

---

## 1. 目的與背景

本計畫書說明如何將產線測試機台所產生的累計式測試 Log 檔（`Log_All.txt`），依照每一筆獨立的測試紀錄，自動分拆成各自獨立的 `.txt` 檔案，並以標準化命名方式儲存，方便後續追蹤、查驗與分析。

### 1.1 問題說明

原始 Log 檔為「連續累加」格式，由同一產品多次測試所產生，所有測試紀錄依時間順序直接串接於單一檔案中，無明確分隔符號。若要針對單一測試紀錄進行查驗或追溯，必須手動搜尋，耗費大量時間且容易出錯。

### 1.2 預期效益

- 每筆測試一個獨立檔案，便於快速定位特定 MAC / 時間的測試結果
- 檔名直接呈現測試結果（PASS / FAIL / STOP），無需開啟檔案即可判斷
- 後續可批次匯入資料庫或 QA 系統
- FAIL 與 STOP 的紀錄可快速篩選，加速不良品追蹤

---

## 2. 輸入 / 輸出規格

### 2.1 輸入檔案

| 項目 | 說明 |
|------|------|
| 檔案名稱 | Log_All.txt |
| 編碼 | UTF-8（含 CRLF 換行，即 `\r\n`） |
| 結構 | 多筆測試紀錄依序串接，無分頁或分隔符號 |
| 每筆起始特徵 | 行首為 `MAC1:\t`（Tab 分隔） |
| 每筆結尾特徵 | `End:\t\t YYYY/MM/DD HH:MM:SS` 後接空行 |
| 結果標記位置 | `End:` 行之前 3 行內 |

### 2.2 輸出檔案

| 項目 | 說明 |
|------|------|
| 輸出目錄 | `split_logs/`（與腳本同層，自動建立） |
| 檔案格式 | `.txt`，UTF-8，LF 換行 |
| 命名規則 | `YYYYMMDD_HHMMSS_MAC1_MAC2_RESULT.txt` |
| 命名範例 | `20241205_083101_001F7B5F0AC6_001F7B5F0AC7_PASS.txt` |
| 內容 | 單一完整測試紀錄，首行 `MAC1:`，尾行 `End:` 後接兩個換行 |

### 2.3 測試結果分類

腳本會偵測每筆測試內的結果標記字串，共三種類型：

| 結果類型 | 標記字串 | 本次數量 | 說明 |
|----------|----------|----------|------|
| PASS | `**** P A S S ****` | 200 | 測試全數通過 |
| FAIL | `**** F A I L ****` | 26 | 至少一項測試失敗 |
| STOP | `**** S T O P ****` | 9 | 測試中途中止 |

> **注意：** 本次 `Log_All.txt` 實際結果分布：PASS 200 筆、FAIL 26 筆、STOP 9 筆，合計 235 筆。

---

## 3. Log 檔案結構解析

### 3.1 單筆測試紀錄結構

每筆獨立測試由以下固定欄位組成（以 Tab `\t` 作為欄位與值的分隔符號）：

| 欄位 | 範例值 / 說明 |
|------|--------------|
| `MAC1:\t` | `001F7B5F0AC6`（第一張網卡 MAC，分拆的起始特徵） |
| `MAC2:\t` | `001F7B5F0AC7`（第二張網卡 MAC） |
| `Start:\t` | `2024/12/05 08:31:01`（測試開始時間，用於命名） |
| 測試項目 1～N | 依序列出 BT / WiFi 各測試步驟及量測數值 |
| 結果標記 | `**** P A S S ****` 或 `**** F A I L ****` 或 `**** S T O P ****` |
| `End:\t\t` | `2024/12/05 08:32:39`（測試結束時間） |
| `Test Time:` | `01:38.3`（本次測試耗時） |

### 3.2 相鄰兩筆紀錄的銜接方式

兩筆測試之間沒有任何分隔標記，第一筆的 `Test Time:` 行後緊接著第二筆的 `MAC1:` 行（中間僅有空白行），示意如下：

```
…（第 N 筆測試內容）
        **** P A S S ****

End:		2024/12/05 08:32:39

Test Time:  01:38.3


MAC1:	001F7B5F0AD0    ← 第 N+1 筆起始
MAC2:	001F7B5F0AD1
Start:	2024/12/05 08:33:30
```

> **注意：** 因此分拆邏輯採用「向前看（lookahead）」的正規表達式切割，以確保 `MAC1:` 行歸屬於下一筆、不被切掉。

---

## 4. 分拆邏輯說明

### 4.1 整體流程

1. 讀取整份 `Log_All.txt` 至記憶體
2. 將所有換行統一正規化為 LF（`\n`），去除 Windows 的 CR（`\r`）
3. 以正規表達式 `(?=^MAC1:\t)` 做 lookahead 分割，取得所有 segment（片段）
4. 濾除不以 `MAC1:` 起始的片段（開頭可能存在的空白內容）
5. 逐一解析每個 segment，提取 MAC1、MAC2、Start 時間、結果標記
6. 組合檔名並寫出至 `split_logs/` 目錄
7. 輸出統計摘要

### 4.2 關鍵解析規則

#### MAC1 / MAC2 提取

找到行首為 `MAC1:\t` 或 `MAC2:\t` 的行，取 Tab 之後的字串，去除首尾空白。

```python
if line.startswith("MAC1:"):
    mac1 = line.split("\t")[-1].strip()
```

#### Start 時間提取

找到行首為 `Start:\t` 的行，取 Tab 之後的字串，格式為 `YYYY/MM/DD HH:MM:SS`。

- 日期部分：取前 10 字元，去除 `/` → `YYYYMMDD`
- 時間部分：取第 11 字元之後，去除 `:` → `HHMMSS`

```python
date_str = dt[:10].replace("/", "")    # 20241205
time_str = dt[11:].replace(":", "")   # 083101
```

#### 結果標記偵測

在每行（去除首尾空白後）以字串包含（`in`）方式偵測：

- 偵測到 `**** P A S S ****` → `result = "PASS"`
- 偵測到 `**** F A I L ****` → `result = "FAIL"`
- 偵測到 `**** S T O P ****` → `result = "STOP"`

> **注意：** 三種結果在同一筆內只會出現其中一種。若一筆內均未出現，視為異常，該筆將被跳過並於終端機輸出警告。

#### 檔案命名組合

```python
filename = f"{date_str}_{time_str}_{mac1}_{mac2}_{result}.txt"
# 範例：20241205_083101_001F7B5F0AC6_001F7B5F0AC7_PASS.txt
```

#### 輸出格式

每個 segment 的內容先去除尾端多餘空白，再補上兩個換行後寫出，以清楚標示每筆結束。

---

## 5. 執行步驟（代理人操作指引）

> **注意：** 以下步驟假設代理人使用 Linux / macOS 終端機或 Windows WSL 環境，具備 Python 3.6+ 執行能力。

### 步驟 1：確認環境

確認 Python 版本 ≥ 3.6：

```bash
python3 --version
```

無需安裝任何第三方套件，本腳本僅使用 Python 標準函式庫（`re`、`os`）。

### 步驟 2：準備工作目錄

1. 建立工作資料夾，例如 `/work/log_split/`
2. 將 `Log_All.txt` 放入此資料夾
3. 將 `split_log.py` 腳本放入同一資料夾

完成後目錄結構應如下：

```
/work/log_split/
  ├── Log_All.txt
  └── split_log.py
```

### 步驟 3：確認腳本設定值

開啟 `split_log.py`，確認以下兩個變數路徑正確：

```python
INPUT_FILE = "/work/log_split/Log_All.txt"   # 修改為實際路徑
OUTPUT_DIR = "/work/log_split/split_logs"    # 輸出目錄（會自動建立）
```

### 步驟 4：執行腳本

```bash
cd /work/log_split
python3 split_log.py
```

正常執行後，終端機會輸出類似：

```
Found 235 segments
Done! 235 files written, 0 skipped → /work/log_split/split_logs
Result summary: {'PASS': 200, 'FAIL': 26, 'STOP': 9}
```

### 步驟 5：驗證輸出

1. 確認 `split_logs/` 目錄已建立，且內含 235 個 `.txt` 檔案
2. 隨機抽取 3 筆 PASS、2 筆 FAIL、1 筆 STOP，開啟檢查：
   - 首行應為 `MAC1:\t`（對應檔名中的 MAC1）
   - `Start:` 欄位的日期時間應對應檔名前段
   - 檔案末端應可看到對應結果標記與 `End:` 時間
3. 執行以下指令確認檔案數量：

```bash
ls split_logs/ | wc -l           # 應輸出 235
ls split_logs/ | grep PASS | wc -l   # 應輸出 200
ls split_logs/ | grep FAIL | wc -l   # 應輸出 26
ls split_logs/ | grep STOP | wc -l   # 應輸出 9
```

---

## 6. 分拆腳本完整內容（split_log.py）

請將以下程式碼完整複製，另存為 `split_log.py`，存放路徑需與 `Log_All.txt` 相同目錄（或修改 `INPUT_FILE` 變數）。

```python
import re
import os

INPUT_FILE = "/work/log_split/Log_All.txt"   # ← 修改為實際路徑
OUTPUT_DIR = "/work/log_split/split_logs"    # ← 修改為輸出目錄

os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(INPUT_FILE, "r", encoding="utf-8", errors="replace") as f:
    content = f.read()

# 統一換行符號為 LF
content = content.replace('\r\n', '\n').replace('\r', '\n')

# 以 lookahead 切割，保留 MAC1: 於下一個 segment 起始
segments = re.split(r'(?=^MAC1:\t)', content, flags=re.MULTILINE)
segments = [s for s in segments if s.strip().startswith("MAC1:")]

print(f"Found {len(segments)} segments")

success = 0
skipped = 0

for seg in segments:
    lines = seg.strip().split('\n')
    mac1 = mac2 = date_str = time_str = result = None

    for line in lines:
        line = line.strip()
        if line.startswith("MAC1:"):
            mac1 = line.split("\t")[-1].strip()
        elif line.startswith("MAC2:"):
            mac2 = line.split("\t")[-1].strip()
        elif line.startswith("Start:"):
            dt = line.split("\t")[-1].strip()
            try:
                date_str = dt[:10].replace("/", "")
                time_str = dt[11:].replace(":", "")
            except:
                pass
        elif "**** P A S S ****" in line:
            result = "PASS"
        elif "**** F A I L ****" in line:
            result = "FAIL"
        elif "**** S T O P ****" in line:
            result = "STOP"

    if not all([mac1, mac2, date_str, time_str, result]):
        print(f"  Skipped: MAC1={mac1}, date={date_str}, time={time_str}, result={result}")
        skipped += 1
        continue

    filename = f"{date_str}_{time_str}_{mac1}_{mac2}_{result}.txt"
    filepath = os.path.join(OUTPUT_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(seg.rstrip() + "\n\n")

    success += 1

print(f"Done! {success} files written, {skipped} skipped → {OUTPUT_DIR}")

from collections import Counter
files = os.listdir(OUTPUT_DIR)
results = Counter(f.split('_')[-1].replace('.txt', '') for f in files)
print(f"Result summary: {dict(results)}")
```

---

## 7. 異常情況與處理方式

| 異常情況 | 原因 | 處理方式 |
|----------|------|----------|
| 輸出顯示 `Skipped: N 筆` | 某筆缺少 MAC1/MAC2/Start/結果其中一項 | 記錄 MAC 與時間，手動查原始 Log 補全 |
| 檔案數量與預期不符 | 原始 Log 有不完整紀錄或編碼問題 | 比對 `grep -c '^MAC1:' Log_All.txt` 與輸出數量差異 |
| UnicodeDecodeError | Log 含非 UTF-8 字元 | 已使用 `errors='replace'`，可忽略；若需完整字元請改 `errors='ignore'` |
| 重複檔名（同 MAC + 同時間） | 極少數情況，同秒內重複測試 | 後寫入的會覆蓋前者；若需保留請在腳本加計數器後綴 |
| STOP 筆數不符合預期 | 機台異常中斷，未能完整記錄 | 人工核對 STOP 檔案與機台操作紀錄 |

---

## 8. 擴充與後續應用建議

### 8.1 未來若 Log 格式有變動

- 若結果標記字串改變（如改為 `[PASS]` / `[FAIL]`），只需修改腳本中對應的 `in` 判斷字串
- 若新增第三個 MAC（MAC3），在解析迴圈中同樣方式補上即可
- 若 `Start:` 時間格式改變，修改 `date_str` / `time_str` 的切片範圍

### 8.2 批次自動化

- 可搭配 `cron` / Task Scheduler，設定每日定時自動執行分拆
- 每次執行前，建議備份前一天的 `split_logs/` 目錄，或改用日期子目錄

### 8.3 FAIL / STOP 快速彙整

執行以下指令可快速列出所有 FAIL 與 STOP 的檔案：

```bash
ls split_logs/ | grep -E 'FAIL|STOP'
```

---

## 附錄：本次執行結果摘要

| 項目 | 數值 |
|------|------|
| 原始檔案 | Log_All.txt（5.4 MB，142,834 行） |
| 分拆總筆數 | 235 筆 |
| PASS | 200 筆（85.1%） |
| FAIL | 26 筆（11.1%） |
| STOP | 9 筆（3.8%） |
| 跳過（異常） | 0 筆 |
| 輸出目錄 | split_logs/ |
| 命名範例 | 20241205_083101_001F7B5F0AC6_001F7B5F0AC7_PASS.txt |
| Log 涵蓋時間 | 2024/12/05 08:31:01 ～ 17:32:42（共約 9 小時） |

---

*— 文件結束 —*

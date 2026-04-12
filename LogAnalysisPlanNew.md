# 產線測試 Log 自動化分拆執行計畫書 (整合版)
*Production Line Test Log Splitting & Analysis Execution Plan*

| 項目 | 內容 |
|------|------|
| 文件版本 | v2.0 (New) |
| 整合日期 | 2026-04-11 |
| 原始檔案 | Log_All.txt |
| 總測試筆數 | 235 筆 (依據樣板數據) |
| 執行環境 | Windows / Linux / macOS (Python 3.6+) |

---

## 1. 專案背景與目標

目前產線測試機台產出的 Log 為連續累加格式（所有產品的測試紀錄皆存於同一個 `Log_All.txt`）。這造成了單一產品履歷查詢、品質追蹤與不良品分析的極大困難。

**本計畫目標：**
- **自動化分拆**：將大檔案依據每一台產品的測試區間（由 `MAC1:` 起始）進行物理分拆。
- **標準化命名**：檔名直接包含日期、時間、雙 MAC 地址與測試結果，達成「不開檔即知結果」。
- **結構化分析**：區分 PASS、FAIL 與 STOP 三種狀態，便於後續數據統計與 QA 追查。

---

## 2. 原始 Log 結構分析

### 2.1 單筆測試紀錄特徵
根據對 `Log_All.txt` 的解析，每一段測試區塊遵循以下邊界定義：

| 特徵點 | 描述 | 提取/判斷邏輯 |
| :--- | :--- | :--- |
| **起始 (Start)** | 行首為 `MAC1:\t` | 作為 Split 的切割基準點。 |
| **識別碼 (IDs)** | `MAC1` 與 `MAC2` | 位於區塊頂部，用於唯一識別產品。 |
| **時間 (Time)** | `Start:\t YYYY/MM/DD HH:MM:SS` | 用於檔名時間戳記。 |
| **結束 (End)** | `End:\t\t YYYY/MM/DD HH:MM:SS` | 標記一筆紀錄的完成。 |
| **結果標記** | 包含特定字列 | 偵測 `PASS`, `FAIL`, 或 `STOP`。 |

### 2.2 相鄰紀錄銜接示意
兩筆測試之間透過空行銜接，第一筆的 `Test Time:` 後緊接著第二筆的 `MAC1:`。
```text
...
End:		2024/12/05 08:32:39
Test Time:  01:38.3

MAC1:	001F7B5F0AD0    <-- 下一筆起始
MAC2:	001F7B5F0AD1
Start:	2024/12/05 08:33:30
...
```

---

## 3. 輸出規範與命名標準

### 3.1 命名規則
分拆後的獨立檔案必須嚴格遵守以下格式：
* **公式**：`$DATE_$TIME_$MAC1_$MAC2_$RESULT.txt`
* **範例**：`20241205_083101_001F7B5F0AC6_001F7B5F0AC7_PASS.txt`

### 3.2 欄位定義
1. `$DATE`：YYYYMMDD (例如 20241205)
2. `$TIME`：HHMMSS (例如 083101)
3. `$MAC1` / `$MAC2`：測試紀錄對應的 MAC 地址
4. `$RESULT`：測試最終狀態 (PASS / FAIL / STOP)

### 3.3 目錄結構
* 腳本執行後將自動建立 `split_logs/` 資料夾。
* 所有結果將獨立存儲於該資料夾中。

---

## 4. 自動化處理腳本 (split_log.py)

此腳本採用 **Lookahead Regex (正向預查)** 技術，確保 `MAC1:` 標籤完整保留在子檔案的起始位置。

```python
import re
import os
from collections import Counter

# --- 設定路徑 ---
INPUT_FILE = "Log_All.txt"   # 原始檔案
OUTPUT_DIR = "split_logs"    # 輸出目錄

def split_log_process():
    # 建立輸出目錄
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not os.path.exists(INPUT_FILE):
        print(f"錯誤: 找不到原始檔案 {INPUT_FILE}")
        return

    print(f"正在讀取檔案: {INPUT_FILE} ...")
    with open(INPUT_FILE, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # 1. 統一換行符號為 LF (\n)，並去除 CR (\r)
    content = content.replace('\r\n', '\n').replace('\r', '\n')

    # 2. 使用 lookahead 切割，保留 MAC1: 於下一個 segment 起始
    # 正規表示式解析：在 MAC1: 出現的位置之前進行切割
    segments = re.split(r'(?=^MAC1:\t)', content, flags=re.MULTILINE)
    
    # 3. 濾除不具效的片段
    segments = [s for s in segments if s.strip().startswith("MAC1:")]
    print(f"偵測到總測試筆數: {len(segments)}")

    success_count = 0
    skipped_count = 0

    for seg in segments:
        lines = seg.strip().split('\n')
        mac1 = mac2 = date_str = time_str = result = None

        # 4. 解析關鍵欄位
        for line in lines:
            line_clean = line.strip()
            if line_clean.startswith("MAC1:"):
                mac1 = line_clean.split("\t")[-1].strip()
            elif line_clean.startswith("MAC2:"):
                mac2 = line_clean.split("\t")[-1].strip()
            elif line_clean.startswith("Start:"):
                dt_raw = line_clean.split("\t")[-1].strip()
                try:
                    # YYYY/MM/DD HH:MM:SS -> YYYYMMDD, HHMMSS
                    date_str = dt_raw[:10].replace("/", "")
                    time_str = dt_raw[11:].replace(":", "")
                except:
                    pass
            # 結果偵測
            if "**** P A S S ****" in line:
                result = "PASS"
            elif "**** F A I L ****" in line:
                result = "FAIL"
            elif "**** S T O P ****" in line:
                result = "STOP"

        # 5. 檔案寫出
        if all([mac1, mac2, date_str, time_str, result]):
            filename = f"{date_str}_{time_str}_{mac1}_{mac2}_{result}.txt"
            filepath = os.path.join(OUTPUT_DIR, filename)
            
            with open(filepath, "w", encoding="utf-8") as out_f:
                # 每個檔案末端保留兩次換行以符規範
                out_f.write(seg.rstrip() + "\n\n")
            success_count += 1
        else:
            print(f"  跳過異常區塊: MAC1={mac1}, Result={result}")
            skipped_count += 1

    print("-" * 30)
    print(f"處理完成！")
    print(f"成功輸出: {success_count} 筆")
    print(f"跳過(資訊不全): {skipped_count} 筆")
    
    # 統計摘要
    if os.path.exists(OUTPUT_DIR):
        files = os.listdir(OUTPUT_DIR)
        summary = Counter(f.split('_')[-1].replace('.txt', '') for f in files)
        print(f"結果分布: {dict(summary)}")

if __name__ == "__main__":
    split_log_process()
```

---

## 5. 異常處理與執行建議

### 5.1 常見異常表
| 異常情況 | 可能原因 | 處理建議 |
| :--- | :--- | :--- |
| **Skipped (跳過)** | 某筆紀錄缺少 `MAC` 或 `Start` 時間。 | 核對原始 Log，確認是否為機台斷電或中斷造成。 |
| **編碼錯誤** | Log 包含非 UTF-8 字元。 | 已在腳本加入 `errors='replace'`，確保程序不中斷。 |
| **檔案數量不符** | 原始 Log 有不完整紀錄。 | 使用 `grep -c '^MAC1:' Log_All.txt` 指令確認原始數量。 |

### 5.2 後續擴充建議
- **排程自動化**：可配合 Windows 工作排程器每日凌晨執行一次。
- **數據分析**：分拆後的檔名格式非常適合直接讀取並匯入 Excel 或 PowerBI 進行趨勢分析。
- **快速篩選**：可利用系統搜尋 `*FAIL.txt` 快速鎖定特定時段的所有故障機台紀錄。

---
*文件結束 - 整合日期 2026/04/11*

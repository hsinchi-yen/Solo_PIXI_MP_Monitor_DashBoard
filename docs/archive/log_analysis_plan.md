# 產線測試 Log 自動化分拆執行計畫書

## 1. 專案背景與目標
目前產線測試機台產出的 Log 為連續累加格式（所有產品的測試紀錄皆存於同一個 `Log_All.txt`）。為了便於後續品質追蹤、單一產品履歷查詢與良率統計，必須將此大檔案依據每一台產品的測試區間進行分拆，並依照指定規範進行命名與匯出。

## 2. 原始檔案結構分析
根據 `Log_All.txt` 的特徵，單一測試區塊的邊界定義如下：
* **起始標誌 (Start Trigger)**：每一段測試皆以 `MAC1:` 標籤作為開頭。
* **結束標誌 (End Trigger)**：以 `End:` 欄位及其後的測試結束時間為結尾，通常伴隨兩個換行符號作為視覺分隔。
* **關鍵資訊提取點**：
    * **MAC1 / MAC2**：位於區塊頂部。
    * **日期時間 (Start Time)**：位於 `Start:` 欄位，用於檔名標記。
    * **測試結果 (Result)**：搜尋區塊內是否包含 `**** P A S S ****` 或 `**** F A I L ****`。

## 3. 檔案命名規範
分拆後的獨立檔案必須嚴格遵守以下格式：
* **命名公式**：`$DATE_$TIME_$MAC1_$MAC2_$RESULT.txt`
* **範例**：`20241205_083101_001F7B5F0AC6_001F7B5F0AC7_PASS.txt`
* **欄位定義**：
    * `$DATE`：YYYYMMDD (例如 20241205)
    * `$TIME`：HHMMSS (例如 083101)
    * `$MAC1` / `$MAC2`：測試紀錄對應的 MAC 地址
    * `$RESULT`：最終結果 (PASS 或 FAIL)

## 4. 執行環境準備
請確保執行環境具備以下條件：
1.  **作業系統**：Windows / macOS / Linux。
2.  **軟體**：已安裝 **Python 3.x**。
3.  **檔案放置**：將原始檔案 `Log_All.txt` 與下述腳本放在同一個資料夾內。

## 5. 自動化處理腳本 (Python)
請將以下程式碼儲存為 `split_log.py`：

```python
import re
import os

def process_logs(input_file):
    output_folder = "Split_Logs_Output"
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    print(f"正在讀取檔案: {input_file} ...")
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 使用 MAC1: 作為切分標誌，並保留該標誌
    records = re.split(r'(?=MAC1:)', content)
    
    count = 0
    for record in records:
        if not record.strip() or "MAC1:" not in record:
            continue
        
        try:
            # 1. 提取 MAC 地址
            m1 = re.search(r'MAC1:\s+([0-9A-F]+)', record)
            m2 = re.search(r'MAC2:\s+([0-9A-F]+)', record)
            mac1 = m1.group(1) if m1 else "NOMAC1"
            mac2 = m2.group(1) if m2 else "NOMAC2"

            # 2. 提取 Start 時間 (格式: 2024/12/05 08:31:01)
            t_m = re.search(r'Start:\s+(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2}):(\d{2})', record)
            if t_m:
                date_str = f"{t_m.group(1)}{t_m.group(2)}{t_m.group(3)}"
                time_str = f"{t_m.group(4)}{t_m.group(5)}{t_m.group(6)}"
            else:
                date_str, time_str = "00000000", "000000"

            # 3. 判斷結果
            if "**** P A S S ****" in record:
                result = "PASS"
            elif "**** F A I L ****" in record:
                result = "FAIL"
            else:
                result = "INCOMPLETE"

            # 4. 寫入檔案
            file_name = f"{date_str}_{time_str}_{mac1}_{mac2}_{result}.txt"
            with open(os.path.join(output_folder, file_name), 'w', encoding='utf-8') as out_f:
                out_f.write(record.strip() + "\n")
            
            count += 1
        except Exception as e:
            print(f"處理區塊時出錯: {e}")

    print(f"處理完成！共匯出 {count} 個獨立 Log 檔案至 {output_folder} 資料夾。")

if __name__ == "__main__":
    process_logs("Log_All.txt")
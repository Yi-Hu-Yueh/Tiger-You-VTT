# Tiger-You-VTT

> YouTube / 本機影片字幕擷取與語音轉字幕工具  
> 支援 YouTube 字幕、自動字幕、內嵌文字字幕，以及無字幕影片的 `faster-whisper` 本機語音轉錄。

chatgpt: https://chatgpt.com/share/6ac07112-ac1c-83ee-910d-5a7d1d747d79

codex: https://chatgpt.com/s/cx_6ac07130c7708191a9b7e540db3563c8

github: https://github.com/Yi-Hu-Yueh/Tiger-You-VTT


[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-local-green)
![ASR](https://img.shields.io/badge/ASR-faster--whisper-orange)

---

## 1. 專案簡介

Tiger-You-VTT 是一個本機執行的影片字幕擷取工具。使用者可以：

- 貼上 **YouTube URL**
- 或直接 **上傳本機影片**

系統會自動判斷最適合的字幕來源，不需要手動選擇處理方式。

### YouTube URL

```text
YouTube URL
    ↓
檢查 YouTube 字幕
    ├─ 有人工字幕 / 自動字幕
    │   → 直接取得字幕
    │
    └─ 沒有可用字幕
        → 只下載音訊
        → faster-whisper
        → 語音轉字幕
```

### 本機影片

```text
上傳影片
    ↓
檢查影片內嵌字幕軌
    ├─ 有可用文字字幕
    │   → 直接擷取字幕
    │
    └─ 沒有可用文字字幕
        → 檢查音訊
        → faster-whisper
        → 語音轉字幕
```

最終統一輸出：

- `segments`：含時間戳的結構化字幕片段
- `VTT`
- `SRT`
- `TXT`

TXT 片段之間使用英文半形逗號 `,`：

```text
句子1,句子2,句子3
```

![對談範例 1](./pics/001.png)

---

## 2. 主要功能

- ✅ YouTube URL 單一入口
- ✅ 自動偵測人工字幕
- ✅ 自動偵測 YouTube 自動字幕
- ✅ 無 YouTube 字幕時自動使用 `faster-whisper`
- ✅ 本機影片上傳
- ✅ 自動擷取影片內嵌文字字幕
- ✅ 無內嵌字幕時自動使用 `faster-whisper`
- ✅ 本機 GPU / CPU 語音辨識
- ✅ NVIDIA GPU CUDA 加速
- ✅ CUDA 失敗時 CPU `int8` fallback
- ✅ Whisper `VAD` 空結果時自動重試一次 `vad_filter=False`
- ✅ 擷取開始 / 結束時間
- ✅ 背景 Job
- ✅ 即時顯示目前已取得的字幕
- ✅ 即時顯示執行所耗時間
- ✅ **停止**功能
- ✅ 停止後保留目前已取得的文字
- ✅ 停止後仍可取得部分 TXT / VTT / SRT
- ✅ FFmpeg / FFprobe 媒體檢查
- ✅ 上傳影片與暫存音訊自動清除
- ✅ Swagger API 文件
- ✅ 本機 Web UI

---

## 3. License

本專案使用 **MIT License**。

```text
MIT License

Copyright (c) 2026 Tiger (樂以虎@Taiwan)
```

完整條款請參閱：

```text
LICENSE
```

---

# 4. 系統需求

## 基本需求

建議環境：

- Windows 10 / Windows 11
- Python **3.11+**
- Git
- FFmpeg / FFprobe
- 至少 8 GB RAM
- 建議 16 GB RAM 以上

本專案開發與驗證環境主要使用：

```text
Python 3.11.3
Windows 11
RAM 16 GB
```

---

## 4.1 FFmpeg / FFprobe

本機影片上傳、媒體檢查與區間擷取會使用：

```text
ffmpeg
ffprobe
```

安裝後確認：

```powershell
ffmpeg -version
ffprobe -version
```

只要兩個指令都可以正常顯示版本即可。

FFmpeg 官方網站：

https://ffmpeg.org/

---

# 5. 從 GitHub 下載

GitHub Repository：

https://github.com/Yi-Hu-Yueh/Tiger-You-VTT

## 方法 A：Git Clone

```powershell
git clone https://github.com/Yi-Hu-Yueh/Tiger-You-VTT.git
cd Tiger-You-VTT
```

## 方法 B：下載 ZIP

GitHub 頁面：

```text
Code
→ Download ZIP
```

解壓縮後進入：

```text
Tiger-You-VTT
```

---

# 6. Python 環境安裝

## 6.1 建立虛擬環境

建議使用獨立 Python virtual environment：

```powershell
python -m venv .venv
```

啟動：

```powershell
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 阻擋啟動腳本，可只對目前視窗暫時允許：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

確認 Python：

```powershell
python --version
```

---

## 6.2 安裝 Python 套件

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

主要 Runtime 套件包含：

- FastAPI
- Uvicorn
- yt-dlp
- webvtt-py
- faster-whisper
- CTranslate2
- PyAV
- python-multipart
- httpx

---

# 7. 沒有 GPU：CPU 安裝與執行

**沒有 NVIDIA GPU 也可以使用。**

建議設定：

```powershell
$env:WHISPER_DEVICE="cpu"
$env:WHISPER_COMPUTE_TYPE="int8"
```

再啟動 Tiger-You-VTT：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

CPU 模式：

```text
faster-whisper
→ CPU
→ int8
```

### CPU 優點

- 不需要 CUDA
- 不需要 NVIDIA GPU
- 安裝最簡單
- 相容性最好

### CPU 缺點

- 無字幕影片需要 ASR 時速度可能很慢
- `large-v3` 對長影片可能耗時較久

> 如果影片本身已有 YouTube 字幕或內嵌文字字幕，系統會直接使用字幕，不會啟動 Whisper，因此 CPU / GPU 差異不大。

---

# 8. 有 NVIDIA GPU：CUDA 加速

如果有相容的 NVIDIA GPU，可使用 CTranslate2 CUDA 加速 `faster-whisper`。

官方 `faster-whisper` GPU 執行環境要求包含：

- **cuBLAS for CUDA 12**
- **cuDNN 9 for CUDA 12**

本專案不要求一定安裝完整 CUDA Toolkit；重點是 CTranslate2 執行時能找到相容的 CUDA Runtime DLL。

## 8.1 自動 GPU 模式

推薦：

```powershell
$env:WHISPER_DEVICE="auto"
$env:WHISPER_COMPUTE_TYPE="auto"
```

Tiger-You-VTT 會嘗試選擇可用 CUDA 模式，若 GPU 執行失敗，仍保留 CPU fallback。

---

## 8.2 GTX 1070 驗證結果

本專案已實機驗證：

```text
GPU                  NVIDIA GeForce GTX 1070 8 GB
faster-whisper       1.2.1
CTranslate2          4.8.2
PyAV                 18.1.0
Whisper model        large-v3
Working device       cuda
Working compute      int8_float32
```

實測 CTranslate2 回報支援：

```text
float32
int8_float32
int8
```

GTX 1070 測試中，`int8_float32` 為最實用的設定。

### 單一短影片實測

同一個短影片的 Whisper inference：

```text
CPU int8             約 121.484 秒
GPU int8_float32     約   5.435 秒
```

該單次樣本中 GPU inference 約快：

```text
22.35x
```

此數據只代表該測試樣本，不保證所有影片都有相同比例。

---

## 8.3 `cublas64_12.dll` 找不到

若看到：

```text
RuntimeError: Library cublas64_12.dll is not found or cannot be loaded
```

代表 GPU 已被辨識，但 CTranslate2 找不到需要的 CUDA Runtime DLL。

Tiger-You-VTT 支援：

```text
WHISPER_CUDA_RUNTIME_DIR
```

例如，若相容 DLL 已存在某個資料夾：

```powershell
$env:WHISPER_CUDA_RUNTIME_DIR="D:\path\to\cuda-runtime-dlls"
```

在本專案的已驗證開發環境中，所需 CUDA 12 cuBLAS DLL 位於現有 Python 環境的：

```text
...\Lib\site-packages\torch\lib
```

其中包含：

```text
cublas64_12.dll
cublasLt64_12.dll
cudart64_12.dll
```

專案會優先使用設定的 `WHISPER_CUDA_RUNTIME_DIR`，或在適用時發現目前環境中的 `torch\lib`。

> 不建議為了排錯就隨意升降 CTranslate2 / faster-whisper、重裝 NVIDIA Driver 或安裝完整 CUDA Toolkit。先確認實際錯誤與 DLL 搜尋路徑。

---

# 9. 啟動系統

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

成功後：

```text
Uvicorn running on http://127.0.0.1:8000
```

若 8000 已被使用，可以改成：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

---

# 10. 開啟 UI

預設：

```text
http://127.0.0.1:8000/
```

如果使用 8001：

```text
http://127.0.0.1:8001/
```

Swagger：

```text
http://127.0.0.1:8000/docs
```

---

# 11. UI 操作說明

Tiger-You-VTT UI 提供兩種來源：

```text
YouTube URL
本機影片上傳
```

後端會自動決定使用現有字幕或 Whisper。

---

## 11.1 YouTube URL

選擇 YouTube 模式後，在 URL 欄位貼入：

```text
https://www.youtube.com/watch?v=xxxxxxxxxxx
```

不需要自己選：

- 人工字幕
- 自動字幕
- Whisper

系統自動判斷：

```text
人工字幕
    ↓ 沒有
自動字幕
    ↓ 沒有
faster-whisper
```

---

## 11.2 上傳影片

選擇影片上傳模式：

```text
Choose File
```

支援的常見副檔名包括：

```text
.mp4
.mov
.mkv
.avi
.webm
.flv
.wmv
.mpeg
.mpg
```

系統自動：

```text
檢查 Embedded Text Subtitle
        ↓ 有
      直接取字幕

        ↓ 沒有
檢查 Audio
        ↓ 有
faster-whisper
```

不需要手動指定處理模式。

---

# 12. 擷取開始 / 結束時間

UI 有：

```text
擷取開始時間: 小時:分鐘
擷取結束時間: 小時:分鐘
```

格式：

```text
H:M
```

代表：

```text
小時:分鐘
```

不是：

```text
分鐘:秒
```

## 範例

```text
0:3
```

表示：

```text
0 小時 3 分鐘
```

```text
2:25
```

表示：

```text
2 小時 25 分鐘
```

---

## 12.1 UI 預設值

預設：

```text
擷取開始時間: 0:0
擷取結束時間: 0:10
```

因此在一般情況下：

```text
0:0 → 0:10
```

代表擷取：

```text
影片開始 → 第 10 分鐘
```

---

## 12.2 短影片

如果影片只有 2 分鐘，而 UI 保持原始預設：

```text
0:0
0:10
```

系統會知道 `0:10` 是尚未修改的 UI 預設值，並自動使用：

```text
0:0 → 實際影片結束
```

因此短影片不會因預設 10 分鐘而發生 `invalid_time_range`。

但是如果使用者**自己明確輸入**超過影片長度的時間，仍會回傳範圍錯誤。

---

## 12.3 清空時間欄位

如果手動清空：

### 開始時間空白

```text
從影片最前面開始
```

### 結束時間空白

```text
一直擷取到影片結束
```

### 兩個都空白

```text
擷取完整影片
```

---

# 13. 開始取得字幕

設定來源與時間後，按：

```text
開始取得字幕
```

背景 Job 會啟動。

UI 會持續更新：

- Job 狀態
- 已取得 segment 數量
- 目前字幕文字
- 執行所耗時間

---

# 14. 執行所耗時間

UI 顯示：

```text
執行所耗時間
```

格式：

```text
HH:MM:SS
```

例如：

```text
00:00:07
00:05:12
01:03:09
27:00:00
```

計時從 Job 建立開始，包括：

```text
queued
running
stopping
```

Job 進入以下狀態後時間會固定：

```text
completed
stopped
failed
```

---

# 15. 停止功能

Whisper 執行期間可以按：

```text
停止
```

狀態可能變成：

```text
running
→ stopping
→ stopped
```

## 重要

STOP 是 **cooperative cancellation**。

也就是：

> 停止會在目前安全的字幕片段完成後生效。

因此不是強制瞬間殺掉 Whisper process。

例如按下 STOP 時已有 4 段字幕：

```text
segment 1
segment 2
segment 3
segment 4
```

如果第 5 段正在完成，最終可能保留：

```text
segment 1
segment 2
segment 3
segment 4
segment 5
```

然後停止。

這是正常設計。

---

## 15.1 停止後文字不會消失

按停止後：

- 已取得的 `segments` 保留
- TXT 保留
- VTT 保留
- SRT 保留
- 執行所耗時間保留
- 不會清空目前文字

因此可以提早結束長影片轉錄，仍然取得目前為止的內容。

---

# 16. 輸出格式

## TXT

純文字輸出以英文半形逗號連接各 segment：

```text
第一句,第二句,第三句
```

TXT 不包含時間碼。

---

## VTT

例如：

```text
WEBVTT

00:01:00.000 --> 00:01:03.200
第一句
```

包含原始影片時間軸。

---

## SRT

例如：

```text
1
00:01:00,000 --> 00:01:03,200
第一句
```

---

## TranscriptSegment

API 內部標準格式：

```json
{
  "start": 60.0,
  "end": 63.2,
  "text": "第一句"
}
```

---

# 17. 下載結果

Job 完成或停止後，UI 可下載：

- TXT
- VTT
- SRT

即使按了停止，只要已有完成的字幕片段，也可以下載**目前為止**的結果。

---

# 18. 有字幕與無字幕影片的差別

## 有現成字幕

例如 YouTube 本身已有人工字幕：

```text
YouTube
→ VTT
→ parser
→ TXT / VTT / SRT
```

這種情況：

- 不啟動 Whisper
- 不需要 GPU
- 通常非常快

---

## 沒有字幕

```text
影片
→ 音訊
→ faster-whisper
→ TranscriptSegment[]
→ TXT / VTT / SRT
```

這種情況才會使用：

```text
CPU
或
NVIDIA GPU
```

所以 **GPU 加速只會明顯影響 Whisper 路徑**。

---

# 19. API

Swagger：

```text
http://127.0.0.1:8000/docs
```

主要 API：

| Method | Endpoint | 用途 |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/youtube/info` | 取得 YouTube 影片 / 字幕資訊 |
| POST | `/api/youtube/subtitle` | 同步取得 YouTube 字幕 |
| POST | `/api/video/subtitle` | 同步處理上傳影片 |
| POST | `/api/jobs/youtube` | 建立 YouTube 背景 Job |
| POST | `/api/jobs/video` | 建立影片上傳背景 Job |
| GET | `/api/jobs/{job_id}` | 查詢 Job / partial transcript |
| POST | `/api/jobs/{job_id}/stop` | 停止 Job |

---

# 20. API 範例

## YouTube 同步 API

```bash
curl -X POST \
  "http://127.0.0.1:8000/api/youtube/subtitle" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.youtube.com/watch?v=VIDEO_ID",
    "start_time": "0:0",
    "end_time": "0:10"
  }'
```

---

## 上傳影片同步 API

```bash
curl -X POST \
  "http://127.0.0.1:8000/api/video/subtitle" \
  -F "file=@example.mp4" \
  -F "start_time=0:0" \
  -F "end_time=0:10"
```

---

# 21. Job 狀態

背景工作可能有：

```text
queued
running
stopping
stopped
completed
failed
```

Job 只存在目前 Server process 的記憶體內。

重啟 Uvicorn 後，舊 Job 不會保留。

---

# 22. Whisper 設定

常用環境變數：

```text
WHISPER_MODEL
WHISPER_DEVICE
WHISPER_COMPUTE_TYPE
WHISPER_CUDA_RUNTIME_DIR
```

預設模型：

```text
large-v3
```

推薦一般設定：

```powershell
$env:WHISPER_DEVICE="auto"
$env:WHISPER_COMPUTE_TYPE="auto"
```

強制 CPU：

```powershell
$env:WHISPER_DEVICE="cpu"
$env:WHISPER_COMPUTE_TYPE="int8"
```

---

# 23. 模型下載

第一次真正需要 Whisper 時，`faster-whisper` 可能需要下載：

```text
large-v3
```

因此第一次無字幕影片處理可能需要：

- 網路連線
- 較長等待時間
- 足夠的磁碟空間

模型使用快取後，後續不需要每次重新下載。

如果影片已有可用字幕，Whisper 是 lazy-load，不會因為啟動 Tiger-You-VTT 就自動載入模型。

---

# 24. 常見問題

## 24.1 Port 8000 已被占用

錯誤：

```text
[Errno 10048]
only one usage of each socket address...
```

查詢：

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

或改用：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

---

## 24.2 YouTube 看得到字幕，但系統說沒有字幕

影片上的文字可能是：

```text
burned-in subtitle
```

也就是已經直接燒進畫面，不是真正的 YouTube subtitle track。

此時 Tiger-You-VTT 會：

```text
無可下載字幕
→ audio
→ faster-whisper
```

目前不使用 OCR。

---

## 24.3 Whisper 空轉錄

系統目前：

```text
第一次
vad_filter=True

若自然產生 0 usable segments
→ 自動再試一次
vad_filter=False
```

如果停止操作造成 0 segment，則**不會**誤觸發這個 retry。

---

## 24.4 GPU 失敗

若 GPU 無法完成實際 CUDA inference：

```text
CUDA
→ failure
→ CPU int8 fallback
```

因此 GPU 問題不應阻止基本轉錄功能。

---

## 24.5 為什麼 GPU 被偵測到仍失敗？

「GPU 被偵測到」不代表 CTranslate2 已能載入所有 CUDA Runtime DLL。

若出現：

```text
cublas64_12.dll is not found or cannot be loaded
```

請確認：

```text
WHISPER_CUDA_RUNTIME_DIR
```

是否指向含相容 CUDA 12 runtime DLL 的資料夾。

---

# 25. 專案目錄結構

概念結構：

```text
Tiger-You-VTT/
│
├─ app/
│  ├─ main.py
│  ├─ config.py
│  │
│  ├─ routers/
│  │  ├─ youtube.py
│  │  ├─ video.py
│  │  ├─ jobs.py
│  │  └─ ui.py
│  │
│  ├─ schemas/
│  │  ├─ youtube.py
│  │  ├─ video.py
│  │  ├─ transcript.py
│  │  └─ jobs.py
│  │
│  ├─ services/
│  │  ├─ youtube.py
│  │  ├─ video.py
│  │  ├─ whisper.py
│  │  ├─ transcript.py
│  │  ├─ media_range.py
│  │  ├─ jobs.py
│  │  └─ errors.py
│  │
│  └─ static/
│     └─ index.html
│
├─ tests/
├─ requirements.txt
├─ requirements-dev.txt
├─ LICENSE
└─ README.md
```

---

# 26. 執行測試

安裝開發依賴：

```powershell
python -m pip install -r requirements-dev.txt
```

執行：

```powershell
python -m pytest -q
```

目前本機開發版本已建立完整測試，涵蓋：

- YouTube subtitle
- YouTube Whisper fallback
- Embedded subtitle
- Uploaded video
- FFmpeg / FFprobe
- Whisper CPU/GPU
- CUDA fallback
- VAD retry
- H:M range
- timestamp offset
- STOP
- partial transcript
- Job lifecycle
- elapsed timer
- UI

---

# 27. 安全與隱私

- Whisper 推論在本機執行
- 不使用雲端 ASR API
- 不使用 Qwen / OpenAI / Ollama / LangChain 作字幕分析
- 上傳影片使用暫存路徑
- 處理結束後清除 temporary media
- 不使用 `shell=True`
- 上傳檔名不直接作為任意 filesystem path
- 使用上傳大小限制
- YouTube URL 路徑仍需要連線到 YouTube
- 首次下載 Whisper 模型時需要網路

---

# 28. 已知限制

1. STOP 是 cooperative cancellation，不保證瞬間停止正在運算中的單一 Whisper segment。
2. Job 儲存在記憶體中，Server 重啟後不保留。
3. 畫面燒錄字幕目前不做 OCR。
4. Whisper ASR 準確率會受到語言、背景音、音量與錄音品質影響。
5. `large-v3` 在 CPU 上可能很慢。
6. NVIDIA GPU 加速依賴本機 CUDA runtime 相容性。
7. GPU acceleration 並不影響已存在字幕的快速路徑。
8. H:M 輸入只接受「小時:分鐘」，不接受秒欄位。

---

# 29. 建議操作流程

最簡單的日常使用方式：

```text
1. 啟動 Server
2. 開啟 http://127.0.0.1:8000/
3. 選擇 YouTube 或上傳影片
4. 確認擷取開始 / 結束時間
5. 按「開始取得字幕」
6. 觀察即時字幕與執行所耗時間
7. 若不想繼續，按「停止」
8. 已取得文字仍會保留
9. 下載 TXT / VTT / SRT
```

---

# 30. MIT License

本專案依 MIT License 發布。

你可以自由：

- 使用
- 複製
- 修改
- 合併
- 發布
- 散布
- 再授權
- 銷售軟體副本

前提是保留原始版權與授權聲明。

詳見：

[LICENSE](LICENSE)

---

## Repository

**Tiger-You-VTT**

https://github.com/Yi-Hu-Yueh/Tiger-You-VTT

---

Copyright © 2026 Tiger (樂以虎@Taiwan)

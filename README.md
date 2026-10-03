# Tiger-You-VTT

> YouTube / 本機影片 / 語音檔 / Windows 系統播放聲音的字幕擷取與語音轉文字工具  
> 支援 YouTube 字幕、自動字幕、內嵌文字字幕、`faster-whisper` 本機 ASR，以及 Windows WASAPI Loopback 即時系統音訊轉錄。

chatgpt: https://chatgpt.com/share/6ac07112-ac1c-83ee-910d-5a7d1d747d79

codex: https://chatgpt.com/s/cx_6ac07130c7708191a9b7e540db3563c8

github: https://github.com/Yi-Hu-Yueh/Tiger-You-VTT

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-local-green)
![ASR](https://img.shields.io/badge/ASR-faster--whisper-orange)
![CUDA](https://img.shields.io/badge/CUDA-GTX%201070-success)
![Windows Audio](https://img.shields.io/badge/Windows-WASAPI%20Loopback-blue)

---

## 1. 專案簡介

Tiger-You-VTT 是一個以 **本機處理為主** 的字幕擷取與語音轉文字工具。

目前支援四種來源：

1. **YouTube URL**
2. **上傳影片**
3. **上傳語音檔**
4. **電腦播放聲音（Windows WASAPI Loopback）**

後端會自動選擇適合的處理方式；使用者不需要自己決定要用字幕軌、Whisper、GPU 或 CPU。

### 1.1 YouTube URL

```text
YouTube URL
    ↓
檢查 YouTube 字幕
    ├─ 有人工字幕 / 自動字幕
    │   → 直接取得字幕
    │
    └─ 沒有可用字幕
        → 下載 audio-only
        → faster-whisper
        → 語音轉文字
```

### 1.2 本機影片

```text
上傳影片
    ↓
FFprobe 檢查媒體 streams
    ↓
有 Embedded Text Subtitle？
    ├─ 有
    │   → FFmpeg 擷取文字字幕
    │
    └─ 沒有
        → 檢查 audio
        → faster-whisper
```

### 1.3 本機語音檔

```text
上傳語音檔
    ↓
FFprobe 驗證
    ↓
faster-whisper
    ↓
TranscriptSegment[]
    ↓
TXT / VTT / SRT
```

### 1.4 Windows 電腦播放聲音

```text
Chrome / Edge / VLC / PotPlayer / Spotify / 其他程式
                    ↓
          Windows 音訊輸出裝置
                    ↓
              WASAPI Loopback
                    ↓
            PCM Audio Chunk
                    ↓
             faster-whisper
                    ↓
             即時累積字幕
```

> 此模式擷取的是指定 Windows 輸出裝置的**混合聲音**，不是只擷取某一個 Chrome 分頁或單一應用程式。

所有有限長度媒體來源最終都統一成：

- `segments`：含時間戳的 `TranscriptSegment[]`
- `VTT`
- `SRT`
- `TXT`

TXT 片段使用英文半形逗號 `,` 串接：

```text
句子1,句子2,句子3
```

![對談範例 1](./pics/001.png)

![對談範例 1](./pics/002.png)

---

## 2. 主要功能

- ✅ YouTube URL 單一入口
- ✅ 自動偵測人工字幕
- ✅ 自動偵測 YouTube 自動字幕
- ✅ 無 YouTube 字幕時自動使用 `faster-whisper`
- ✅ 本機影片上傳
- ✅ 自動擷取影片內嵌文字字幕
- ✅ 無內嵌字幕時自動使用 `faster-whisper`
- ✅ 本機語音檔上傳轉文字
- ✅ Windows WASAPI Loopback 電腦播放聲音即時轉文字
- ✅ 本機 GPU / CPU 語音辨識
- ✅ NVIDIA GPU CUDA 加速
- ✅ GTX 1070 + `cuda/int8_float32` 實機驗證
- ✅ CUDA 失敗時 CPU `int8` fallback
- ✅ Whisper VAD 空結果時可安全重試
- ✅ Live system-audio 靜音 chunk 不做 VAD-off hallucination retry
- ✅ 擷取開始 / 結束時間（H:M）
- ✅ 背景 Job
- ✅ 即時顯示目前已取得字幕
- ✅ 即時顯示執行所耗時間
- ✅ **停止**功能
- ✅ 停止後保留目前已取得文字
- ✅ 停止後仍可下載部分 TXT / VTT / SRT
- ✅ FFmpeg / FFprobe 媒體檢查
- ✅ 上傳媒體與暫存檔自動清除
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

## 4.1 基本需求

建議：

- Windows 10 / Windows 11
- Python **3.11+**
- Git
- FFmpeg / FFprobe
- 至少 8 GB RAM
- 建議 16 GB RAM 以上

主要開發與驗證環境：

```text
Python 3.11.3
Windows 11
RAM 16 GB
GPU NVIDIA GeForce GTX 1070 8 GB
```

> `電腦播放聲音` 模式使用 Windows WASAPI Loopback，因此此功能為 Windows 專用。

---

## 4.2 FFmpeg / FFprobe

影片、音訊、內嵌字幕與區間擷取會使用：

```text
ffmpeg
ffprobe
```

確認：

```powershell
ffmpeg -version
ffprobe -version
```

FFmpeg：

https://ffmpeg.org/

---

# 5. 從 GitHub 下載

GitHub：

https://github.com/Yi-Hu-Yueh/Tiger-You-VTT

## 方法 A：Git Clone

```powershell
git clone https://github.com/Yi-Hu-Yueh/Tiger-You-VTT.git
cd Tiger-You-VTT
```

## 方法 B：Download ZIP

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

```powershell
python -m venv .venv
```

啟動：

```powershell
.\.venv\Scripts\Activate.ps1
```

若 PowerShell 阻擋：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

確認：

```powershell
python --version
```

---

## 6.2 安裝套件

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
- **PyAudioWPatch**（Windows WASAPI Loopback）

---

# 7. 沒有 GPU：CPU 模式

沒有 NVIDIA GPU 也可以使用。

```powershell
$env:WHISPER_DEVICE="cpu"
$env:WHISPER_COMPUTE_TYPE="int8"
```

啟動：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

CPU 路徑：

```text
faster-whisper
→ CPU
→ int8
```

### 優點

- 不需要 CUDA
- 不需要 NVIDIA GPU
- 安裝簡單
- 相容性高

### 缺點

- `large-v3` 對長音訊可能很慢

> 如果來源本身已有可用字幕，系統直接使用字幕，不啟動 Whisper，所以 CPU/GPU 差異不大。

---

# 8. NVIDIA GPU / CUDA 加速

如果有相容 NVIDIA GPU，可使用 CTranslate2 CUDA。

`faster-whisper` GPU 執行環境通常需要相容的：

- cuBLAS for CUDA 12
- cuDNN 9 for CUDA 12

本專案不要求一定安裝完整 CUDA Toolkit；核心要求是 CTranslate2 執行時能載入相容 Runtime DLL。

## 8.1 自動模式

```powershell
$env:WHISPER_DEVICE="auto"
$env:WHISPER_COMPUTE_TYPE="auto"
```

若 GPU inference 發生錯誤，仍保留 CPU fallback。

---

## 8.2 GTX 1070 實測

```text
GPU                  NVIDIA GeForce GTX 1070 8 GB
faster-whisper       1.2.1
CTranslate2          4.8.2
PyAV                 18.1.0
Whisper model        large-v3
Working device       cuda
Working compute      int8_float32
```

CTranslate2 實測可用 compute type：

```text
float32
int8_float32
int8
```

GTX 1070 上 `int8_float32` 為目前實測最合適模式。

### 短樣本實測

```text
CPU int8             約 121.484 秒
GPU int8_float32     約   5.435 秒
```

該單次短樣本純 inference 約：

```text
22.35x faster
```

> 此結果只代表該測試，不保證所有媒體都有相同倍率。

---

## 8.3 `cublas64_12.dll` 找不到

若出現：

```text
RuntimeError: Library cublas64_12.dll is not found or cannot be loaded
```

可設定：

```text
WHISPER_CUDA_RUNTIME_DIR
```

例如：

```powershell
$env:WHISPER_CUDA_RUNTIME_DIR="D:\path\to\cuda-runtime-dlls"
```

已驗證開發環境中，相容 DLL 位於：

```text
...\Lib\site-packages\torch\lib
```

包含：

```text
cublas64_12.dll
cublasLt64_12.dll
cudart64_12.dll
```

專案支援 process-local DLL search path，不需要因此修改全域 Windows 設定。

---

# 9. 啟動系統

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

成功：

```text
Uvicorn running on http://127.0.0.1:8000
```

如果 `8000` 已被占用：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

---

# 10. 開啟 UI

預設：

```text
http://127.0.0.1:8000/
```

Swagger：

```text
http://127.0.0.1:8000/docs
```

---

# 11. UI 操作說明

UI 現在有四種來源：

```text
○ YouTube 網址
○ 上傳影片
○ 上傳語音檔
○ 電腦播放聲音
```

---

## 11.1 YouTube 網址

貼入：

```text
https://www.youtube.com/watch?v=xxxxxxxxxxx
```

系統自動：

```text
人工字幕
    ↓ 沒有
自動字幕
    ↓ 沒有
audio-only
    ↓
faster-whisper
```

不需要自己選字幕類型或 Whisper。

---

## 11.2 上傳影片

常見支援副檔名：

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

處理：

```text
FFprobe
↓
Embedded Text Subtitle？
├─ 有 → 直接擷取
└─ 無 → audio → faster-whisper
```

---

## 11.3 上傳語音檔

支援常見格式：

```text
.mp3
.wav
.m4a
.aac
.flac
.ogg
.opus
.wma
```

處理：

```text
Upload
→ FFprobe 驗證
→ faster-whisper
→ TXT / VTT / SRT
```

語言由 Whisper 自動偵測。

---

## 11.4 電腦播放聲音

選擇：

```text
電腦播放聲音
```

UI 會列出 Windows WASAPI Loopback 輸出裝置，例如：

```text
喇叭 (Realtek High Definition Audio) [Loopback]
```

流程：

```text
選擇音訊輸出裝置
→ 開始取得字幕
→ 播放瀏覽器 / VLC / PotPlayer / Spotify 等聲音
→ 系統每個 bounded chunk 送至 faster-whisper
→ UI 持續顯示目前字幕
```

### 注意

- 此模式不使用麥克風。
- 擷取的是該 Windows 輸出裝置的混合聲音。
- 同一輸出裝置上若多個程式同時發聲，可能一起被轉錄。
- 目前不支援只抓某一個應用程式或瀏覽器分頁。
- Live 模式不使用 H:M 開始/結束區間。

---

# 12. 擷取開始 / 結束時間

有限長度來源（YouTube、影片、語音檔）使用：

```text
擷取開始時間: 小時:分鐘
擷取結束時間: 小時:分鐘
```

格式：

```text
H:M
```

也就是：

```text
小時:分鐘
```

不是分鐘:秒。

### 範例

```text
0:3
```

= 0 小時 3 分鐘。

```text
2:25
```

= 2 小時 25 分鐘。

---

## 12.1 UI 預設

```text
擷取開始時間: 0:0
擷取結束時間: 0:10
```

預設代表：

```text
影片 / 音訊開始 → 第 10 分鐘
```

如果來源短於 10 分鐘，而且 `0:10` 仍是未修改的預設值，系統會使用實際媒體結尾，不會因此報錯。

使用者若自行明確輸入超過媒體長度的值，仍會收到 range validation error。

---

## 12.2 清空欄位

開始空白：

```text
從頭開始
```

結束空白：

```text
到媒體結束
```

兩者空白：

```text
擷取全部
```

---

## 12.3 電腦播放聲音模式

`電腦播放聲音` 是 Live source。

因此：

```text
擷取開始時間
擷取結束時間
```

會隱藏或停用。

開始 = 從現在開始擷取。

停止 = 結束此次 Live capture。

---

# 13. 開始取得字幕

按：

```text
開始取得字幕
```

背景 Job 啟動後，UI 會更新：

- Job 狀態
- segment 數量
- 目前文字
- 執行所耗時間

---

# 14. 執行所耗時間

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

計時包括：

```text
queued
running
stopping
```

以下狀態會停止計時：

```text
completed
stopped
failed
```

---

# 15. 停止功能

Whisper Job 執行時可按：

```text
停止
```

狀態：

```text
running
→ stopping
→ stopped
```

STOP 使用 **cooperative cancellation**：

> 停止會在目前安全的字幕片段完成後生效。

因此不是直接殺掉 Python / Whisper process。

已經完成的字幕片段會保留。

---

## 15.1 停止後不丟文字

停止後保留：

- `segments`
- TXT
- VTT
- SRT
- 執行所耗時間

例如 STOP 前已有 4 個 segment，但第 5 個正在安全完成，最後保留 5 個是正常行為。

---

# 16. 電腦播放聲音的 Live Chunk

System Audio 預設：

```text
SYSTEM_AUDIO_CHUNK_SECONDS = 10
```

也就是概念上：

```text
錄製約 10 秒
→ Whisper
→ 顯示文字
→ 下一個 chunk
```

Live 字幕延遲約由：

```text
chunk capture 時間
+
Whisper inference 時間
```

構成。

因此這不是逐字零延遲字幕。

第一次使用 `large-v3` 還包含模型載入成本，第一段可能明顯較慢。

---

## 16.1 靜音處理

System Audio 的安靜 chunk 不應被視為錯誤。

明顯靜音：

```text
→ 不增加字幕
→ Job 繼續 running
```

Live chunk 若 VAD 得不到語音：

```text
→ 接受 0 segments
→ 不執行 file-mode 的 VAD-off retry
```

這可降低靜音時產生 hallucinated transcript 的風險。

---

# 17. 輸出格式

## TXT

```text
第一句,第二句,第三句
```

不含時間碼。

## VTT

```text
WEBVTT

00:01:00.000 --> 00:01:03.200
第一句
```

## SRT

```text
1
00:01:00,000 --> 00:01:03,200
第一句
```

## TranscriptSegment

```json
{
  "start": 60.0,
  "end": 63.2,
  "text": "第一句"
}
```

System Audio 使用的是**此次 capture session 的相對時間軸**，不是 Windows 真實時鐘時間。

---

# 18. 下載結果

Job 完成或停止後可下載：

- TXT
- VTT
- SRT

STOP 後只要已有 completed segments，也能下載目前為止的結果。

---

# 19. 有字幕與無字幕來源

## 有現成文字字幕

```text
字幕 track
→ parser
→ TXT / VTT / SRT
```

不啟動 Whisper，因此通常非常快。

## 無字幕但有音訊

```text
audio
→ faster-whisper
→ TranscriptSegment[]
→ TXT / VTT / SRT
```

才會使用 CPU / GPU。

---

# 20. API

Swagger：

```text
http://127.0.0.1:8000/docs
```

主要 API：

| Method | Endpoint | 用途 |
|---|---|---|
| GET | `/health` | 服務健康檢查 |
| GET | `/` | 本機 Web UI |
| POST | `/api/youtube/info` | 取得 YouTube metadata / subtitle tracks |
| POST | `/api/youtube/subtitle` | 同步取得 YouTube 字幕 / Whisper fallback |
| POST | `/api/video/subtitle` | 同步處理上傳影片 |
| POST | `/api/audio/transcript` | 同步轉錄上傳語音檔 |
| POST | `/api/jobs/youtube` | 建立 YouTube Background Job |
| POST | `/api/jobs/video` | 建立影片 Background Job |
| POST | `/api/jobs/audio` | 建立語音檔 Background Job |
| GET | `/api/system-audio/devices` | 取得 WASAPI Loopback 裝置 |
| POST | `/api/jobs/system-audio` | 建立 Windows 系統音訊 Live Job |
| GET | `/api/jobs/{job_id}` | Job status / partial transcript / elapsed |
| POST | `/api/jobs/{job_id}/stop` | Cooperative STOP |

---

# 21. API 範例

## 21.1 YouTube

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

## 21.2 上傳影片

```bash
curl -X POST \
  "http://127.0.0.1:8000/api/video/subtitle" \
  -F "file=@example.mp4" \
  -F "start_time=0:0" \
  -F "end_time=0:10"
```

## 21.3 上傳語音檔

```bash
curl -X POST \
  "http://127.0.0.1:8000/api/audio/transcript" \
  -F "file=@speech.mp3" \
  -F "start_time=0:0" \
  -F "end_time=0:10"
```

## 21.4 系統音訊裝置

```bash
curl "http://127.0.0.1:8000/api/system-audio/devices"
```

## 21.5 開始系統音訊 Live Job

```bash
curl -X POST \
  "http://127.0.0.1:8000/api/jobs/system-audio" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 5
  }'
```

---

# 22. Job 狀態

```text
queued
running
stopping
stopped
completed
failed
```

Job 為 in-memory。

重啟 Uvicorn 後舊 Job 不保留。

---

# 23. Whisper 設定

常用環境變數：

```text
WHISPER_MODEL
WHISPER_DEVICE
WHISPER_COMPUTE_TYPE
WHISPER_CUDA_RUNTIME_DIR
SYSTEM_AUDIO_CHUNK_SECONDS
```

預設 Whisper：

```text
large-v3
```

推薦：

```powershell
$env:WHISPER_DEVICE="auto"
$env:WHISPER_COMPUTE_TYPE="auto"
```

CPU：

```powershell
$env:WHISPER_DEVICE="cpu"
$env:WHISPER_COMPUTE_TYPE="int8"
```

---

# 24. Whisper 模型下載

第一次真正需要 ASR 時，`faster-whisper` 可能下載 `large-v3`。

第一次執行可能需要：

- 網路
- 磁碟空間
- 額外等待時間

模型成功快取後，不需要每次下載。

字幕 fast path 不會在 FastAPI startup 時預載 Whisper。

---

# 25. Windows WASAPI Loopback

System Audio 使用：

```text
PyAudioWPatch
```

取得 Windows WASAPI Loopback capture device。

已驗證範例：

```text
喇叭 (Realtek High Definition Audio) [Loopback]
sample rate: 48000 Hz
channels: 2
```

此清單只應包含可用 loopback device，不包含麥克風。

---

# 26. 常見問題

## 26.1 Port 8000 已被占用

```text
[Errno 10048]
only one usage of each socket address...
```

查：

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

或：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

---

## 26.2 YouTube 畫面有字幕，但沒有字幕軌

可能是：

```text
burned-in subtitle
```

此時：

```text
無可下載 subtitle track
→ audio
→ faster-whisper
```

目前不做 OCR。

---

## 26.3 Whisper 空轉錄

檔案模式：

```text
vad_filter=True
→ 自然得到 0 usable segments
→ 再試一次 vad_filter=False
```

如果 STOP 導致 0 segment，不進行 retry。

System Audio Live chunk 則不使用此 VAD-off retry，避免靜音 hallucination。

---

## 26.4 GPU 失敗

```text
CUDA
→ inference failure
→ CPU int8 fallback
```

基本轉錄能力仍保留。

---

## 26.5 `cublas64_12.dll` 錯誤

```text
RuntimeError: Library cublas64_12.dll is not found or cannot be loaded
```

檢查：

```text
WHISPER_CUDA_RUNTIME_DIR
```

專案可透過 process-local DLL path 使用相容 runtime，不需要直接污染全域系統設定。

---

## 26.6 電腦播放聲音沒有字幕

依序確認：

1. Windows 正在使用的輸出裝置是否與 UI 選取裝置相同
2. 是否真的有聲音播放
3. 裝置是否為 `[Loopback]`
4. 是否切換了 Bluetooth / Speakers / Headphones
5. Job 是否仍為 `running`

System Audio 不會自動跟隨執行中途切換的 Windows output device。

---

# 27. 專案目錄結構

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
│  │  ├─ audio.py
│  │  ├─ system_audio.py
│  │  ├─ jobs.py
│  │  └─ ui.py
│  │
│  ├─ schemas/
│  │  ├─ youtube.py
│  │  ├─ video.py
│  │  ├─ audio.py
│  │  ├─ system_audio.py
│  │  ├─ transcript.py
│  │  └─ jobs.py
│  │
│  ├─ services/
│  │  ├─ youtube.py
│  │  ├─ video.py
│  │  ├─ audio.py
│  │  ├─ system_audio.py
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
├─ pics/
├─ requirements.txt
├─ requirements-dev.txt
├─ LICENSE
└─ README.md
```

---

# 28. 執行測試

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

目前最新已驗證本機版本：

```text
246 passed
0 failed
0 skipped
```

測試涵蓋：

- YouTube subtitle
- YouTube Whisper fallback
- Embedded subtitle
- Uploaded video
- Uploaded audio
- WASAPI device enumeration
- System Audio capture
- FFmpeg / FFprobe
- Whisper CPU / GPU
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

# 29. 安全與隱私

- Whisper 推論在本機
- Windows system-audio capture 在本機
- 不使用雲端 ASR API
- 不使用 Qwen / OpenAI / Ollama / LangChain 做字幕分析
- 上傳媒體使用暫存路徑
- Live PCM chunk 處理後刪除
- 不保存整段系統音訊錄音
- 不使用 `shell=True`
- 上傳檔名不直接作任意 filesystem path
- 有 upload size 限制
- YouTube 路徑仍需要連網
- 首次取得 Whisper 模型需要網路

---

# 30. 已知限制

1. STOP 是 cooperative cancellation，不保證瞬間中斷正在推論中的 segment。
2. Job 儲存在記憶體，Server 重啟後不保留。
3. Burned-in visual subtitle 不做 OCR。
4. Whisper 準確率受語言、音量、背景音與錄音品質影響。
5. `large-v3` CPU 模式可能很慢。
6. NVIDIA GPU 加速依賴 CUDA runtime 相容性。
7. GPU 不影響已有字幕的 fast path。
8. H:M 只接受「小時:分鐘」，不接受秒欄位。
9. System Audio 只支援 Windows WASAPI Loopback。
10. System Audio 擷取同一輸出裝置上的混合聲音，不隔離單一應用程式。
11. System Audio latency = chunk capture + Whisper inference。
12. System Audio Job 執行中切換 Windows output device 不會自動跟隨。
13. 第一個 Whisper chunk 可能因模型初次載入而明顯較慢。

---

# 31. 建議操作流程

```text
1. 啟動 Server
2. 開啟 http://127.0.0.1:8000/
3. 選擇來源：
   - YouTube 網址
   - 上傳影片
   - 上傳語音檔
   - 電腦播放聲音
4. 有限媒體可確認擷取開始/結束時間
5. System Audio 選擇正確的 Loopback 輸出裝置
6. 按「開始取得字幕」
7. 觀察即時字幕與執行所耗時間
8. 若需要提早停止，按「停止」
9. 已取得字幕仍保留
10. 下載 TXT / VTT / SRT
```

---

# 32. MIT License

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

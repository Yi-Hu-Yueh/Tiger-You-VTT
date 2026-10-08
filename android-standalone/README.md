# Tiger-You-VTT Standalone — Owner accepted v0.4.1

This is a separate Android application, package `tw.tiger.tigeryouvtt.standalone`.
It does not replace, share configuration with, or call the accepted `android-app/`
Phone Client + PC Server app. Android 10+ (API 29), arm64-v8a or x86_64 required.

**Final owner acceptance: A, B, C, C-UX, D and D2-R all PASS.**
Local-file and microphone workflows are retained. JVM tests do not prove physical
AudioPlaybackCapture behavior, recognition quality, battery use or phone latency.
Version 6 adds an optional online YouTube layer; the accepted offline core remains
network-independent. Version 7 (`0.4.1-checkpoint-d2r`) is the accepted D2-R state.

```text
Android Client + PC Server: OWNER ACCEPTANCE = PASS
OWNER_CHECKPOINT_A = PASS
OWNER_CHECKPOINT_B = PASS
OWNER_CHECKPOINT_C = PASS
OWNER_CHECKPOINT_C_UX = PASS
OWNER_CHECKPOINT_D = PASS
OWNER_CHECKPOINT_D2R = PASS
```

Owner acceptance was declared for physical use; the historical host measurements
below are separate evidence. The retained owner checklists are repeatable
regression procedures, not outstanding acceptance gates. See the root README
for all three architectures and `../docs/LICENSING.md` for redistribution review.

## Architecture

SAF local document → Android MediaExtractor/MediaCodec → downmix → continuous-phase
16 kHz float PCM resampler → bounded 20-second windows → sherpa-onnx JNI/ONNX Runtime
CPU inference → `TranscriptSegment(start, end, text)` → TXT/VTT/SRT SAF export.

There is no Python, HTTP backend, PC URL, mobile token, remote ASR API, LLM, or
pyannote dependency. Version 6 has INTERNET permission solely for the explicitly
started YouTube subsystem; it has no model downloader. Choose a
file physically stored on the phone: cloud SAF providers can otherwise need a
network connection themselves. Airplane-mode transcription is an owner gate.

UI is Traditional Chinese. The recognizer supports Chinese/English/Cantonese/
Japanese/Korean; an optional local Android ICU Simplified–Traditional conversion
is enabled by default. This is script conversion, not a language model; review
proper nouns and mixed-script text.

## Feasibility decision (2026-10-08)

| Option | Evidence / tradeoff | Decision |
|---|---|---|
| whisper.cpp + JNI | Official Android example; multilingual; MIT runtime. Tiny/base/small Q5_1 files are 32,152,673 / 59,707,625 / 190,085,487 bytes. Quantization reduces disk/RAM but not necessarily real-time latency. Upstream unquantized memory estimates are ~273/388/852 MB, not phone measurements or quantized guarantees. Requires NDK/CMake/JNI integration; NDK/CMake were not installed here. Offline PCM windows and native timestamps are attractive. | Strong future alternative, especially tiny/base on limited-memory devices. Not chosen for first integration gate. |
| sherpa-onnx + SenseVoice Small INT8 | Official pinned Android AAR and Kotlin APIs; official offline APK examples; non-autoregressive multilingual model. Two files, ~239.55 MB, CPU, bounded PCM windows. Android native package avoids custom JNI compilation. Native call is synchronous; STOP waits for the current window/model load. | Selected for highest integration confidence on a modern 64-bit phone, subject to actual phone validation. |

No third engine was shown clearly superior enough to justify another dependency.
The 239 MB model is larger than whisper tiny/base: recommend a 4 GB+ RAM phone and
at least 1 GiB free storage before sideload installation. This is a conservative
trial recommendation, **not measured RAM consumption**. No large-v3 model is used.
No desktop GPU results are claimed as Android results.

Sources: [whisper.cpp](https://github.com/ggml-org/whisper.cpp),
[Android JNI example](https://github.com/ggml-org/whisper.cpp/tree/master/examples/whisper.android),
[quantized model files](https://huggingface.co/ggerganov/whisper.cpp/tree/main),
[sherpa Android examples](https://k2-fsa.github.io/sherpa/onnx/android/prebuilt-apk.html),
[SenseVoice support](https://k2-fsa.github.io/sherpa/onnx/sense-voice/index.html),
[pinned runtime release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.8).

## Bundled model and licensing

Model: SenseVoice Small, original authors FunAudioLLM / Alibaba Group; ONNX
conversion distributed by csukuangfj / sherpa-onnx. Runtime is Apache-2.0, ONNX
Runtime is MIT. **Model weights have separate terms**, not the runtime license:
[official model card](https://huggingface.co/FunAudioLLM/SenseVoiceSmall) references
the [FunASR Model Open Source License Agreement](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE).
The model license and author/source attribution are included in APK assets and
the model UI offers an offline license reader. Retain these with redistribution.
The model weights and tokens ARE bundled in version `0.1.1-checkpoint-a-bundled`
(versionCode 2) and remain bundled in accepted versionCode 7. Git excludes `*.onnx`;
the small `tokens.txt`, licenses and pinned metadata are source-controlled.
No LFS/hosting substitution is used. Bootstrap is an explicit pre-build step.

Pinned conversion repository revision: `2365baeacb507f821a0c8120fcee3d484dba7a07`.

| File | Bytes | SHA256 |
|---|---:|---|
| model.int8.onnx | 239233841 | c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51 |
| tokens.txt | 315894 | f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc |

Total: **239,549,735 bytes**. Explicit bootstrap uses these pinned HTTPS URLs:
[model](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/2365baeacb507f821a0c8120fcee3d484dba7a07/model.int8.onnx),
[tokens](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/2365baeacb507f821a0c8120fcee3d484dba7a07/tokens.txt).
Repository: `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`.
The model SHA256 is also published in the upstream Hugging Face LFS/Xet pointer;
the tokens SHA256 was computed from the pinned revision in the original project.
Both downloaded files matched the previously recorded hashes and sizes.
Authoritative local source copy: `D:\TigerModels\Tiger-You-VTT\SenseVoice-Small-INT8\`.
Build assets: `app/src/main/assets/sensevoice-small-int8/`. Run
`../scripts/prepare_standalone_model.ps1` from this directory before building;
it verifies cache and copied assets. The Android app never references
that PC path. `verifyBundledModel` checks BOTH assets before every build, and
Gradle stores ONNX/TXT assets uncompressed. Do not substitute another revision.

On launch, a single foreground job automatically prepares the model without
download/import. APK AssetManager streams into a 64 KiB buffer and writes
`context.filesDir/sensevoice-small-int8/<name>.part`. It validates exact length
and SHA256, fsyncs the file, then atomically renames it on the same filesystem.
No non-atomic fallback is accepted. Both final files are hash-verified before
READY; inference rechecks both before constructing JNI. Backup/device transfer
remain disabled. No external/shared-storage permission is needed.

Every cold start verifies existing bytes; valid files are not recopied. Truncated
or corrupt files are replaced from the APK. Only known `.part` files are removed
on retry, and a completed first file survives interruption before the second.
Cancellation/I/O failure cleans the active partial; process death is recovered
on next launch. READY is never inferred from file size alone. UI errors are
controlled Traditional Chinese messages, not native exception/path dumps.

Before extraction, available internal storage must cover the sum of missing or
invalid files plus a **64 MiB reserve**. Fresh preparation requires exactly
**306,658,599 bytes (293 MiB rounded up)** available AFTER installation. Files are
copied sequentially and renamed, so each temporary becomes the final copy rather
than requiring a second complete extracted copy. Replacement keeps the old file
until its new temporary validates; the free-space check budgets the new bytes.
The installed APK itself also occupies storage, and sideload/package-manager
staging may need additional space: reserve **1 GiB before installation** (device
installer policy may require more). Low storage fails before opening model assets
with a Traditional Chinese free-space message; an I/O failure mid-copy fails
closed. Retry through 「重新準備內建模型」 after freeing space.

## Local-file safety and limitations

- One foreground-service operation at a time covers inference/model management.
- Two CPU inference threads; one recognizer instance per job, released in finally.
- No producer queue: decoder is backpressured by one <=20-second PCM window.
- Maximum input length 60 minutes; cooperative 30-minute wall-time ceiling.
- Severe Android thermal status stops work; no thermal controls are disabled.
- STOP is cooperative, not hard preemption of JNI. Model load/current window can
  delay it; actual upper latency is unmeasured until Checkpoint A.
- Completed segments are journaled in private storage; process restart restores
  them but does not restart a job. Backup disabled. Export to a chosen destination.
- MediaCodec support depends on device codecs/container. Invalid/no-audio media,
  unsupported PCM, permission loss, decoding stalls, and timestamp discontinuities
  fail visibly. WAV/MP3/AAC/M4A/MP4 are candidates, not all-device promises.
- Timestamps are **coarse window boundaries**, not word alignment. Continuous
  sample accounting avoids per-buffer drift; discontinuous media fails rather
  than silently returning misleading synchronization. Words crossing windows and
  silence hallucinations need real-device quality checks; no VAD is claimed.
- Model load time, processing wall time, processed audio duration, RTF, sampled
  peak PSS, and file first-text time are shown after windows complete. Sampled PSS
  is an approximation, not true peak. Live first-text latency is not measured.

## Microphone live on-device ASR — Checkpoint B

VersionCode **3**, versionName **0.2.0-checkpoint-b**, same standalone package.
Output: `dist/android/Tiger-You-VTT-Standalone-Microphone-debug.apk`. The previously
accepted bundled Checkpoint A APK is not overwritten.

`AudioRecord` → one dedicated capture thread → bounded overlapping PCM windows →
two-slot queue → one sherpa-onnx OfflineRecognizer consumer → canonical
`TranscriptSegment` list → existing private journal and TXT/VTT/SRT exports.
Microphone ASR remains without network, PC address, server, or cloud dependencies.

The integrated SenseVoice API is **offline**, not a true incremental streaming
recognizer. This release starts with **3-second windows and 0.5-second overlap**
(2.5-second stride). Two-second windows would invoke the offline model more often
with less linguistic context; five seconds adds more initial latency. Three
seconds is a bounded initial engineering choice, not a measured optimum on the
owner's phone. No PC timing was used to select it. No VAD or provisional-token
prediction is claimed; recent text updates when a window completes.

Capture requests mono PCM16 at 16 kHz first, then tries 48 kHz and 44.1 kHz if
initialization fails. It verifies the actual AudioRecord format/rate/state; Android
may itself convert the hardware rate into the requested capture format. The two
higher returned rates use a continuous **63-tap Hamming-windowed low-pass FIR**
before phase-continuous linear conversion to 16 kHz. This prevents naive decimation
aliasing; FIR delay is below 1 ms. PCM16 is normalized to float. Reads are
nonblocking, in ~20 ms chunks, on an audio-priority thread. The device buffer is
at least 0.5 seconds. A >400 ms read interruption fails closed because timestamps
could otherwise silently lose capture continuity. Format fallback/route changes
still require physical validation.

At most **two queued windows**, one in-flight inference window, one producer
window and one transient offered copy exist. Queue overflow drops the oldest
pending window to keep latency bounded, with visible dropped-window and unique
16 kHz sample counters. Timestamps retain the resulting gap. These are application
queue counters, not a claim of zero hardware/AudioFlinger loss. Retained text is
capped at 10,000 segments / 1,000,000 characters; recording at 30 minutes. Severe
thermal state or capture errors stop safely. No raw recording file accumulates.

Timestamps use captured/resampled sample indices, not inference/UI completion
times. First window is [0,3]; the next has acoustic context [2.5,5.5] but commits
the non-overlapping time span [3,5.5]. Cues are coarse capture windows, not word
alignment. Exact suffix/prefix dedup runs only between adjacent overlapping
windows, requiring four CJK characters or two complete Latin words. It does not
dedup across dropped windows or sessions. Boundary errors/repeated phrases remain
possible and must be assessed by the owner. Previous cue times are immutable;
new microphone sessions append after the last retained cue (session-relative
capture time plus the existing transcript's end offset, not wall-clock downtime).

### STOP, ownership and lifecycle

Local-media and microphone services acquire the **same JobGate before loading a
model**. One consumer owns one recognizer. Duplicate START is rejected; no second
model instance is loaded concurrently. The microphone validates the existing
bundled model and reuses its exact paths/configuration (auto language, two CPU
threads). It releases the recognizer after each job rather than caching competing
models. No change was made to the accepted local-media decoder/service.

STOP closes the queue immediately. The capture thread stops accepting samples
and calls AudioRecord.stop/release independently of native inference. **Target:
capture release within 1 second**, not guaranteed without device measurement.
An already-running JNI decode cannot be safely preempted: its completed result
is retained, and START remains unavailable until that model is released. The UI
distinguishes microphone activity from final inference/cleanup. Queued windows
and the unfinished tail are deliberately discarded on STOP and counted separately;
wait for text before stopping when the final words matter. This prioritizes prompt
capture shutdown over draining additional inference jobs. No unsafe concurrent
release of a recognizer being decoded is attempted.

Microphone capture permission is requested on the microphone page. Starting requires
the visible workflow plus granted RECORD_AUDIO. A non-exported microphone-type
foreground service displays a notification with STOP. On Android 11+ it selects
the microphone service type; API 29 uses the compatible foreground start. Leaving
the tab, backgrounding, screen lock, rotation, task removal or service destruction
signals STOP. Rotation intentionally ends the session; it does not restart capture.
Process restart restores only completed transcript, never recording state. System
permission revocation, capture silencing/unavailability, initialization failure,
native errors and overflow are visible with controlled Traditional Chinese text.

UI reports source rate, capture duration, model-load time, completed windows,
first-text latency from capture start, latest/mean processing time, approximate RTF
(processing time divided by newly covered audio), queue depth/drop counts,
STOP-to-capture-release time and sampled peak PSS. These are runtime measurements;
host fake-source test timing is explicitly **not phone performance**. Transcript
is retained on STOP and exported through the canonical exporters. 「清除」 requires
confirmation, clears the shared local-file/microphone/playback transcript only, and is
disabled while any job is active.

Sources: [Android AudioRecord](https://developer.android.com/reference/android/media/AudioRecord),
[microphone foreground service prerequisites](https://developer.android.com/develop/background-work/services/fgs/service-types#microphone),
[pinned sherpa OfflineRecognizer API](https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/sherpa-onnx/kotlin-api/OfflineRecognizer.kt).

## Playback live on-device ASR — Checkpoint C

VersionCode **4**, versionName **0.3.0-checkpoint-c**, same standalone package.
Output: `dist/android/Tiger-You-VTT-Standalone-Playback-debug.apk`. Checkpoint A,
Checkpoint B and existing Android client APKs are not overwritten.

System MediaProjection consent → mediaProjection foreground service →
AudioPlaybackCaptureConfiguration + AudioRecord → the **same** bounded live PCM
pipeline and single SenseVoice consumer used by B → canonical transcript/export.
The playback service subclasses the shared microphone service's inference runner,
overriding capture, consent, notification and lifecycle hooks only. Local-media
decoding is unchanged. All three sources acquire the same JobGate before model
loading; a busy model or duplicate START is rejected in Traditional Chinese.

### Consent and platform policy

「裝置音訊」 validates model readiness, model ownership and RECORD_AUDIO, then
opens Android's system consent dialog. Each session requests fresh consent. Only
a request id survives activity recreation; projection result data is never saved
to disk or reused. Cancellation does not start capture. Consent is rechecked at
service start, followed by `startForeground(...MEDIA_PROJECTION)`, then one
`getMediaProjection` call and callback registration, then playback AudioRecord.
The service is not exported and returns START_NOT_STICKY; process restart restores
completed text, never a capture session. No virtual display, video recording or
screen image is created.

Configuration matches **USAGE_MEDIA, USAGE_GAME and USAGE_UNKNOWN** only. Android
enforces same-profile and source-app capture-policy restrictions. RECORD_AUDIO
is also required by playback capture, but this source never selects a microphone
AudioRecord and never falls back to one. No security, DRM, FLAG_SECURE or app
opt-out bypass is attempted. No all-app/YouTube/call capture promise is made.

Source: [Android audio playback capture](https://developer.android.com/media/platform/av-capture),
[MediaProjection lifecycle](https://developer.android.com/media/grow/media-projection),
[foreground-service ordering and type](https://developer.android.com/develop/background-work/services/fgs/service-types#media-projection).

### PCM, windows and silence

Playback requests PCM16 mono at 16 kHz, then 48 kHz and 44.1 kHz; stereo at 48 kHz
or 44.1 kHz is a final format fallback, never a different audio source. Actual
rate, channel count, encoding and AudioRecord state are validated. Stereo uses
an overflow-safe arithmetic mean, carrying partial frames between reads. Mono
is unchanged. Both use B's continuous 63-tap anti-alias FIR / phase-continuous
resampler and normalize to mono 16 kHz float PCM.

B's **3-second window / 0.5-second overlap / two queued windows** remains intact.
Overflow drops the oldest pending window, counts unique dropped samples and
preserves timestamp gaps. Capture sample indices produce nonnegative monotonic
coarse window timestamps; finalized cues are immutable. Conservative adjacent
overlap dedup is unchanged (four CJK characters or two complete Latin words).
New sessions append after existing retained text. This is windowed offline-model
inference, not true incremental streaming or word-level alignment.

After **8 seconds below -60 dBFS RMS**, a recoverable notice appears once:
「目前未收到可擷取的裝置音訊。來源 App 可能禁止播放音訊擷取。」 Meaningful PCM
clears the notice; ordinary brief silence is not an error. Entirely quiet windows
skip ASR to reduce silence hallucinations; this energy threshold is not speech
VAD and may miss extremely quiet speech. Processed-window and RTF metrics include
these skipped windows and are not a speech-only throughput benchmark.

Initial zero-length reads receive a 10-second grace period (allowing the 8-second
notice), then fail visibly if no frames arrive. After the first PCM, B's 400 ms
read-stall watchdog remains to avoid hiding continuity loss. Silent PCM frames
do not trigger this read-stall error. The app cannot prove why a source is silent:
pause, low volume and source-policy restrictions are possibilities, not diagnoses.

### STOP and lifecycle

Playback intentionally continues when switching to a player or recreating the
activity. An ongoing foreground notification identifies the source and provides
STOP; Android's capture indicator remains visible. Notification permission is
optional; if denied, Android still exposes foreground work in Active apps and
the in-app STOP remains available. Microphone B's stop-on-background/tab-exit
policy is unchanged. Lock/screen-off, task removal, projection revocation, service
destruction, severe heat and permission loss stop playback safely.

STOP immediately rejects new PCM, closes the queue and releases MediaProjection;
the dedicated nonblocking producer stops/releases AudioRecord independently of
in-flight native inference. Projection callbacks and screen receivers are removed
idempotently, including startup failure. The capture indicator should disappear
without waiting for inference; real device timing is an owner gate. Queued and
partial-tail audio is discarded and counted. An already-running native decode
finishes safely and retains its result; the model gate remains owned until native
cleanup completes. The foreground notification reports final cleanup, then is
removed. No unsafe native recognizer preemption is attempted.

The Traditional Chinese UI includes 尚未開始 / 等待系統授權 / 準備擷取 / 擷取中 /
停止中 / 已停止 / 錯誤, START/STOP/confirmed clear, canonical TXT/VTT/SRT exports,
capture duration, recent/retained text, model-load/first-text/window timing,
approximate RTF, queue/drop/STOP counters and sampled peak PSS. Export is available
after STOP finishes. The same 30-minute capture and transcript-size limits apply.
No raw playback media file accumulates. Playback needs no network, backend or PC;
test with a local player and airplane mode plus Wi-Fi off. Version 6's INTERNET
permission does not change the offline playback path.

## Checkpoint C UX refinement — default-display consent

VersionCode **5**, versionName **0.3.1-checkpoint-c-ux**. New output:
`dist/android/Tiger-You-VTT-Standalone-Playback-UX-debug.apk`; the accepted
Checkpoint C APK above and all earlier APKs are preserved.

The former no-argument `createScreenCaptureIntent()` lets Android 14+ offer app
or whole-display sharing, explaining the owner's app-selection screen. Tiger
does not create a custom app/window picker. For **API 34+**, START now calls
`MediaProjectionManager.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())`.
This is Android's official default-display/app-sharing opt-out API, verified
against the installed SDK 36.1 public stubs and sources. **API 29–33** retains
the original no-argument system consent path, guarded from references to the
new API. minSdk 29 / targetSdk 36 remain unchanged.

Source: [default-display API (added in API 34)](https://developer.android.com/reference/android/media/projection/MediaProjectionConfig#createConfigForDefaultDisplay()),
[official app-sharing opt-out and manufacturer override caveat](https://developer.android.com/media/grow/media-projection#opt_out).

There is one system-consent launch per START, not a promise of exactly one
dialog on every phone. Android/manufacturer policy controls the actual screens
and can override the opt-out. If a chooser remains, do not bypass it. The UI
states this limitation neutrally; the owner must confirm whether the separate
app-selection screen disappears on their device.

Fresh consent, canceled/stale-result rejection, model ownership, foreground
service ordering, STOP and revocation cleanup are unchanged. Authorization is
default-display scoped but actual capture remains **audio-only**: no virtual
display, screen pixels, encoder or video file. No microphone fallback, network,
hidden APIs, automatic clicking, token reuse or security-policy bypass is added.
All PCM, windowing, deduplication, inference, transcript and export paths remain
unchanged. Focused `PlaybackUxTest` checks SDK wiring/source contracts; the
existing `PlaybackTest` executes consent-state, capture-pipeline and STOP tests.
Neither proves the physical Android consent UI.

## Checkpoint D — optional online YouTube, local ASR

Microphone (AudioRecord) and playback are the owner-accepted B/C baseline.
The default-display consent UX has OWNER_CHECKPOINT_C_UX = PASS. No new
physical playback or consent-UI result is inferred from JVM tests.
Android only permits capture of eligible media/game/unknown usage, in the same
profile, with consent and capture policy permitting it. Apps/DRM can disallow it.
Do not promise capture of YouTube, calls, every app, or protected audio.
[Android platform rules](https://developer.android.com/media/platform/av-capture).

VersionCode **6**, versionName **0.4.0-checkpoint-d-youtube**, package unchanged.
New artifact: `dist/android/Tiger-You-VTT-Standalone-YouTube-debug.apk`. All prior
accepted APKs are preserved. This supersedes the earlier direct-URL feasibility
deferral, but does not promise every YouTube video or future extraction stability.

### D0 selection and dependency pin

Selected **NewPipe Extractor v0.26.5**, revision
`f9e6bb808f82bf3e4dc1f2a29a16fd376931c8ef`, using
`com.github.teamnewpipe:NewPipeExtractor:v0.26.5` from JitPack (restricted to its
dependency groups). The exact release source, downloader APIs, metadata/search,
subtitle format selection, audio-stream APIs and published POM were inspected.
It is an actively maintained JVM library supporting anonymous YouTube without
an owner API key, PC, Python, Deno or browser engine. It exposes manual/automatic
captions and audio-only streams. No clearly superior maintained JVM candidate
was established. `youtubedl-android` was evaluated but rejected for this phase:
it bundles Python/yt-dlp and would reintroduce the PC-style runtime complexity.

Other direct pins: OkHttp **4.12.0**, jsoup **1.22.2**, Android
`desugar_jdk_libs_nio` **2.1.5** (required by upstream for minSdk below 33).
NewPipe supplies a JVM Rhino interpreter internally; this is not Deno or a
browser engine. Upstream extraction remains unofficial and can break; no local
HTML/player-JavaScript compatibility patches or infinite retries are added.

Sources: [pinned NewPipe release](https://github.com/TeamNewPipe/NewPipeExtractor/releases/tag/v0.26.5),
[integration/requirements/license](https://github.com/TeamNewPipe/NewPipeExtractor/tree/v0.26.5),
[Python-based alternative](https://github.com/yausername/youtubedl-android).

**Licensing:** NewPipe is GPL-3.0-or-later, not MIT. Its complete license and source
attribution are bundled and readable offline from the YouTube tab. Original Tiger
source notices remain MIT; a linked APK must not be advertised as MIT-only.
Before public redistribution, fulfill applicable GPL corresponding-source and
notice obligations. The unmodified SenseVoice weights remain separately licensed
model data under their existing FunASR terms; no relicensing is claimed. This
private owner-test build is not a legal certification of redistribution
compatibility. No public release, commit, push or project-wide license change
is performed by this task.

### D1 search and captions

The Traditional Chinese YouTube tab has bounded keyword search (1–200 chars,
first page, at most 20 unique videos), a text-only result list with title/channel/
duration, direct URL input, metadata inspection and 取得字幕. Thumbnails are
deferred to avoid extra image requests and dependencies. Selecting a result
prepares its canonical video URL; it does not execute arbitrary returned URLs.
HTTPS watch, youtu.be, shorts, live and embed URLs are validated against exact
YouTube hostnames and 11-character IDs. Playlist-only/arbitrary URLs are rejected.

Captions are requested explicitly as **WebVTT** using the pinned extractor API.
Selection is zh-TW → other zh → en → other available languages; within a language
manual precedes automatic. At most 12 candidate tracks are tried. Caption network
or verification failures are reported, not disguised as caption absence. Empty
or malformed VTT can proceed to another track, then audio fallback. Valid captions
skip audio discovery/download and never claim the ASR gate.

The bounded deterministic VTT parser handles cue IDs/settings, HTML formatting,
entities and inline timestamp markup. It preserves cue order and source times;
to satisfy the existing non-overlapping canonical exporter, overlapping prior
cue ends are clipped to the next start, and equal-start cue text is combined.
It does not semantically rewrite or deduplicate rolling automatic captions.
Invalid/out-of-order timing is rejected; maximum 10,000 cues/1,000,000 text chars.
All results are existing `TranscriptSegment` values exported with the original
TXT/VTT/SRT renderer. No second exporter is added.

Caption results live in YouTube tab state, separate from concurrently running
offline text, and remain after STOP until process exit. Export before closing
the process. New results replace the prior YouTube transcript; failure/cancel
keeps already produced segments. Audio-ASR output also follows the existing
local private journal. This is not a multi-video persistent library.

### D2 audio and shared inference

No usable captions → acquire the existing single model gate → select an
audio-only **M4A progressive HTTPS URL** nearest 96 kbps → bounded private cache
download → original Android MediaExtractor/MediaCodec + 16 kHz mono pipeline →
the same local SenseVoice runner → canonical transcript/export. Adaptive itag
audio exposed as a direct progressive URL is supported. Manifest-only DASH/HLS,
live/unknown-duration and >60-minute sources fail visibly; no video download or
new demuxer is substituted. The accepted transcriber was extracted into
`LocalTranscriber` without adding another recognizer implementation.

Cache directory: `context.cacheDir/youtube-audio`; only generated `yt-*.m4a`
files are cleaned. Maximum **100 MiB**, plus **32 MiB** free-space reserve;
unknown-length responses budget the full limit. Length mismatch/empty responses
fail. The private file is removed in finally on success, failure and cancellation;
stale files after process death are removed on the next explicit YouTube job.
Cleanup failure is visible, not silently reported as success. Nothing is placed
in shared storage; no storage permission is added.

STOP cancels the active OkHttp call and signals cooperative decode/inference
cancellation. Native inference is not forcibly preempted. Completed text remains,
and the gate is released only after runner cleanup. A stale local-service STOP
or destruction cannot cancel a different service's later model owner. Duplicate
YouTube jobs are rejected; caption-only jobs may coexist with offline ASR because
they do not acquire or overwrite its model/transcript state.

### Isolation, privacy and network limits

INTERNET is used only for YouTube search, metadata, captions and audio download.
No network client/extractor is initialized by application startup, model prep,
local-file, microphone or playback services. Network setup is lazy inside an
explicit YouTube service operation. All ASR remains on-device; no local file,
transcript, microphone or playback samples are uploaded. No telemetry, Google
login, cookie import, browser credentials, PC server or cloud ASR is added.

HTTPS resource hosts and every redirect are restricted to YouTube/Google media
domains. No cookie jar or Authorization/browser-cookie headers are used; no
signed media URL is shown in UI or intentionally logged. Extractor text responses
are capped at 8 MiB, captions at 2 MB, redirects at five; connection/read/metadata-call
timeouts are 10/20/60 seconds. D2-R audio uses 120 seconds per request and a
15-minute transfer budget. No automatic retry loop. Titles/channels are plain
Compose text, not executable HTML. Authentication/bot challenges produce a
controlled Traditional Chinese result; source authentication is a future gate.
No automatic fallback to microphone or AudioPlaybackCapture exists.

Offline regression contracts now check the actual network independence of A/B/C
instead of incorrectly asserting that the entire version-6 APK lacks INTERNET.
These tests do not substitute for the owner's airplane-mode regression on phone.

### Explicit real-network probes (never normal unit tests)

`./gradlew.bat :app:youtubeSmoke` runs the production adapter/workflow against:

- Search: `Rick Astley Never Gonna Give You Up`
- Captions: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`
- No captions/audio discovery: `https://www.youtube.com/watch?v=Y_5hEaDzAsE`

It checks caption parsing/export without ASR and downloads the complete
no-caption M4A through the production downloader, validates its ISO-BMFF boxes,
then removes the file. It does not run Android MediaCodec/JNI or prove phone
ASR. A 300-second host watchdog cancels HTTP. The separate opt-in D0 JVM harness in
`tools/youtube-smoke/` uses the same pinned upstream library independently of
Android integration; invoke only explicitly with `--live`. Avoid repeated probes.

Original Checkpoint D host validation on 2026-10-08: **51 focused / 199 full standalone tests passed**,
zero failures or skips; existing Android client **20 passed**. Debug build and
lint succeeded (11 non-fatal warnings). The production adapter returned 19 search
results; `dQw4w9WgXcQ` exposed six tracks and selected manual English, producing
61 canonical segments and all three exports without ASR. `Y_5hEaDzAsE` exposed
zero caption tracks and a 128 kbps M4A audio-only stream; a bounded range request
returned HTTP 206 with 1,024 bytes containing the M4A initialization header.
This verifies stream discovery/access, **not full download/decode/ASR on Android**.
No phone was connected during that historical host run; do not
interpret these host results as full Checkpoint D PASS.

### Checkpoint D2-R — complete-download repair (2026-10-09)

VersionCode **7**, versionName **0.4.1-checkpoint-d2r**, same package. New APK:
`dist/android/Tiger-You-VTT-Standalone-YouTube-D2R-debug.apk`; previous APKs must
not be overwritten. Subsequent owner phone acceptance is PASS as recorded above.

**Reproduced root cause:** the original no-Range production request for
`Y_5hEaDzAsE` returned HTTP **200**, `audio/mp4`, Content-Length **29,690,750**.
The transfer ran at approximately 32 KiB/s and the **whole-call 60-second timeout**
aborted it at **60,403 ms / 1,916,928 bytes** (`InterruptedIOException`). Its
generic IOException UI hid the timeout. The physical owner's exact exception
was not captured; this is a matching host reproduction, not phone proof.

**Repair:** preserve pinned OkHttp 4.12.0 and NewPipe v0.26.5; download fresh,
sequential **1 MiB HTTP byte ranges**, starting at zero. This is not persistent
resume or automatic retry. At most 100 requests / 100 MiB, 15 minutes total,
10-second connect / 20-second idle read / 120-second per-media-call limits.
Every redirect remains HTTPS and allowlisted (at most five). Each 206 must
match the requested start/end, stable total size, Content-Length where available,
and stable ETag when supplied. A range-ignoring 200 is accepted only as the
first complete response; never append a 200 after a prior range.

Media headers: unchanged `User-Agent: Mozilla/5.0`, conventional `Range`, and
`Accept-Encoding: identity` so byte counts refer to the stored representation.
No Origin/Referer/client spoofing, account cookies, credentials, copied PO tokens,
or token broker. The pinned AudioStream/Stream APIs expose no per-media request
header map. Its URL already carries upstream-generated client/nonce context;
it is preserved, not reconstructed. Itag **140**, format **M4A**, MIME
**audio/mp4**, **128 kbps**, **PROGRESSIVE_HTTP**, and ItagItem content length
are retained. Resolve and download in the same job; never persist signed URLs.
Explicitly expired URLs fail visibly; automatic refresh/retries are **zero**.

Official API evidence:
[AudioStream](https://github.com/TeamNewPipe/NewPipeExtractor/blob/v0.26.5/extractor/src/main/java/org/schabi/newpipe/extractor/stream/AudioStream.java),
[Stream](https://github.com/TeamNewPipe/NewPipeExtractor/blob/v0.26.5/extractor/src/main/java/org/schabi/newpipe/extractor/stream/Stream.java),
[YouTube stream construction](https://github.com/TeamNewPipe/NewPipeExtractor/blob/v0.26.5/extractor/src/main/java/org/schabi/newpipe/extractor/services/youtube/extractors/YoutubeStreamExtractor.java).
Upstream's Android downloader also uses ordinary byte ranges; no upstream
extractor modification or blind upgrade is needed for this reproduced failure.

Download into a private `.m4a.part`, streaming with a 64 KiB buffer. Only after
positive and consistent byte counts, fsync/close, and full-file ISO-BMFF box
validation (`ftyp`, `moov`, `mdat`, box boundaries) is it renamed to `.m4a`.
This checks integrity/container, not Android codec support. Success/failure/STOP
clean temporary data; abandoned parts are cleaned on the next YouTube job.
Cleanup failure is reported, not silently ignored.

The UI distinguishes DNS, unreachable network, connect/read/whole-call timeout,
TLS, HTTP status (including 400/401/403/404/416/other 4xx/5xx), unsafe/excessive
redirects, invalid/expired URL, byte mismatch/EOF, storage, container, decode,
and ASR failures. Safe diagnostics contain only hostname, numeric status/bytes,
MIME, redirect count, range flag and enumerated problem. No exception text,
query string, signed URL, cookie or authorization header is emitted. Downloaded
MB and preparation/decode/ASR stages are visible. The shared LocalTranscriber
only gains default-no-op stage observation; its accepted inference/decoder,
microphone/playback, exporters, and C UX consent behavior are unchanged.

**Actual repaired host result:** same known video, complete **29,690,750 bytes**
in **22,506 ms**, HTTP **206**, 29 contiguous ranges, no redirects,
`audio/mp4`, host `rr3---sn-45gx5nuvox-u2xed.googlevideo.com`. Expected and written
sizes agree; all boxes validated, `.part` promoted, then host temp file removed.
No 403/PO-token/SABR restriction was observed; no claim is made about all networks.
D1 `dQw4w9WgXcQ` still returned six tracks / manual English / 61 canonical
segments and TXT/VTT/SRT without ASR. No physical phone was connected.

Final D2-R validation: **33 new download tests + 51 YouTube regression tests
passed (84 focused); 232 full standalone tests passed; 20 existing Android
client tests passed**, zero failures/errors/skips. Debug assembly and lint passed
(zero lint errors, 11 warnings). All 121 checked non-standalone project files
matched their pre-task hashes. No Python backend tests or Git mutations ran.

**OWNER_CHECKPOINT_D2R = PASS.** Accepted owner regression procedure:

1. Install/update `Tiger-You-VTT-Standalone-YouTube-D2R-debug.apk`.
2. PC Tiger-You-VTT server OFF.
3. Phone network ON.
4. Open YouTube.
5. Paste `https://www.youtube.com/watch?v=Y_5hEaDzAsE`.
6. Confirm metadata succeeds, 0 captions, audio download starts, progresses beyond
   1 KB, completes, decode begins, local SenseVoice begins, and transcript appears.
7. STOP/cancel test once.
8. Retry and complete once.
9. Export TXT/VTT/SRT.
10. Report the exact UI stage and safe diagnostic if it fails.

Also retest `https://www.youtube.com/watch?v=dQw4w9WgXcQ` captions. Phone-specific
CDN/network behavior, Android codecs and actual ASR remain unverified here.

### Owner Checkpoint D — accepted regression procedure

**D1 — DIRECT CAPTIONS**

1. Install/update the YouTube APK.
2. Enable Wi-Fi/mobile network.
3. Open YouTube tab.
4. Search for a known public video.
5. Confirm results appear.
6. Select one result with captions.
7. Confirm title/channel/duration.
8. Retrieve subtitles.
9. Confirm transcript appears.
10. Export TXT/VTT/SRT.
11. Test the same video using direct URL input.

**D2 — NO-CAPTION ASR FALLBACK**

1. Select a public video without usable captions.
2. Confirm UI reports no captions and switches to local ASR.
3. Confirm audio is downloaded without video where practical.
4. Confirm SenseVoice runs on phone.
5. Confirm transcript appears.
6. Confirm temporary audio is cleaned afterward.
7. Export TXT/VTT/SRT.
8. Turn PC server completely off and repeat enough to prove independence.

Report phone model, Android version, search result, tested video IDs, direct
caption/direct URL/audio fallback success, download time, ASR processing time,
RTF, memory and extractor errors. Also repeat A/B/C with airplane mode ON, Wi-Fi
and mobile data OFF; exercise STOP during download/ASR and verify retained text.
**OWNER_CHECKPOINT_D = PASS.** Physical acceptance is owner-declared.

## Build / tests

Use JDK 17+, Android SDK 36.1 / build-tools 36.0.0. This isolated project reuses the
existing verified Gradle 9.3.1 wrapper and AGP 9.1.1 / Compose plugin 2.2.10.

```powershell
cd android-standalone
./tools/fetch-runtime.ps1
../scripts/prepare_standalone_model.ps1
./gradlew.bat :app:testDebugUnitTest --tests '*BundledModelTest'
./gradlew.bat :app:testDebugUnitTest --tests '*MicrophoneTest'
./gradlew.bat :app:testDebugUnitTest --tests '*PlaybackTest'
./gradlew.bat :app:testDebugUnitTest --tests '*Playback*Test'
./gradlew.bat :app:testDebugUnitTest --tests '*YouTubeTest'
./gradlew.bat test assembleDebug lintDebug
```

The official AAR (50,129,134 bytes) is downloaded only into ignored
`.runtime/standalone-deps/`; every preBuild verifies SHA256
`633c24321e06b1fe79feafa03ea16cbc0f8a286641e2da3559bac91bdb13bd96`.
No source tree needs NDK/CMake installed for this prebuilt runtime.
Tests cover conversion/export, chronology, resampling/bounds, single-job and stop
state, model manifest and real asset hashes, storage boundaries, no-recopy,
partial/cancel recovery, offline source/UI contracts, and readiness gating.
Native MediaCodec/JNI, permissions, FGS lifecycle and physical-device behavior
remain device-test gates; JVM tests are not substitutes.

## Owner Checkpoint A — accepted; retain as regression procedure

1. Install/update `dist/android/Tiger-You-VTT-Standalone-Bundled-debug.apk` on a 64-bit Android
   10+ phone. It must appear beside the existing Tiger-You-VTT, not replace it.
2. Launch and wait for **正在準備內建模型... → 已就緒**, with **來源：APK 內建**.
   Confirm no download/import is requested. Read the offline model license.
   No microphone permission is needed for local-file transcription.
3. Put a 10–30 second clear Chinese/English audio/video file in phone storage.
4. Turn the PC Tiger-You-VTT server off, enable airplane mode AND disable Wi-Fi, open **本機檔案**,
   select that file, and start transcription. No server/token setup should appear.
5. Verify recognized text; export TXT/VTT/SRT to phone Documents. Open each and
   check coarse cue times against the source, especially a file with leading silence.
6. Try a longer file, STOP, retain/export partial results, rotate/reopen the app,
   then restart another job. Try invalid media and canceled pickers. Record any crash.
7. Send phone model/Android version, model load time, media duration, processing
   time, RTF, sampled PSS/memory if shown, and recognition/export/STOP results.

## Owner Checkpoint B — accepted; retain as regression procedure

1. Install/update the Checkpoint B APK.
2. Open 麥克風.
3. Grant microphone permission.
4. Turn airplane mode ON.
5. Ensure Wi-Fi OFF.
6. Ensure PC server OFF.
7. Tap 開始.
8. Speak Mandarin for approximately 15–30 seconds.
9. Confirm text appears.
10. Tap 停止.
11. Confirm STOP is prompt and transcript remains.
12. Export TXT/VTT/SRT.
13. Clear transcript.
14. Start again.
15. Speak English for approximately 15–30 seconds.
16. Stop and verify English transcript.
17. Report phone model, Android version, Chinese quality, English quality,
    first-text latency, processing/window time, RTF, STOP latency, PSS/memory,
    and any duplicated/missing phrases.

Also retain denial/revocation, STOP during model load, local-file contention,
rotation, tab exit/backgrounding and restart as microphone regression checks.
This task does not claim new physical microphone measurements.

## Owner Checkpoint C — accepted; retain as regression procedure

1. Install/update the Checkpoint C APK.
2. Open Tiger-You-VTT Standalone.
3. Open "裝置音訊".
4. Turn airplane mode ON.
5. Turn Wi-Fi OFF.
6. Ensure PC Tiger-You-VTT server is OFF.
7. Prepare a local Chinese/English audio/video file on the phone.
8. Play it using a local Android media player that permits playback capture.
9. Return to Tiger-You-VTT and tap START.
10. Accept the Android screen/audio capture consent dialog.
11. Resume/play the local media.
12. Confirm subtitles appear.
13. Confirm the app is not using microphone fallback.
14. Tap STOP.
15. Confirm Android capture indicator disappears.
16. Confirm transcript remains.
17. Export TXT/VTT/SRT.
18. Record:
    - phone model
    - Android version
    - source media/player
    - first-text latency
    - window processing time
    - RTF
    - STOP latency
    - PSS/memory
    - duplicate phrases
    - missing phrases
    - dropped windows
19. Then optionally test another application.
20. Do not treat an app that prohibits capture as a Tiger-You-VTT failure
    until a known capturable source has also been tested.

**OWNER_CHECKPOINT_C is accepted per the owner.** Retain consent cancellation, system
revocation, notification STOP during model loading/inference, background and
rotation without duplicate sessions, screen lock, task removal and a fresh
consent dialog on restart. Device latency, background battery use, thermal limits,
OEM policies and recognition quality remain unmeasured here. No next feature is
authorized by this UX task.

## Owner Checkpoint C UX — accepted regression procedure

1. Install/update the new UX APK.
2. Open 裝置音訊.
3. Tap 開始.
4. Observe exactly what Android shows.
5. Confirm whether the separate "選擇要分享的應用程式" screen is gone.
6. Approve the remaining system consent.
7. Play a local media source.
8. Confirm subtitles still appear.
9. STOP and verify transcript remains.

**OWNER_CHECKPOINT_C_UX = PASS.** Acceptance applies to the owner's tested device,
not every OEM or Android version. Platform-enforced selection must remain
legal and functional; no unsupported workaround is permitted.

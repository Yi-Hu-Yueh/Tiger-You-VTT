package tw.tiger.tigeryouvtt.standalone

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
import androidx.lifecycle.Lifecycle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    private var microphonePermission by mutableStateOf(false)
    override fun onResume() {
        super.onResume()
        microphonePermission = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED
        MicrophoneJobs.foregroundVisible.set(true)
    }
    override fun onStop() {
        MicrophoneJobs.foregroundVisible.set(false)
        MicrophoneJobs.stop("已離開畫面，錄音停止中；保留已完成字幕")
        super.onStop()
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        LocalJobs.initialize(this)
        setContent { MaterialTheme { StandaloneScreen() } }
        if (!LocalJobs.state.value.modelReady && !LocalJobs.gate.busy()) {
            runCatching { startForegroundService(Intent(this, LocalService::class.java).setAction("prepare")) }
                .onFailure { LocalJobs.state.value = LocalJobs.state.value.copy(modelStatus = "尚未就緒：無法啟動準備，請保持 App 開啟後重試") }
        }
    }
    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    private fun StandaloneScreen() {
        val state by LocalJobs.state.collectAsState()
        val microphone by MicrophoneJobs.state.collectAsState()
        val playback by PlaybackJobs.state.collectAsState()
        val playbackPhase by PlaybackJobs.phase.collectAsState()
        val playbackNotice by PlaybackJobs.notice.collectAsState()
        var tab by rememberSaveable { mutableIntStateOf(intent.getIntExtra("capture-tab", 0).coerceIn(0, 5)) }
        var projectionRequest by rememberSaveable { mutableLongStateOf(-1) }
        DisposableEffect(tab) {
            MicrophoneJobs.tabVisible.set(tab == 1)
            onDispose {
                if (tab == 1) {
                    MicrophoneJobs.tabVisible.set(false)
                    MicrophoneJobs.stop("已離開麥克風頁面，錄音停止中；保留字幕")
                }
            }
        }
        var uriText by rememberSaveable { mutableStateOf("") }
        var exportFormat by rememberSaveable { mutableStateOf("txt") }
        var traditional by rememberSaveable { mutableStateOf(true) }
        var licenseText by remember { mutableStateOf<String?>(null) }
        var clearMicrophone by remember { mutableStateOf(false) }
        var clearPlayback by remember { mutableStateOf(false) }
        var notice by remember { mutableStateOf("") }
        val scope = rememberCoroutineScope()
        val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (!granted) notice = "通知權限未授予；請保持 App 可見並使用畫面上的停止按鈕"
        }
        val microphoneRequest = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            microphonePermission = granted
            notice = if (granted) "麥克風權限已允許，請按開始" else "麥克風權限未授予；可再次允許，或至系統設定開啟"
        }
        val playbackPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            microphonePermission = granted
            notice = if (granted) "錄音權限已允許；請按裝置音訊開始取得系統擷取授權" else "錄音權限未授予，無法擷取裝置音訊；此模式不會改用麥克風"
        }
        val projectionConsent = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val request = projectionRequest
            projectionRequest = -1
            if (PlaybackJobs.result(request, result.resultCode == RESULT_OK && result.data != null)) {
                val error = playbackStartError(microphonePermission, LocalJobs.state.value.modelReady, LocalJobs.gate.busy())
                if (error != null || !lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) {
                    PlaybackJobs.fail(error ?: "請回到裝置音訊頁面後重新開始並授權")
                } else {
                    runCatching { startForegroundService(Intent(this, PlaybackService::class.java).setAction("start")
                        .putExtra("request-id", request).putExtra("projection-result", result.resultCode)
                        .putExtra("projection-data", result.data).putExtra("traditional", traditional)) }
                        .onFailure { PlaybackJobs.fail("無法啟動裝置音訊前景服務，請重新開始並授權") }
                }
            }
        }
        fun startPlayback() {
            val error = playbackStartError(microphonePermission, state.modelReady, LocalJobs.gate.busy())
            if (error != null) { notice = error; return }
            if (projectionRequest >= 0) { notice = "系統授權尚未返回，請先完成或取消對話框"; return }
            val request = PlaybackJobs.request(state.modelReady, LocalJobs.gate.busy())
            if (request == null) { notice = "裝置音訊工作或授權仍在處理中"; return }
            projectionRequest = request
            runCatching {
                val manager = getSystemService(MediaProjectionManager::class.java)
                // Official API 34+ opt-out of app selection. Fresh system consent is still required.
                // We use this authorization only for playback audio, never a virtual display or video surface.
                val consentIntent = if (Build.VERSION.SDK_INT >= 34) {
                    manager.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())
                } else {
                    manager.createScreenCaptureIntent()
                }
                projectionConsent.launch(consentIntent)
            }
                .onFailure { projectionRequest = -1; PlaybackJobs.fail("無法開啟 Android 擷取授權，請稍後重試") }
        }
        fun startMicrophone() {
            val error = microphoneStartError(microphonePermission, state.modelReady, LocalJobs.gate.busy(), MicrophoneJobs.visible())
            if (error != null) { notice = error; return }
            runCatching { startForegroundService(Intent(this, MicrophoneService::class.java).setAction("start").putExtra("traditional", traditional)) }
                .onFailure { notice = "無法啟動麥克風，請確認錄音權限並保持此頁開啟" }
        }
        fun begin(action: String, uri: Uri? = null) {
            if (LocalJobs.gate.busy()) return
            if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) permission.launch(Manifest.permission.POST_NOTIFICATIONS)
            runCatching { startForegroundService(Intent(this, LocalService::class.java).setAction(action).setData(uri)
                .putExtra("traditional", traditional).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)) }
                .onFailure { notice = "無法啟動工作，請保持 App 開啟後重試" }
        }
        val mediaPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) {
                runCatching { contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
                uriText = uri.toString(); notice = "已選取檔案（雲端供應者的檔案請先下載到手機）"
            }
        }
        val exporter = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/plain")) { uri ->
            if (uri != null) {
                val snapshot = state.segments
                val format = exportFormat
                scope.launch {
                    notice = runCatching { withContext(Dispatchers.IO) {
                        contentResolver.openOutputStream(uri, "wt").use { output ->
                            requireNotNull(output) { "無法建立檔案" }
                            output.write(Export.render(snapshot, format).toByteArray(Charsets.UTF_8))
                        }
                    }; "已匯出 ${format.uppercase()}" }.getOrElse { "匯出失敗，請重新選擇儲存位置" }
                }
            }
        }
        Scaffold { padding ->
            Column(Modifier.padding(padding).fillMaxSize()) {
                Text("Tiger-You-VTT Standalone", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(16.dp))
                Text("獨立模式：不需要電腦伺服器", modifier = Modifier.padding(horizontal = 16.dp))
                ScrollableTabRow(tab) {
                    listOf("本機檔案", "麥克風", "裝置音訊", "YouTube", "模型", "設定").forEachIndexed { i, label ->
                        Tab(selected = tab == i, onClick = { tab = i }, text = { Text(label) })
                    }
                }
                Column(Modifier.padding(16.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(state.status, style = MaterialTheme.typography.titleMedium)
                    if (state.busy) {
                        LinearProgressIndicator(Modifier.fillMaxWidth())
                        Button(onClick = {
                            if (YouTubeJobs.ownsModel.get()) YouTubeJobs.stop()
                            else if (PlaybackJobs.active.get() != null) PlaybackJobs.stop()
                            else if (MicrophoneJobs.active.get() != null) MicrophoneJobs.stop() else LocalJobs.gate.requestStop()
                            notice = "已要求停止；已完成字幕會保留，目前原生辨識完成前無法開始新工作"
                        }) { Text("停止") }
                    }
                    if (notice.isNotEmpty()) Text(notice)
                    when (tab) {
                        0 -> {
                            Text("Checkpoint A：內建模型自動準備，無需下載或匯入。請先用手機內 10–30 秒清晰中英語音驗收。")
                            Text("模型：SenseVoice Small INT8\n來源：APK 內建\n狀態：${state.modelStatus}")
                            if (!state.modelReady && !state.busy) OutlinedButton(onClick = { begin("prepare") }) { Text("重新準備內建模型") }
                            Button(enabled = !state.busy, onClick = { mediaPicker.launch(arrayOf("audio/*", "video/*")) }) { Text("選取本機音訊／影片") }
                            Text(if (uriText.isEmpty()) "尚未選取檔案" else "已選取本機檔案")
                            Button(enabled = canStartTranscription(state.modelReady, state.busy, uriText.isNotEmpty()), onClick = { begin("transcribe", Uri.parse(uriText)) }) { Text("開始本機轉錄") }
                            Text("字幕使用最多 20 秒 PCM 視窗，時間戳為視窗邊界，非逐字對齊；跨視窗詞句與準確度待實機驗收。")
                            if (state.metrics.isNotEmpty()) Text(state.metrics)
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                listOf("txt", "vtt", "srt").forEach { format ->
                                    OutlinedButton(enabled = state.segments.isNotEmpty(), onClick = { exportFormat = format; exporter.launch("Tiger-Standalone.$format") }) { Text(format.uppercase()) }
                                }
                            }
                            Text("已保留 ${state.segments.size} 段")
                            state.segments.takeLast(30).forEach { Text("${Export.timestamp(it.start, '.')}\n${it.text}") }
                        }
                        1 -> {
                            Text("Checkpoint B：手機本機麥克風辨識（3 秒視窗，非真正串流模型）")
                            Text("錄音權限：${if (microphonePermission) "已允許" else "未授予"}")
                            if (!microphonePermission) Button(enabled = !state.busy, onClick = { microphoneRequest.launch(Manifest.permission.RECORD_AUDIO) }) { Text("允許麥克風") }
                            if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                                OutlinedButton(enabled = !state.busy, onClick = { permission.launch(Manifest.permission.POST_NOTIFICATIONS) }) { Text("允許錄音通知（可選）") }
                                Text("未允許通知時，Android 仍在執行中 App 顯示前景工作；畫面上仍可停止。")
                            }
                            Text(microphone.status, color = if (microphone.error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface)
                            Text(if (microphone.recording) "● 錄音中：麥克風正在擷取" else "麥克風未錄音")
                            Text("擷取時間：${"%.1f".format(microphone.timing.captureSeconds)} 秒")
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Button(enabled = microphonePermission && state.modelReady && !state.busy && !microphone.working, onClick = { startMicrophone() }) { Text("開始") }
                                Button(enabled = microphone.working, onClick = { MicrophoneJobs.stop() }) { Text("停止") }
                                OutlinedButton(enabled = !state.busy && !microphone.working && state.segments.isNotEmpty(), onClick = { clearMicrophone = true }) { Text("清除") }
                            }
                            Text("離開此頁、背景化、鎖屏或旋轉會停止錄音，不自動重啟。STOP 先釋放麥克風；目前原生辨識仍需完成，期間模型維持使用中。")
                            Text("停止會捨棄尚未開始辨識的佇列及尾端音訊；請說完後等候文字出現再停止。視窗重疊 0.5 秒，時間戳為擷取視窗，非逐字對齊。")
                            val timing = microphone.timing
                            Text("模型載入 ${microphone.modelLoadMs?.let { "$it ms" } ?: "—"}｜來源 ${microphone.sourceRate ?: 0} Hz → 16000 Hz\n" +
                                "已處理 ${timing.processedWindows} 視窗｜首字 ${timing.firstTextMs?.let { "$it ms" } ?: "—"}\n" +
                                "最近 ${timing.latestMs} ms｜平均 ${"%.0f".format(timing.meanMs)} ms｜RTF ${"%.2f".format(timing.rtf)}\n" +
                                "佇列 ${microphone.queue.depth}/2｜過載丟棄 ${microphone.queue.droppedWindows} 視窗／${microphone.queue.droppedSamples} 取樣\n" +
                                "停止捨棄 ${microphone.queue.stoppedSamples} 取樣｜STOP 至釋放 ${timing.stopReleaseMs?.let { "$it ms" } ?: "—"}\n" +
                                "取樣峰值 PSS ${microphone.peakPssMiB} MiB")
                            if (microphone.queue.droppedWindows > 0) Text("裝置辨識速度跟不上錄音：有音訊被丟棄，字幕不完整", color = MaterialTheme.colorScheme.error)
                            Text("最近字幕：${state.segments.lastOrNull()?.text ?: "尚無文字"}")
                            Text("已保留 ${state.segments.size} 段（與本機檔案共用字幕；停止不清除）")
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                listOf("txt", "vtt", "srt").forEach { format ->
                                    OutlinedButton(enabled = !state.busy && state.segments.isNotEmpty(), onClick = { exportFormat = format; exporter.launch("Tiger-Microphone.$format") }) { Text(format.uppercase()) }
                                }
                            }
                            state.segments.takeLast(30).forEach { Text("${Export.timestamp(it.start, '.')}\n${it.text}") }
                        }
                        2 -> {
                            Text("裝置音訊：本機播放音訊字幕（視窗辨識，非真正串流）")
                            Text("部分應用程式依 Android 安全政策禁止擷取播放音訊。")
                            Text("僅擷取允許的播放音訊，不使用麥克風替代；不錄製畫面。每次開始都需要 Android 系統授權。")
                            Text("Android 14 以上會請求預設整個螢幕的系統授權，僅用於播放音訊；授權畫面由系統決定，部分裝置仍可能顯示選擇畫面。")
                            Text("狀態：${playbackPhase.label}", style = MaterialTheme.typography.titleMedium)
                            Text(playback.status, color = if (playback.error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface)
                            if (playbackNotice.isNotEmpty()) Text(playbackNotice, color = MaterialTheme.colorScheme.error)
                            if (!microphonePermission) Button(enabled = !state.busy, onClick = { playbackPermission.launch(Manifest.permission.RECORD_AUDIO) }) { Text("允許裝置音訊所需錄音權限") }
                            if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED)
                                OutlinedButton(enabled = !state.busy, onClick = { permission.launch(Manifest.permission.POST_NOTIFICATIONS) }) { Text("允許擷取通知（建議）") }
                            Text("未允許通知時，Android 仍在執行中 App 顯示前景工作；可返回此頁停止。")
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Button(enabled = microphonePermission && state.modelReady && !state.busy && projectionRequest < 0 &&
                                    playbackPhase in setOf(PlaybackPhase.IDLE, PlaybackPhase.STOPPED, PlaybackPhase.ERROR), onClick = { startPlayback() }) { Text("開始") }
                                Button(enabled = playback.working || playbackPhase in setOf(PlaybackPhase.CONSENT, PlaybackPhase.STARTING), onClick = { PlaybackJobs.stop() }) { Text("停止") }
                                OutlinedButton(enabled = !state.busy && !playback.working && playbackPhase != PlaybackPhase.CONSENT && state.segments.isNotEmpty(), onClick = { clearPlayback = true }) { Text("清除") }
                            }
                            Text(if (playback.recording) "● 裝置音訊擷取中" else "裝置音訊未擷取")
                            Text("授權後可切換至播放器；背景擷取持續並顯示系統指示／前景通知。通知可停止。鎖屏、移除 App 工作或系統撤銷授權會停止；返回不自動重啟。")
                            Text("先用允許擷取的本機播放器驗收。若沒有字幕，請確認正在播放；安靜片段或來源禁止擷取皆可能沒有可用音訊。")
                            val timing = playback.timing
                            Text("擷取 ${"%.1f".format(timing.captureSeconds)} 秒｜模型載入 ${playback.modelLoadMs?.let { "$it ms" } ?: "—"}\n" +
                                "來源 ${playback.sourceRate ?: 0} Hz → 16000 Hz｜已處理 ${timing.processedWindows} 視窗\n" +
                                "首字 ${timing.firstTextMs?.let { "$it ms" } ?: "—"}｜最近 ${timing.latestMs} ms｜平均 ${"%.0f".format(timing.meanMs)} ms\n" +
                                "RTF ${"%.2f".format(timing.rtf)}｜佇列 ${playback.queue.depth}/2｜丟棄 ${playback.queue.droppedWindows} 視窗／${playback.queue.droppedSamples} 取樣\n" +
                                "停止捨棄 ${playback.queue.stoppedSamples} 取樣｜STOP 至釋放 ${timing.stopReleaseMs?.let { "$it ms" } ?: "—"}\n" +
                                "取樣峰值 PSS ${playback.peakPssMiB} MiB")
                            Text("停止先關閉擷取／系統投影；目前辨識完成前模型仍使用中。佇列及未完成尾端會捨棄並計數，已完成字幕保留。")
                            if (playback.queue.droppedWindows > 0) Text("辨識速度跟不上播放，字幕可能缺漏", color = MaterialTheme.colorScheme.error)
                            Text("最近字幕：${state.segments.lastOrNull()?.text ?: "尚無文字"}")
                            Text("已保留 ${state.segments.size} 段（三種來源共用字幕）")
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                listOf("txt", "vtt", "srt").forEach { format ->
                                    OutlinedButton(enabled = !state.busy && state.segments.isNotEmpty(), onClick = { exportFormat = format; exporter.launch("Tiger-Playback.$format") }) { Text(format.uppercase()) }
                                }
                            }
                            state.segments.takeLast(30).forEach { Text("${Export.timestamp(it.start, '.')}\n${it.text}") }
                        }
                        3 -> YouTubeScreen(traditional)
                        4 -> {
                            Text("模型：SenseVoice Small INT8\n來源：APK 內建\n狀態：${state.modelStatus}")
                            Text("模型與詞彙共 239,549,735 bytes，已包含在 APK。首次準備至少需要 293 MiB 可用內部空間；安裝前建議保留 1 GiB。")
                            Text("在 App 私有空間準備並驗證大小及 SHA256；已驗證的模型不重複複製，轉錄前再次校驗。無需網路或外部儲存權限。")
                            Text("SenseVoice Small INT8：FunAudioLLM／Alibaba Group；ONNX 轉換：csukuangfj／sherpa-onnx。模型適用獨立 FunASR Model License，非引擎授權。")
                            TextButton(onClick = { scope.launch {
                                licenseText = withContext(Dispatchers.IO) {
                                    runCatching { assets.open("licenses/SenseVoice-FunASR-MODEL-LICENSE.txt").bufferedReader().use { it.readText() } }
                                        .getOrDefault("無法讀取內建授權文件，請重新安裝完整 APK")
                                }
                            } }) { Text("閱讀內建模型授權（離線）") }
                            OutlinedButton(enabled = !state.busy, onClick = { begin("prepare") }) { Text("校驗／重新準備內建模型") }
                        }
                        5 -> {
                            Text("本版本只執行手機 CPU 推論；無伺服器網址、連線金鑰、LLM 或說話者分離。內建模型、本機檔案、麥克風及裝置音訊皆不依賴網路；僅 YouTube 資料取得需要網路權限。")
                            Row { Checkbox(traditional, { traditional = it }, enabled = !state.busy); Text("使用本機 ICU 轉換繁體中文（可能需校正專有名詞）") }
                            Text("單一工作／2 條推論執行緒／PCM 視窗上限 20 秒／檔案上限 60 分鐘／工作上限 30 分鐘。嚴重過熱時合作式停止。不重啟中斷工作；保留已完成字幕。")
                            Text("引擎 sherpa-onnx 1.13.8（Apache-2.0）／ONNX Runtime（MIT）。效能數字為本機執行時量測，不是 PC 基準。")
                        }
                    }
                }
            }
        }
        licenseText?.let { text -> AlertDialog(onDismissRequest = { licenseText = null }, title = { Text("FunASR Model License") },
            text = { Text(text, modifier = Modifier.verticalScroll(rememberScrollState())) },
            confirmButton = { TextButton(onClick = { licenseText = null }) { Text("關閉") } }) }
        if (clearMicrophone) AlertDialog(onDismissRequest = { clearMicrophone = false }, title = { Text("清除保留字幕？") },
            text = { Text("這會清除本 App 共用的本機檔案／麥克風／裝置音訊字幕，不刪除原始媒體或已匯出檔案。") },
            confirmButton = { TextButton(onClick = { clearMicrophone = false; scope.launch(Dispatchers.IO) { MicrophoneJobs.clear(this@MainActivity) } }) { Text("清除") } },
            dismissButton = { TextButton(onClick = { clearMicrophone = false }) { Text("取消") } })
        if (clearPlayback) AlertDialog(onDismissRequest = { clearPlayback = false }, title = { Text("清除共用字幕？") },
            text = { Text("清除本機檔案／麥克風／裝置音訊保留字幕；不刪除媒體或已匯出檔案。") },
            confirmButton = { TextButton(onClick = { clearPlayback = false; scope.launch(Dispatchers.IO) { PlaybackJobs.clear(this@MainActivity) } }) { Text("清除") } },
            dismissButton = { TextButton(onClick = { clearPlayback = false }) { Text("取消") } })
    }
}

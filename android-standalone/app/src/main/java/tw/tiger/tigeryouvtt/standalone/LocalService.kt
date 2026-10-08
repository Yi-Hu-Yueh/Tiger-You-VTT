package tw.tiger.tigeryouvtt.standalone

import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.icu.text.Transliterator
import android.net.Uri
import android.os.*
import android.util.AtomicFile
import com.k2fsa.sherpa.onnx.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

data class LocalState(val busy: Boolean = false, val status: String = "就緒", val segments: List<TranscriptSegment> = emptyList(), val metrics: String = "",
    val modelReady: Boolean = false, val modelStatus: String = "正在準備內建模型...")
object LocalJobs {
    val gate = JobGate()
    val state = MutableStateFlow(LocalState())
    private val initialized = AtomicBoolean(false)
    fun initialize(context: Context) {
        if (!initialized.compareAndSet(false, true)) return
        runCatching {
            val file = File(context.noBackupFilesDir, "transcript.json")
            if (!file.isFile || file.length() > 4_000_000) return@runCatching
            val json = JSONObject(AtomicFile(file).openRead().bufferedReader().use { it.readText() })
            val rows = json.getJSONArray("segments")
            val segments = (0 until rows.length()).map { i -> rows.getJSONObject(i).let { TranscriptSegment(it.getDouble("start"), it.getDouble("end"), it.getString("text")) } }
            Export.render(segments, "txt") // validate chronology before recovery
            state.value = LocalState(status = "已復原上次字幕；中斷的工作不會自動重跑", segments = segments)
        }
    }
    fun persist(context: Context) {
        val file = AtomicFile(File(context.noBackupFilesDir, "transcript.json"))
        val rows = JSONArray()
        state.value.segments.forEach { rows.put(JSONObject().put("start", it.start).put("end", it.end).put("text", it.text)) }
        val data = JSONObject().put("segments", rows).toString().toByteArray(Charsets.UTF_8)
        require(data.size <= 4_000_000) { "字幕容量超過安全上限" }
        val stream = file.startWrite()
        try { stream.write(data); file.finishWrite(stream) } catch (e: Exception) { file.failWrite(stream); throw e }
    }
}

class LocalService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var ownsModel = false
    override fun onBind(intent: Intent?) = null
    override fun onCreate() { super.onCreate(); LocalJobs.initialize(this) }
    @OptIn(ExperimentalCoroutinesApi::class, DelicateCoroutinesApi::class)
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "stop") {
            if (!ownsModel) { stopSelf(); return START_NOT_STICKY }
            LocalJobs.gate.requestStop(); LocalJobs.state.value = LocalJobs.state.value.copy(status = "停止中：等待目前模型載入／單一視窗完成"); return START_NOT_STICKY
        }
        if (intent == null || !LocalJobs.gate.start()) return START_NOT_STICKY
        ownsModel = true
        val action = intent.action ?: ""
        val old = LocalJobs.state.value
        LocalJobs.state.value = old.copy(busy = true, status = "準備中")
        try {
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel("local-asr", "本機轉錄", NotificationManager.IMPORTANCE_LOW))
            val stop = PendingIntent.getService(this, 1, Intent(this, LocalService::class.java).setAction("stop"), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val open = PendingIntent.getActivity(this, 2, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val notification = Notification.Builder(this, "local-asr").setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle("Tiger-You-VTT 獨立模式").setContentText("本機工作執行中；可隨時停止")
                .setContentIntent(open).setOngoing(true).addAction(Notification.Action.Builder(null, "停止", stop).build()).build()
            val type = if (Build.VERSION.SDK_INT >= 35 && action == "transcribe") ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROCESSING else ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            startForeground(101, notification, type)
        } catch (_: Exception) {
            LocalJobs.state.value = old.copy(status = "無法啟動前景工作，請保持 App 開啟並檢查系統權限")
            ownsModel = false; LocalJobs.gate.finish(); stopSelf(); return START_NOT_STICKY
        }
        scope.launch(start = CoroutineStart.ATOMIC) {
            val started = SystemClock.elapsedRealtime()
            fun checkWork() {
                LocalJobs.gate.check()
                check(SystemClock.elapsedRealtime() - started < 30 * 60_000) { "已達 30 分鐘安全執行上限" }
                if (Build.VERSION.SDK_INT >= 29) check(getSystemService(PowerManager::class.java).currentThermalStatus < PowerManager.THERMAL_STATUS_SEVERE) { "裝置過熱，已停止工作並保留字幕" }
            }
            try {
                val models = ModelStore(this@LocalService)
                when (action) {
                    "prepare" -> {
                        LocalJobs.state.value = LocalJobs.state.value.copy(modelReady = false, modelStatus = "正在準備內建模型...")
                        models.prepareBundled(::checkWork, { _, done, total ->
                            LocalJobs.state.value = LocalJobs.state.value.copy(modelStatus = "正在準備內建模型... ${done * 100 / total}%")
                        })
                        LocalJobs.state.value = LocalJobs.state.value.copy(modelReady = true, modelStatus = "已就緒")
                    }
                    "transcribe" -> LocalTranscriber(this@LocalService, ::status).transcribe(requireNotNull(intent.data), intent.getBooleanExtra("traditional", true), ::checkWork)
                    else -> error("未知工作")
                }
                status(if (action == "transcribe" && LocalJobs.state.value.segments.isEmpty()) "完成：未辨識到文字，請確認音軌" else "完成")
            } catch (e: ModelPreparationFailure) { status(e.userMessage)
            } catch (_: StopRequested) { status("已停止；保留已完成的字幕")
            } catch (_: SecurityException) { status("檔案存取權限被拒絕，請重新選取本機檔案")
            } catch (_: OutOfMemoryError) { status("記憶體不足，請關閉其他 App 或改用較短檔案")
            } catch (_: LinkageError) { status("此裝置無法載入原生 ASR，請回報 CPU／Android 版本")
            } catch (_: Exception) { status("工作失敗：媒體解碼或模型載入失敗，請確認檔案及內部儲存空間後重試")
            } finally {
                if (!LocalJobs.state.value.modelReady) {
                    LocalJobs.state.value = LocalJobs.state.value.copy(modelStatus = "尚未就緒：${LocalJobs.state.value.status}")
                }
                runCatching { LocalJobs.persist(this@LocalService) }
                withContext(NonCancellable + Dispatchers.Main.immediate) {
                    LocalJobs.state.value = LocalJobs.state.value.copy(busy = false)
                    ownsModel = false; LocalJobs.gate.finish()
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf(startId)
                }
            }
        }
        return START_NOT_STICKY
    }
    private fun status(text: String) { LocalJobs.state.value = LocalJobs.state.value.copy(status = text) }
    override fun onTimeout(startId: Int, fgsType: Int) { if (ownsModel) LocalJobs.gate.requestStop(); stopForeground(STOP_FOREGROUND_REMOVE); stopSelf(startId) }
    override fun onDestroy() { if (ownsModel) LocalJobs.gate.requestStop(); scope.cancel(); super.onDestroy() }
}

/** The accepted local-file decoder/recognizer, shared by local files and downloaded YouTube audio. */
internal class LocalTranscriber(private val context: Context, private val status: (String) -> Unit,
    private val completed: (List<TranscriptSegment>) -> Unit = {},
    private val phase: (String) -> Unit = {}) {
    fun transcribe(uri: Uri, traditional: Boolean, checkWork: () -> Unit) {
        phase("asr")
        val store = ModelStore(context)
        status("校驗模型")
        LocalJobs.state.value = LocalJobs.state.value.copy(modelReady = false, modelStatus = "校驗內建模型中")
        store.validate(checkWork)
        checkWork()
        LocalJobs.state.value = LocalState(busy = true, status = "載入本機模型", modelReady = true, modelStatus = "已就緒")
        LocalJobs.persist(context)
        val started = SystemClock.elapsedRealtime()
        val recognizer = OfflineRecognizer(config = OfflineRecognizerConfig(modelConfig = OfflineModelConfig(
            senseVoice = OfflineSenseVoiceModelConfig(model = confinedFile(store.root, "model.int8.onnx").absolutePath, language = "auto"),
            tokens = confinedFile(store.root, "tokens.txt").absolutePath, numThreads = 2, provider = "cpu", debug = false
        )))
        val loaded = SystemClock.elapsedRealtime()
        var peakPssKb = 0
        var audioSeconds = 0.0
        var firstTextMs: Long? = null
        try {
            val converter = if (traditional) Transliterator.getInstance("Simplified-Traditional") else null
            phase("decode")
            MediaDecoder(context).decode(uri, checkWork) { pcm, start, end ->
                checkWork(); status("本機辨識：${end.toInt()} 秒；停止需等待目前視窗")
                phase("asr")
                val stream = recognizer.createStream()
                val text = try {
                    stream.acceptWaveform(pcm, 16000)
                    recognizer.decode(stream)
                    recognizer.getResult(stream).text.replace(Regex("<\\|.*?\\|>"), "").trim()
                } finally { stream.release() }
                checkWork()
                audioSeconds += pcm.size / 16000.0
                if (text.isNotBlank()) {
                    if (firstTextMs == null) firstTextMs = SystemClock.elapsedRealtime() - started
                    val result = TranscriptSegment(start, end, converter?.transliterate(text) ?: text)
                    val segments = LocalJobs.state.value.segments + result
                    require(segments.sumOf { it.text.length } <= 1_000_000) { "字幕容量達上限" }
                    LocalJobs.state.value = LocalJobs.state.value.copy(segments = segments)
                    phase("storage")
                    LocalJobs.persist(context)
                    completed(segments)
                }
                val memory = Debug.MemoryInfo(); Debug.getMemoryInfo(memory); peakPssKb = maxOf(peakPssKb, memory.totalPss)
                val wall = (SystemClock.elapsedRealtime() - loaded) / 1000.0
                LocalJobs.state.value = LocalJobs.state.value.copy(metrics = "模型載入 ${loaded - started} ms｜處理 ${"%.1f".format(wall)} s｜音訊 ${"%.1f".format(audioSeconds)} s｜RTF ${"%.2f".format(wall / audioSeconds)}｜取樣峰值 PSS ${peakPssKb / 1024} MiB｜首段文字 ${firstTextMs?.let { "$it ms" } ?: "尚無文字"}（檔案，非 live）")
                phase("decode")
            }
        } finally { recognizer.release() }
    }
}

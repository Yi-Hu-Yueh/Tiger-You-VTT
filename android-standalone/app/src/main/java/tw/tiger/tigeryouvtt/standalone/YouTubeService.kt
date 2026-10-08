package tw.tiger.tigeryouvtt.standalone

import android.app.*
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update

data class YouTubeState(val busy: Boolean = false, val status: String = "尚未開始", val error: Boolean = false,
    val results: List<YouTubeItem> = emptyList(), val video: YouTubeVideo? = null,
    val provenance: String = "尚未取得", val segments: List<TranscriptSegment> = emptyList(), val metrics: String = "", val diagnostic: String = "")
object YouTubeJobs {
    val state = MutableStateFlow(YouTubeState())
    val active = AtomicBoolean()
    val cancel = AtomicReference<YouTubeCancel?>(null)
    val ownsModel = AtomicBoolean()
    fun stop() {
        cancel.get()?.stop()
        if (ownsModel.get()) LocalJobs.gate.requestStop()
        if (active.get()) state.update { it.copy(status = "取消中；等待目前原生視窗安全完成") }
    }
}

class YouTubeService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var owned: YouTubeCancel? = null
    override fun onBind(intent: Intent?) = null
    override fun onCreate() { super.onCreate(); LocalJobs.initialize(this) }
    @OptIn(ExperimentalCoroutinesApi::class, DelicateCoroutinesApi::class)
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "stop") { YouTubeJobs.stop(); if (owned == null) stopSelf(); return START_NOT_STICKY }
        if (intent?.action !in setOf("search", "inspect", "retrieve")) { if (owned == null) stopSelf(); return START_NOT_STICKY }
        if (!YouTubeJobs.active.compareAndSet(false, true)) return START_NOT_STICKY
        val action = intent!!.action!!
        val cancel = YouTubeCancel(); owned = cancel; YouTubeJobs.cancel.set(cancel)
        YouTubeJobs.state.update { it.copy(busy = true, error = false, diagnostic = "", status = if (action == "search") "搜尋中" else "解析影片") }
        try { foreground(false) }
        catch (_: Exception) { finish("無法啟動 YouTube 前景工作，請保持 App 開啟後重試", true); return START_NOT_STICKY }
        scope.launch(start = CoroutineStart.ATOMIC) {
            val started = SystemClock.elapsedRealtime()
            var finalStatus = "已完成"; var failed = false
            try {
                cancel.check()
                val cache = YouTubeCache(File(cacheDir, "youtube-audio")); cache.prepare()
                val source = NewPipeYouTubeSource(YouTubeHttp(cancel, observer = { info ->
                    YouTubeJobs.state.update { it.copy(diagnostic = info.safeText()) }
                }), cache)
                when (action) {
                    "search" -> {
                        val results = source.search(intent.getStringExtra("query") ?: "")
                        cancel.check(); YouTubeJobs.state.update { it.copy(results = results) }
                        if (results.isEmpty()) finalStatus = "找不到搜尋結果"
                    }
                    "inspect" -> {
                        val result = source.video(intent.getStringExtra("url") ?: "")
                        cancel.check(); YouTubeJobs.state.update { it.copy(video = result) }
                    }
                    "retrieve" -> {
                        val workflow = YouTubeWorkflow(source, cache, LocalJobs.gate, { LocalJobs.state.value.modelReady }, cancel)
                        val segments = workflow.retrieve(intent.getStringExtra("url") ?: "",
                            status = { status(it) }, video = { result -> YouTubeJobs.state.update { it.copy(video = result) } },
                            provenance = { p -> YouTubeJobs.state.update { it.copy(provenance = p) } },
                            modelOwnership = { owns ->
                                YouTubeJobs.ownsModel.set(owns)
                                LocalJobs.state.update { it.copy(busy = owns) }
                            }) { file, check ->
                            foreground(true)
                            fun checkWork() {
                                check()
                                if (SystemClock.elapsedRealtime() - started > 30 * 60_000) throw YouTubeFailure("已達 30 分鐘安全上限")
                                if (getSystemService(PowerManager::class.java).currentThermalStatus >= PowerManager.THERMAL_STATUS_SEVERE)
                                    throw YouTubeFailure("裝置過熱，已停止辨識並保留字幕")
                            }
                            YouTubeJobs.state.update { it.copy(metrics = "") }
                            var phase = "asr"
                            try { LocalTranscriber(this@YouTubeService, { status("本機辨識中：$it") }, { done ->
                                YouTubeJobs.state.update { it.copy(segments = done, metrics = LocalJobs.state.value.metrics) }
                            }, { next ->
                                phase = next
                                status(if (next == "decode") "解碼音訊" else if (next == "storage") "儲存已完成字幕" else "本機辨識中")
                            }).transcribe(Uri.fromFile(file), intent.getBooleanExtra("traditional", true), ::checkWork)
                            } catch (e: StopRequested) { throw e
                            } catch (e: YouTubeFailure) { throw e
                            } catch (_: Exception) { throw DownloadFailure(when (phase) {
                                "decode" -> DownloadProblem.DECODE
                                "storage" -> DownloadProblem.STORAGE
                                else -> DownloadProblem.ASR
                            }) }
                            YouTubeJobs.state.update { it.copy(metrics = LocalJobs.state.value.metrics) }
                            LocalJobs.state.value.segments
                        }
                        YouTubeJobs.state.update { it.copy(segments = segments) }
                        if (segments.isEmpty()) finalStatus = "已完成：未辨識到文字"
                    }
                }
                cancel.check()
            } catch (_: StopRequested) { finalStatus = "已取消；已完成字幕保留"
            } catch (_: CancellationException) { finalStatus = "已取消；已完成字幕保留"
            } catch (_: OutOfMemoryError) { finalStatus = "記憶體不足，已停止並保留字幕"; failed = true
            } catch (_: LinkageError) { finalStatus = "無法載入擷取器或本機模型，請回報 Android 版本"; failed = true
            } catch (e: Exception) {
                if (cancel.isStopped()) finalStatus = "已取消；已完成字幕保留"
                else { finalStatus = "失敗：${youtubeError(e)}"; failed = true }
            } finally {
                cancel.stop()
                withContext(NonCancellable + Dispatchers.Main.immediate) { finish(finalStatus, failed) }
            }
        }
        return START_NOT_STICKY
    }
    private fun status(value: String) { YouTubeJobs.state.update { it.copy(status = value) } }
    private fun foreground(asr: Boolean) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("youtube", "YouTube 本機字幕工作", NotificationManager.IMPORTANCE_LOW))
        val stop = PendingIntent.getService(this, 104, Intent(this, YouTubeService::class.java).setAction("stop"), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val open = PendingIntent.getActivity(this, 1104, Intent(this, MainActivity::class.java).putExtra("capture-tab", 3), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification = Notification.Builder(this, "youtube").setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("Tiger-You-VTT YouTube").setContentText(if (asr) "本機語音辨識中" else "取得 YouTube 資料／音訊中")
            .setOngoing(true).setContentIntent(open).addAction(Notification.Action.Builder(null, "停止", stop).build()).build()
        val type = if (asr && Build.VERSION.SDK_INT >= 35) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROCESSING else ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        startForeground(104, notification, type)
    }
    private fun finish(message: String, error: Boolean) {
        YouTubeJobs.cancel.set(null); owned = null
        YouTubeJobs.state.update { it.copy(busy = false, status = message, error = error) }
        YouTubeJobs.active.set(false)
        stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
    }
    override fun onTaskRemoved(rootIntent: Intent?) { YouTubeJobs.stop(); super.onTaskRemoved(rootIntent) }
    override fun onTimeout(startId: Int, fgsType: Int) { YouTubeJobs.stop() }
    override fun onDestroy() { if (owned != null) YouTubeJobs.stop(); scope.cancel(); super.onDestroy() }
}

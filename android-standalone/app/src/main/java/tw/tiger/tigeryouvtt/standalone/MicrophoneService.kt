package tw.tiger.tigeryouvtt.standalone

import android.Manifest
import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.icu.text.Transliterator
import android.os.*
import com.k2fsa.sherpa.onnx.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

data class MicrophoneState(val working: Boolean = false, val recording: Boolean = false,
    val status: String = "麥克風已停止", val error: Boolean = false, val modelLoadMs: Long? = null,
    val sourceRate: Int? = null, val timing: MicTiming = MicTiming(),
    val queue: MicQueueStats = MicQueueStats(0, 0, 0, 0), val peakPssMiB: Int = 0)

object MicrophoneJobs {
    val state = MutableStateFlow(MicrophoneState())
    val foregroundVisible = AtomicBoolean(false)
    val tabVisible = AtomicBoolean(false)
    val active = AtomicReference<MicPipeline?>(null)
    fun visible() = foregroundVisible.get() && tabVisible.get()
    fun stop(reason: String = "已要求停止錄音；等待目前辨識完成，已完成字幕會保留") {
        active.get()?.let { pipeline ->
            pipeline.requestStop()
            state.update { it.copy(status = reason) }
        }
    }
    fun clear(context: Context) {
        // Claim the same gate even for clear, preventing a queued START from racing persistence.
        if (!LocalJobs.gate.start()) return
        try {
            LocalJobs.state.update { it.copy(segments = clearMicTranscript(false, it.segments), metrics = "") }
            LocalJobs.persist(context)
            state.value = MicrophoneState(status = "已清除保留字幕")
        } catch (_: Exception) { state.update { it.copy(error = true, status = "清除字幕儲存失敗，請確認內部空間") }
        } finally { LocalJobs.gate.finish() }
    }
}

open class MicrophoneService : Service() {
    // Playback overrides only capture/consent/lifecycle hooks; this ASR consumer stays shared.
    protected open val liveState get() = MicrophoneJobs.state
    protected open val activePipeline get() = MicrophoneJobs.active
    protected open val sourceName = "麥克風"
    protected open val notificationId = 102
    protected open val channelId = "microphone-asr"
    protected open val notificationTab = 1
    protected open val notificationIcon = android.R.drawable.ic_btn_speak_now
    protected open fun startError(intent: Intent): String? = microphoneStartError(hasPermission(), LocalJobs.state.value.modelReady, LocalJobs.gate.busy(), MicrophoneJobs.visible())
    protected open fun createPipeline() = MicPipeline()
    protected open fun prepareCapture(intent: Intent) {}
    protected open fun releaseCapture() {}
    protected open fun captureChanged(recording: Boolean) {}
    protected open fun captureFinished(error: Boolean) {}
    protected open fun stopCapture() { MicrophoneJobs.stop() }
    protected open fun openAudioSource(): MicPcmSource = AudioRecordSource.open(this@MicrophoneService)
    protected open fun shouldInfer(window: MicWindow) = true
    protected open fun captureGuard(pipeline: MicPipeline) {
        if (!MicrophoneJobs.visible()) { pipeline.requestStop(); throw StopRequested() }
    }
    protected open fun startTypedForeground(notification: Notification) {
        if (Build.VERSION.SDK_INT >= 30) startForeground(notificationId, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        else startForeground(notificationId, notification)
    }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var owned: MicPipeline? = null
    override fun onBind(intent: Intent?) = null
    override fun onCreate() { super.onCreate(); LocalJobs.initialize(this) }
    @OptIn(ExperimentalCoroutinesApi::class, DelicateCoroutinesApi::class)
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "stop") {
            stopCapture()
            if (owned == null) stopSelf()
            return START_NOT_STICKY
        }
        if (intent?.action != "start") { if (owned == null) stopSelf(); return START_NOT_STICKY }
        if (owned != null) return START_NOT_STICKY // Duplicate intent must not mutate the active job.
        val error = startError(intent)
        if (error != null || !LocalJobs.gate.start()) {
            liveState.update { it.copy(error = true, status = error ?: "模型使用中，請稍後重試") }
            captureFinished(true)
            stopSelf(); return START_NOT_STICKY
        }
        val pipeline = createPipeline()
        owned = pipeline
        activePipeline.set(pipeline)
        liveState.value = MicrophoneState(working = true, status = "校驗／載入本機模型；尚未擷取")
        LocalJobs.state.update { it.copy(busy = true) }
        try { foreground("${sourceName}準備中；可由通知停止", start = true); prepareCapture(intent) }
        catch (_: Exception) {
            releaseCapture()
            finish(pipeline, "無法啟動${sourceName}前景擷取，請重新授權並保持 App 開啟", true)
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf(); return START_NOT_STICKY
        }
        // Enter try/finally even if onDestroy cancels before the IO dispatcher starts this job.
        scope.launch(start = CoroutineStart.ATOMIC) {
            var recognizer: OfflineRecognizer? = null
            var finalStatus = "${sourceName}已停止；保留已完成字幕"
            var failed = false
            try {
                guard(pipeline)
                val store = ModelStore(this@MicrophoneService)
                LocalJobs.state.update { it.copy(modelReady = false, modelStatus = "校驗內建模型中") }
                store.validate { pipeline.check() }
                LocalJobs.state.update { it.copy(modelReady = true, modelStatus = "已就緒") }
                guard(pipeline)
                val started = SystemClock.elapsedRealtime()
                val model = OfflineRecognizer(config = OfflineRecognizerConfig(modelConfig = OfflineModelConfig(
                    senseVoice = OfflineSenseVoiceModelConfig(model = confinedFile(store.root, "model.int8.onnx").absolutePath, language = "auto"),
                    tokens = confinedFile(store.root, "tokens.txt").absolutePath, numThreads = 2, provider = "cpu", debug = false)))
                recognizer = model
                liveState.update { it.copy(modelLoadMs = SystemClock.elapsedRealtime() - started) }
                guard(pipeline)
                val transcript = MicTranscript(LocalJobs.state.value.segments)
                val converter = if (intent.getBooleanExtra("traditional", true)) Transliterator.getInstance("Simplified-Traditional") else null
                pipeline.run(sourceFactory = {
                    openAudioSource().also { source ->
                        liveState.update { it.copy(sourceRate = source.sampleRate) }
                    }
                }, infer = { window ->
                    if (!shouldInfer(window)) return@run ""
                    val stream = model.createStream()
                    try {
                        stream.acceptWaveform(window.pcm, 16000)
                        model.decode(stream)
                        val text = model.getResult(stream).text.replace(Regex("<\\|.*?\\|>"), "").trim()
                        converter?.transliterate(text) ?: text
                    } finally { stream.release() }
                }, result = { window, text ->
                    transcript.append(window, text)
                    LocalJobs.state.update { it.copy(segments = transcript.segments) }
                    LocalJobs.persist(this@MicrophoneService)
                    // PSS collection can be slow: never block the capture thread on it.
                    val memory = Debug.MemoryInfo(); Debug.getMemoryInfo(memory)
                    liveState.update { it.copy(peakPssMiB = maxOf(it.peakPssMiB, memory.totalPss / 1024)) }
                }, capture = { recording ->
                    captureChanged(recording)
                    liveState.update { it.copy(recording = recording,
                        status = if (recording) "● ${sourceName}擷取中（本機視窗辨識）" else "擷取已停止；等待目前辨識釋放") }
                    foreground(if (recording) "● ${sourceName}擷取中；按停止結束" else "擷取已停止；正在釋放本機模型")
                }, metrics = {
                    liveState.update { it.copy(timing = pipeline.timing(), queue = pipeline.queue.stats()) }
                }, guard = { guard(pipeline) })
            } catch (_: StopRequested) {
                // Cooperative STOP is normal; never clear completed text.
            } catch (e: MicrophoneFailure) { finalStatus = e.userMessage; failed = true
            } catch (_: SecurityException) { finalStatus = "${sourceName}授權已被撤銷，請重新授權"; failed = true
            } catch (_: OutOfMemoryError) { finalStatus = "記憶體不足，已停止錄音並保留字幕"; failed = true
            } catch (_: LinkageError) { finalStatus = "無法載入本機 ASR，請回報 Android／CPU 版本"; failed = true
            } catch (_: Exception) { finalStatus = "${sourceName}辨識失敗，已保留字幕；請檢查模型、擷取及儲存空間"; failed = true
            } finally {
                pipeline.requestStop()
                releaseCapture()
                try { recognizer?.release() } catch (_: Throwable) { finalStatus = "原生模型釋放失敗，請重新啟動 App"; failed = true }
                runCatching { LocalJobs.persist(this@MicrophoneService) }
                withContext(NonCancellable + Dispatchers.Main.immediate) {
                    finish(pipeline, finalStatus, failed)
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf() // Atomic with gate release on main; no new START can interleave here.
                }
            }
        }
        return START_NOT_STICKY
    }
    protected fun hasPermission() = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
    private fun guard(pipeline: MicPipeline) {
        pipeline.check()
        captureGuard(pipeline)
        if (!hasPermission()) throw MicrophoneFailure("${sourceName}錄音權限已被撤銷，請重新授權")
        if (getSystemService(PowerManager::class.java).currentThermalStatus >= PowerManager.THERMAL_STATUS_SEVERE)
            throw MicrophoneFailure("裝置過熱，已停止錄音並保留字幕")
    }
    private fun foreground(message: String, start: Boolean = false) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(channelId, "${sourceName}本機辨識", NotificationManager.IMPORTANCE_LOW))
        val stop = PendingIntent.getService(this, notificationId, Intent(this, javaClass).setAction("stop"), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val open = PendingIntent.getActivity(this, notificationId + 1000, Intent(this, MainActivity::class.java).putExtra("capture-tab", notificationTab), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification = Notification.Builder(this, channelId).setSmallIcon(notificationIcon)
            .setContentTitle("Tiger-You-VTT $sourceName").setContentText(message).setOngoing(true).setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "停止", stop).build()).build()
        if (!start) manager.notify(notificationId, notification)
        else startTypedForeground(notification)
    }
    private fun finish(pipeline: MicPipeline, message: String, error: Boolean) {
        liveState.update { it.copy(working = false, recording = false, status = message, error = error,
            timing = pipeline.timing(), queue = pipeline.queue.stats()) }
        LocalJobs.state.update { it.copy(busy = false) }
        activePipeline.compareAndSet(pipeline, null)
        captureFinished(error)
        owned = null
        LocalJobs.gate.finish()
    }
    override fun onTaskRemoved(rootIntent: Intent?) { stopCapture(); super.onTaskRemoved(rootIntent) }
    override fun onDestroy() {
        owned?.requestStop()
        releaseCapture()
        scope.cancel() // JNI unwinds cooperatively; the shared model gate stays owned until finally.
        super.onDestroy()
    }
}

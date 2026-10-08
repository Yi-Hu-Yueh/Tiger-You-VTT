package tw.tiger.tigeryouvtt.standalone

import android.app.Activity
import android.app.Notification
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import java.util.concurrent.atomic.AtomicReference

object PlaybackJobs {
    val consent = PlaybackConsent()
    val phase = MutableStateFlow(PlaybackPhase.IDLE)
    val state = MutableStateFlow(MicrophoneState(status = "尚未開始"))
    val notice = MutableStateFlow("")
    val active = AtomicReference<MicPipeline?>(null)
    val release = AtomicReference<(() -> Unit)?>(null)
    val stopReason = AtomicReference<String?>(null)
    fun publish() { phase.value = consent.current() }
    fun fail(message: String) {
        consent.finish(true); publish()
        state.update { it.copy(working = false, recording = false, error = true, status = message) }
    }
    fun request(ready: Boolean, busy: Boolean): Long? {
        val id = consent.request(ready, busy) ?: return null
        notice.value = ""; stopReason.set(null); publish()
        state.update { it.copy(error = false, status = "等待系統授權") }
        return id
    }
    fun result(id: Long, granted: Boolean): Boolean {
        val accepted = consent.result(id, granted); publish()
        if (!accepted && consent.current() == PlaybackPhase.STOPPED)
            state.update { it.copy(status = "系統授權已取消；未開始擷取", recording = false) }
        return accepted
    }
    fun stop(reason: String = "已停止接受音訊；正在釋放擷取及完成目前辨識") {
        consent.stop(); stopReason.set(reason)
        active.get()?.requestStop()
        release.get()?.invoke()
        if (active.get() == null) consent.finish()
        publish(); state.update { it.copy(status = reason) }
    }
    fun clear(context: Context) {
        if (!LocalJobs.gate.start()) return
        try {
            LocalJobs.state.update { it.copy(segments = clearMicTranscript(false, it.segments), metrics = "") }
            LocalJobs.persist(context)
            state.value = MicrophoneState(status = "已清除保留字幕")
            notice.value = ""
        } catch (_: Exception) { fail("清除字幕失敗，請確認內部儲存空間") }
        finally { LocalJobs.gate.finish() }
    }
}

/** Shares the accepted microphone service's single ASR consumer; only the source differs. */
class PlaybackService : MicrophoneService() {
    override val liveState get() = PlaybackJobs.state
    override val activePipeline get() = PlaybackJobs.active
    override val sourceName = "裝置音訊"
    override val notificationId = 103
    override val channelId = "playback-asr"
    override val notificationTab = 2
    override val notificationIcon = android.R.drawable.ic_media_play
    private var projection: MediaProjection? = null
    private var projectionRelease = CaptureRelease()
    private var screenReceiver: BroadcastReceiver? = null
    override fun createPipeline() = MicPipeline(sourceName = sourceName, initialReadTimeoutMs = 10000)
    override fun startError(intent: Intent): String? {
        playbackStartError(hasPermission(), LocalJobs.state.value.modelReady, LocalJobs.gate.busy())?.let { return it }
        if (!PlaybackJobs.consent.consume(intent.getLongExtra("request-id", -1))) return "授權已過期或重複使用，請重新按開始取得系統授權"
        return null
    }
    override fun startTypedForeground(notification: Notification) {
        startForeground(notificationId, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
    }
    override fun prepareCapture(intent: Intent) {
        // Called only AFTER startForeground; consent is single-use and never persisted.
        projectionRelease = CaptureRelease()
        PlaybackJobs.release.set(::releaseCapture)
        val data = if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra("projection-data", Intent::class.java)
            else @Suppress("DEPRECATION") (intent.getParcelableExtra("projection-data") as? Intent)
        val resultCode = intent.getIntExtra("projection-result", Activity.RESULT_CANCELED)
        intent.removeExtra("projection-data")
        if (resultCode != Activity.RESULT_OK || data == null) throw MicrophoneFailure("未取得系統擷取授權")
        val session = getSystemService(MediaProjectionManager::class.java).getMediaProjection(resultCode, data)
            ?: throw MicrophoneFailure("系統擷取授權無效，請重新開始")
        projection = session
        val callback = object : MediaProjection.Callback() {
            override fun onStop() {
                if (projection === session && !projectionRelease.isClosed())
                    PlaybackJobs.stop("系統已撤銷擷取授權；已停止並保留字幕")
            }
        }
        projectionRelease.install {
            runCatching { session.unregisterCallback(callback) }
            runCatching { session.stop() }
        }
        session.registerCallback(callback, Handler(Looper.getMainLooper()))
        screenReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, event: Intent?) {
                if (event?.action == Intent.ACTION_SCREEN_OFF) PlaybackJobs.stop("螢幕已鎖定／關閉，擷取已停止；請重新授權後開始")
            }
        }.also { receiver ->
            if (Build.VERSION.SDK_INT >= 33) registerReceiver(receiver, IntentFilter(Intent.ACTION_SCREEN_OFF), Context.RECEIVER_NOT_EXPORTED)
            else registerReceiver(receiver, IntentFilter(Intent.ACTION_SCREEN_OFF))
        }
    }
    override fun openAudioSource(): MicPcmSource {
        if (projectionRelease.isClosed()) throw StopRequested()
        return PlaybackAudioSource.open(requireNotNull(projection), ::releaseCapture) { silent ->
            PlaybackJobs.notice.value = if (silent) "目前未收到可擷取的裝置音訊。來源 App 可能禁止播放音訊擷取。" else ""
        }
    }
    override fun shouldInfer(window: MicWindow) = meaningfulPlayback(window.pcm)
    override fun captureGuard(pipeline: MicPipeline) {
        // Explicit foreground capture may continue while the user switches to a media player.
        if (projectionRelease.isClosed()) { pipeline.requestStop(); throw StopRequested() }
    }
    override fun captureChanged(recording: Boolean) {
        if (recording) {
            if (!PlaybackJobs.consent.capturing()) { PlaybackJobs.stop(); throw StopRequested() }
        } else { releaseCapture(); PlaybackJobs.consent.stop() }
        PlaybackJobs.publish()
    }
    override fun stopCapture() { PlaybackJobs.stop() }
    override fun releaseCapture() {
        projectionRelease.close()
        synchronized(this) {
            screenReceiver?.let { runCatching { unregisterReceiver(it) } }
            screenReceiver = null
        }
    }
    override fun captureFinished(error: Boolean) {
        PlaybackJobs.release.set(null)
        projection = null
        PlaybackJobs.consent.finish(error); PlaybackJobs.publish()
        if (!error) PlaybackJobs.stopReason.get()?.let { reason -> liveState.update { it.copy(status = "已停止：$reason") } }
    }
}

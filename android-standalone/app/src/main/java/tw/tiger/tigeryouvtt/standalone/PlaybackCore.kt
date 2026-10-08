package tw.tiger.tigeryouvtt.standalone

import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** Idempotent release even if STOP races resource creation. */
class CaptureRelease {
    private val stopped = AtomicBoolean(false)
    private val action = AtomicReference<(() -> Unit)?>(null)
    fun install(close: () -> Unit) {
        check(action.compareAndSet(null, close))
        if (stopped.get()) action.getAndSet(null)?.invoke()
    }
    fun close() { stopped.set(true); action.getAndSet(null)?.invoke() }
    fun isClosed() = stopped.get()
}

/** Carries incomplete interleaved frames across reads, with no integer overflow. */
class LiveDownmix(private val channels: Int, private val emit: (Short) -> Unit) {
    init { require(channels in 1..2) }
    private var sum = 0
    private var used = 0
    fun accept(sample: Short) {
        sum += sample.toInt(); used++
        if (used == channels) { emit((sum / channels).toShort()); sum = 0; used = 0 }
    }
}

enum class PlaybackPhase(val label: String) {
    IDLE("尚未開始"), CONSENT("等待系統授權"), STARTING("準備擷取"),
    CAPTURING("擷取中"), STOPPING("停止中"), STOPPED("已停止"), ERROR("錯誤")
}

/** Only a request id/phase is retained, NEVER a projection token or result Intent. */
class PlaybackConsent {
    var phase = PlaybackPhase.IDLE; private set
    private var serial = 0L
    private var request: Long? = null
    private var launch: Long? = null
    @Synchronized fun request(ready: Boolean, busy: Boolean): Long? {
        if (!ready || busy || phase in setOf(PlaybackPhase.CONSENT, PlaybackPhase.STARTING, PlaybackPhase.CAPTURING, PlaybackPhase.STOPPING)) return null
        serial++; request = serial; launch = null; phase = PlaybackPhase.CONSENT
        return serial
    }
    @Synchronized fun result(id: Long, granted: Boolean): Boolean {
        if (request != id || phase != PlaybackPhase.CONSENT) return false
        request = null
        phase = if (granted) PlaybackPhase.STARTING else PlaybackPhase.STOPPED
        launch = if (granted) id else null
        return granted
    }
    @Synchronized fun consume(id: Long): Boolean {
        if (launch != id || phase != PlaybackPhase.STARTING) return false
        launch = null
        return true
    }
    @Synchronized fun capturing(): Boolean {
        if (phase != PlaybackPhase.STARTING) return false
        phase = PlaybackPhase.CAPTURING; return true
    }
    @Synchronized fun stop() {
        request = null; launch = null
        phase = if (phase in setOf(PlaybackPhase.STARTING, PlaybackPhase.CAPTURING, PlaybackPhase.STOPPING)) PlaybackPhase.STOPPING else PlaybackPhase.STOPPED
    }
    @Synchronized fun finish(error: Boolean = false) {
        request = null; launch = null; phase = if (error) PlaybackPhase.ERROR else PlaybackPhase.STOPPED
    }
    @Synchronized fun current() = phase
}

fun playbackStartError(permission: Boolean, ready: Boolean, busy: Boolean): String? = when {
    !ready -> "內建模型尚未就緒，請先完成模型準備"
    busy -> "模型使用中，請先停止本機檔案、麥克風或裝置音訊工作"
    !permission -> "裝置音訊擷取需要 Android 錄音權限；此模式不使用麥克風"
    else -> null
}

fun meaningfulPlayback(pcm: FloatArray): Boolean = pcm.isNotEmpty() &&
    pcm.sumOf { it.toDouble() * it } / pcm.size >= 0.000001 // -60 dBFS RMS; not a speech VAD.

/** Recoverable warning after eight seconds below the threshold, never a permanent silence error. */
class PlaybackSilence(private val warning: (Boolean) -> Unit, private val clock: () -> Long) {
    private var lastSignal = clock()
    private var warned = false
    fun accept(buffer: ShortArray, count: Int) {
        require(count in 0..buffer.size)
        val mean = if (count == 0) 0.0 else (0 until count).sumOf { buffer[it].toDouble() * buffer[it] } / count / (32768.0 * 32768)
        val now = clock()
        if (mean >= 0.000001) lastSignal = now
        val next = now - lastSignal >= 8000
        if (next != warned) { warned = next; warning(next) }
    }
}

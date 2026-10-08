package tw.tiger.tigeryouvtt.standalone

import java.util.ArrayDeque
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

class MicrophoneFailure(val userMessage: String) : RuntimeException(userMessage)

fun microphoneStartError(permission: Boolean, ready: Boolean, busy: Boolean, visible: Boolean): String? = when {
    !permission -> "麥克風權限未授予，請允許錄音後再按開始"
    !visible -> "請保持麥克風頁面開啟後再按開始"
    busy -> "模型使用中，請先停止目前工作並等待釋放"
    !ready -> "內建模型尚未就緒，請先完成模型準備"
    else -> null
}

data class MicWindow(val pcm: FloatArray, val startSample: Long, val freshStartSample: Long, val endSample: Long) {
    init {
        require(startSample >= 0 && freshStartSample >= startSample && endSample > freshStartSample)
        require(pcm.size.toLong() == endSample - startSample && pcm.size <= 48000)
    }
    val freshSamples get() = endSample - freshStartSample
}

/** Three seconds initially, then 2.5 new seconds plus 0.5 seconds of context. */
class MicWindows(private val emit: (MicWindow) -> Unit) {
    private val buffer = FloatArray(48000)
    private var used = 0
    var totalSamples = 0L; private set
    private var previousEnd = 0L
    val unsubmittedSamples get() = totalSamples - previousEnd
    fun add(sample: Float) {
        require(sample.isFinite())
        buffer[used++] = sample.coerceIn(-1f, 1f)
        totalSamples++
        if (used == buffer.size) {
            emit(MicWindow(buffer.copyOf(), totalSamples - used, previousEnd, totalSamples))
            previousEnd = totalSamples
            buffer.copyInto(buffer, 0, buffer.size - 8000, buffer.size)
            used = 8000
        }
    }
}

data class MicQueueStats(val depth: Int, val droppedWindows: Long, val droppedSamples: Long, val stoppedSamples: Long)
class MicQueue(private val capacity: Int = 2) {
    init { require(capacity in 1..2) }
    private val pending = ArrayDeque<MicWindow>()
    private var closed = false
    private var droppedWindows = 0L
    private var droppedSamples = 0L
    private var stoppedSamples = 0L
    @Synchronized fun offer(window: MicWindow) {
        if (closed) { stoppedSamples += window.freshSamples; return }
        if (pending.size == capacity) {
            val discarded = pending.removeFirst()
            droppedWindows++; droppedSamples += discarded.freshSamples
        }
        pending.addLast(window)
    }
    @Synchronized fun poll(): MicWindow? = pending.pollFirst()
    @Synchronized fun stop() {
        closed = true
        while (pending.isNotEmpty()) stoppedSamples += pending.removeFirst().freshSamples
    }
    @Synchronized fun discardTail(samples: Long) { require(samples >= 0); stoppedSamples += samples }
    @Synchronized fun stats() = MicQueueStats(pending.size, droppedWindows, droppedSamples, stoppedSamples)
}

/** Anti-alias FIR followed by the existing continuous-phase 16 kHz resampler. */
class MicResampler(sourceRate: Int, emit: (Float) -> Unit) {
    init { require(sourceRate in setOf(16000, 44100, 48000)) }
    private val direct = sourceRate == 16000
    private val converter = Resampler(sourceRate, emit)
    private val history = FloatArray(63)
    private var cursor = 0
    private val taps = DoubleArray(63) { i ->
        val n = i - 31
        val cutoff = 0.45 * 16000 / sourceRate
        (if (n == 0) 2 * cutoff else sin(2 * PI * cutoff * n) / (PI * n)) * (0.54 - 0.46 * cos(2 * PI * i / 62))
    }.let { values -> val sum = values.sum(); values.map { it / sum }.toDoubleArray() }
    fun accept(sample: Short) {
        val value = sample / 32768f
        if (direct) { converter.accept(value); return }
        history[cursor] = value
        var filtered = 0.0
        for (i in taps.indices) filtered += taps[i] * history[(cursor - i + history.size) % history.size]
        cursor = (cursor + 1) % history.size
        converter.accept(filtered.toFloat())
    }
}

/** Exact overlap only; require four CJK characters or two complete Latin words. */
fun deduplicateMicOverlap(previous: String, current: String): String {
    for (size in minOf(previous.length, current.length, 80) downTo 4) {
        val prefix = current.take(size)
        if (!previous.endsWith(prefix)) continue
        val cjk = prefix.count { it.code in 0x3400..0x9fff } >= 4
        val words = prefix.trim().split(Regex("\\s+")).size >= 2 &&
            (size == current.length || !current[size].isLetterOrDigit()) &&
            (size == previous.length || !previous[previous.length - size - 1].isLetterOrDigit())
        if (cjk || words) return current.drop(size).trimStart(' ', '，', ',', '。', '.')
    }
    return current
}

class MicTranscript(initial: List<TranscriptSegment>) {
    var segments: List<TranscriptSegment> = initial.toList(); private set
    private val offset = initial.lastOrNull()?.end ?: 0.0
    private var previousEnd = -1L
    private var previousText = ""
    init { Export.render(initial, "txt") }
    fun append(window: MicWindow, raw: String): TranscriptSegment? {
        val text = raw.replace(Regex("\\s+"), " ").trim()
        val clean = if (previousEnd == window.freshStartSample && window.startSample < window.freshStartSample)
            deduplicateMicOverlap(previousText, text) else text
        previousEnd = window.endSample
        previousText = text
        if (clean.isBlank()) return null
        val start = offset + window.freshStartSample / 16000.0
        val end = offset + window.endSample / 16000.0
        require(start >= (segments.lastOrNull()?.end ?: 0.0))
        if (segments.size >= 10000 || segments.sumOf { it.text.length } + clean.length > 1_000_000)
            throw MicrophoneFailure("字幕容量已達上限，已停止錄音；請匯出並清除後重試")
        val segment = TranscriptSegment(start, end, clean)
        segments = segments + segment
        return segment
    }
}

fun clearMicTranscript(busy: Boolean, existing: List<TranscriptSegment>): List<TranscriptSegment> =
    if (busy) existing else emptyList()

interface MicPcmSource : AutoCloseable {
    val sampleRate: Int
    val channels: Int get() = 1
    fun start()
    /** Nonblocking; zero means no data yet; negative values are capture failures. */
    fun read(buffer: ShortArray): Int
    override fun close()
}

data class MicTiming(val captureSeconds: Double = 0.0, val processedWindows: Int = 0,
    val latestMs: Long = 0, val totalMs: Long = 0, val processedSeconds: Double = 0.0,
    val firstTextMs: Long? = null, val stopReleaseMs: Long? = null) {
    val meanMs get() = if (processedWindows == 0) 0.0 else totalMs.toDouble() / processedWindows
    val rtf get() = if (processedSeconds == 0.0) 0.0 else totalMs / 1000.0 / processedSeconds
}

/** One producer thread and the caller's single inference consumer; no JNI cancellation. */
class MicPipeline(private val clock: () -> Long = { System.nanoTime() / 1_000_000 },
    private val sourceName: String = "麥克風", private val stallTimeoutMs: Long = 400,
    private val initialReadTimeoutMs: Long = stallTimeoutMs) {
    val queue = MicQueue()
    private val stopped = AtomicBoolean(false)
    private val failure = AtomicReference<Throwable?>(null)
    private val stopAt = AtomicReference<Long?>(null)
    private val timing = AtomicReference(MicTiming())
    fun requestStop() { stopAt.compareAndSet(null, clock()); stopped.set(true); queue.stop() }
    fun isStopped() = stopped.get()
    fun check() { if (isStopped()) throw StopRequested() }
    fun timing() = timing.get()

    fun run(sourceFactory: () -> MicPcmSource, infer: (MicWindow) -> String,
        result: (MicWindow, String) -> Unit, capture: (Boolean) -> Unit,
        metrics: () -> Unit, guard: () -> Unit = {}) {
        val done = AtomicBoolean(false)
        var recordingStarted = 0L
        val producer = Thread({
            val windows = MicWindows(queue::offer)
            var source: MicPcmSource? = null
            try {
                check(); guard(); source = sourceFactory(); check(); source.start(); recordingStarted = clock()
                check(); capture(true)
                val resampler = MicResampler(source.sampleRate, windows::add)
                val downmix = LiveDownmix(source.channels, resampler::accept)
                val buffer = ShortArray(source.sampleRate / 50 * source.channels)
                var lastRead = recordingStarted
                var receivedPcm = false
                var lastMetrics = 0L
                var lastGuard = 0L
                while (!isStopped()) {
                    val now = clock()
                    if (now - lastGuard >= 200) { guard(); lastGuard = now }
                    if (now - recordingStarted >= 30 * 60_000) throw MicrophoneFailure("已達 30 分鐘錄音上限，已保留字幕")
                    if (now - lastRead > if (receivedPcm) stallTimeoutMs else initialReadTimeoutMs)
                        throw MicrophoneFailure("${sourceName}讀取中斷或未提供 PCM；請確認來源允許擷取後重新開始")
                    val count = source.read(buffer)
                    if (isStopped()) break
                    if (count < 0 || count > buffer.size) throw MicrophoneFailure("${sourceName}擷取失敗，請確認來源及系統授權後重試")
                    if (count > 0) {
                        receivedPcm = true
                        lastRead = now
                        for (i in 0 until count) { if (isStopped()) break; downmix.accept(buffer[i]) }
                    } else Thread.sleep(5)
                    timing.updateAndGet { it.copy(captureSeconds = windows.totalSamples / 16000.0) }
                    if (now - lastMetrics >= 200) { metrics(); lastMetrics = now }
                }
            } catch (e: StopRequested) { requestStop()
            } catch (e: Throwable) { failure.compareAndSet(null, e); requestStop()
            } finally {
                try { source?.close() } catch (e: Throwable) { failure.compareAndSet(null, e) }
                queue.discardTail(windows.unsubmittedSamples)
                stopAt.get()?.let { at -> timing.updateAndGet { it.copy(stopReleaseMs = (clock() - at).coerceAtLeast(0)) } }
                done.set(true)
                try { capture(false); metrics() } catch (e: Throwable) { failure.compareAndSet(null, e); requestStop() }
            }
        }, "tiger-microphone-capture")
        producer.start()
        try {
            while (!isStopped() && !done.get()) {
                val window = queue.poll()
                if (window == null) { Thread.sleep(10); continue }
                if (isStopped()) { queue.discardTail(window.freshSamples); break }
                try { guard() } catch (e: Throwable) { queue.discardTail(window.freshSamples); throw e }
                val started = clock()
                val text = infer(window)
                // A completed in-flight result is retained even when STOP arrived during JNI.
                result(window, text)
                val elapsed = (clock() - started).coerceAtLeast(0)
                timing.updateAndGet { it.copy(processedWindows = it.processedWindows + 1,
                    latestMs = elapsed, totalMs = it.totalMs + elapsed,
                    processedSeconds = it.processedSeconds + window.freshSamples / 16000.0,
                    firstTextMs = it.firstTextMs ?: if (text.isNotBlank()) (clock() - recordingStarted).coerceAtLeast(0) else null) }
                metrics()
            }
        } finally {
            requestStop()
            producer.join() // Independent of JNI: capture closes on its own thread as soon as STOP is set.
        }
        failure.get()?.let { throw it }
    }
}

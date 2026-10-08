package tw.tiger.tigeryouvtt.standalone

import java.io.File
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.roundToLong

data class TranscriptSegment(val start: Double, val end: Double, val text: String) {
    init { require(start.isFinite() && end.isFinite() && start >= 0 && end > start) }
}
object Export {
    fun timestamp(seconds: Double, separator: Char): String {
        val ms = (seconds * 1000).roundToLong().coerceAtLeast(0)
        return String.format(Locale.ROOT, "%02d:%02d:%02d%c%03d", ms / 3600000, ms / 60000 % 60, ms / 1000 % 60, separator, ms % 1000)
    }
    fun render(segments: List<TranscriptSegment>, format: String): String {
        require(segments.zipWithNext().all { (a, b) -> b.start >= a.end })
        val clean = segments.map { it.copy(text = it.text.replace(Regex("\\s+"), " ").trim()) }.filter { it.text.isNotBlank() }
        if (format == "txt") return clean.joinToString("\n") { it.text }
        require(format == "vtt" || format == "srt")
        val separator = if (format == "vtt") '.' else ','
        return (if (format == "vtt") "WEBVTT\n\n" else "") + clean.mapIndexed { i, s ->
            (if (format == "srt") "${i + 1}\n" else "") + "${timestamp(s.start, separator)} --> ${timestamp(s.end, separator)}\n${s.text}\n\n"
        }.joinToString("")
    }
}
class StopRequested : RuntimeException()
class JobGate {
    private val active = AtomicBoolean(false)
    private val stop = AtomicBoolean(false)
    fun start(): Boolean { if (!active.compareAndSet(false, true)) return false; stop.set(false); return true }
    fun requestStop() { stop.set(true) }
    fun check() { if (stop.get()) throw StopRequested() }
    fun finish() { active.set(false) }
    fun busy() = active.get()
}
fun confinedFile(root: File, name: String): File {
    require(name in setOf("model.int8.onnx", "tokens.txt")) { "Unknown model file" }
    val path = File(root, name).canonicalFile
    require(path.parentFile == root.canonicalFile) { "Model path outside storage" }
    return path
}

/** Fixed-capacity, backpressured PCM windows, never an unbounded producer queue. */
class PcmWindows(private val capacity: Int = 16000 * 20, private val consume: (FloatArray, Double, Double) -> Unit) {
    private val buffer = FloatArray(capacity)
    private var used = 0
    private var total = 0L
    init { require(capacity in 1..16000 * 20) }
    fun add(sample: Float) {
        require(sample.isFinite()) { "PCM 包含無效取樣值" }
        buffer[used++] = sample.coerceIn(-1f, 1f)
        total++
        require(total <= 16000L * 3600) { "媒體上限為 60 分鐘" }
        if (used == capacity) flush()
    }
    fun flush() {
        if (used == 0) return
        consume(buffer.copyOf(used), (total - used) / 16000.0, total / 16000.0)
        used = 0
    }
}

fun validatePcmFormat(rate: Int, channels: Int, encoding: Int) {
    require(rate in 8000..192000 && channels in 1..8 && encoding in setOf(2, 4)) { "不支援此 PCM 格式／聲道／取樣率" }
}
fun validateAudioTimestamp(actual: Double, expected: Double) {
    require(actual.isFinite() && expected.isFinite() && kotlin.math.abs(actual - expected) < 0.25) { "音軌時間戳不連續，無法保證字幕同步" }
}

/** Linear streaming resampler; keeps phase across decoder buffers. */
class Resampler(private val sourceRate: Int, private val emit: (Float) -> Unit) {
    private var index = -1L
    private var next = 0.0
    private var previous = 0f
    init { require(sourceRate in 8000..192000) }
    fun accept(sample: Float) {
        index++
        while (next <= index) {
            val fraction = (next - (index - 1)).coerceIn(0.0, 1.0).toFloat()
            emit(if (index == 0L) sample else previous + (sample - previous) * fraction)
            next += sourceRate / 16000.0
        }
        previous = sample
    }
}

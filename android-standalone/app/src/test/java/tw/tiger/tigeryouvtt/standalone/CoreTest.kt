package tw.tiger.tigeryouvtt.standalone

import org.junit.Assert.*
import org.junit.Test
import org.junit.Rule
import org.junit.rules.TemporaryFolder
import java.io.File
import java.security.MessageDigest

class CoreTest {
    @get:Rule val directory = TemporaryFolder()
    private val segments = listOf(TranscriptSegment(0.0, 1.234, "你好\n世界"), TranscriptSegment(1.234, 3.0, "Hello"))
    @Test fun txtExport() { assertEquals("你好 世界\nHello", Export.render(segments, "txt")) }
    @Test fun vttExport() { assertEquals("WEBVTT\n\n00:00:00.000 --> 00:00:01.234\n你好 世界\n\n00:00:01.234 --> 00:00:03.000\nHello\n\n", Export.render(segments, "vtt")) }
    @Test fun srtExport() { assertTrue(Export.render(segments, "srt").startsWith("1\n00:00:00,000 --> 00:00:01,234\n")) }
    @Test fun hoursDoNotWrap() { assertEquals("25:00:00.001", Export.timestamp(90000.001, '.')) }
    @Test fun millisCarry() { assertEquals("00:01:00.000", Export.timestamp(59.9999, '.')) }
    @Test fun emptyVttHasHeader() { assertEquals("WEBVTT\n\n", Export.render(emptyList(), "vtt")) }
    @Test(expected = IllegalArgumentException::class) fun invalidSegment() { TranscriptSegment(2.0, 1.0, "bad") }
    @Test(expected = IllegalArgumentException::class) fun nanSegment() { TranscriptSegment(Double.NaN, 1.0, "bad") }
    @Test(expected = IllegalArgumentException::class) fun negativeSegment() { TranscriptSegment(-1.0, 1.0, "bad") }
    @Test(expected = IllegalArgumentException::class) fun nonMonotonicRejected() { Export.render(segments.reversed(), "srt") }
    @Test(expected = IllegalArgumentException::class) fun overlappingRejected() { Export.render(listOf(TranscriptSegment(0.0, 2.0, "a"), TranscriptSegment(1.0, 3.0, "b")), "vtt") }
    @Test fun boundedWindowsKeepOffsets() {
        val chunks = mutableListOf<Triple<Int, Double, Double>>()
        val windows = PcmWindows(16000) { pcm, start, end -> chunks += Triple(pcm.size, start, end) }
        repeat(40000) { windows.add(0.5f) }; windows.flush()
        assertEquals(listOf(Triple(16000, 0.0, 1.0), Triple(16000, 1.0, 2.0), Triple(8000, 2.0, 2.5)), chunks)
    }
    @Test(expected = IllegalArgumentException::class) fun oversizedWindowRejected() { PcmWindows(320001) { _, _, _ -> } }
    @Test fun resamplingPreservesDuration() {
        var samples = 0
        val resampler = Resampler(48000) { assertEquals(0.5f, it, 0.001f); samples++ }
        repeat(48000) { resampler.accept(0.5f) }
        assertEquals(16000, samples)
    }
    @Test fun resampling44100PreservesDuration() {
        var samples = 0
        val resampler = Resampler(44100) { samples++ }
        repeat(44100) { resampler.accept(0f) }
        assertEquals(16000, samples)
    }
    @Test(expected = IllegalArgumentException::class) fun invalidRate() { Resampler(0) {} }
    @Test fun singleJobAndRestart() {
        val gate = JobGate()
        assertTrue(gate.start()); assertFalse(gate.start()); assertTrue(gate.busy())
        gate.finish(); assertFalse(gate.busy()); assertTrue(gate.start())
    }
    @Test(expected = StopRequested::class) fun cooperativeCancellation() { val gate = JobGate(); gate.start(); gate.requestStop(); gate.check() }
    @Test fun cancellationDoesNotLeakToNextJob() { val gate = JobGate(); gate.start(); gate.requestStop(); gate.finish(); gate.start(); gate.check() }
    @Test fun pathIsConfined() { assertEquals(File(directory.root, "tokens.txt"), confinedFile(directory.root, "tokens.txt")) }
    @Test(expected = IllegalArgumentException::class) fun traversalRejected() { confinedFile(directory.root, "../secret") }
    @Test(expected = IllegalArgumentException::class) fun absolutePathRejected() { confinedFile(directory.root, "C:/model.int8.onnx") }

    private val bytes = "synthetic model".toByteArray()
    private fun part() = ModelPart("model.int8.onnx", bytes.size.toLong(), MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) })
    private fun store() = ModelStore(null, directory.root, listOf(part()))
    @Test fun modelMissing() { assertFalse(store().installed()) }
    @Test(expected = IllegalArgumentException::class) fun missingModelFailsBeforeNativeLoad() { store().validate {} }
    @Test fun validImportAndStateRecovery() {
        var progress = 0L
        store().install(part(), bytes.inputStream(), {}) { done, _ -> progress = done }
        assertEquals(bytes.size.toLong(), progress)
        assertTrue(store().installed()); store().validate {}
        assertFalse(File(directory.root, "model.int8.onnx.part").exists())
    }
    @Test fun corruptImportPreservesExisting() {
        val store = store(); store.install(part(), bytes.inputStream(), {}) { _, _ -> }
        assertThrows(IllegalArgumentException::class.java) { store.install(part(), ByteArray(bytes.size).inputStream(), {}) { _, _ -> } }
        store.validate {}
    }
    @Test fun stoppedImportCleansPartial() {
        assertThrows(StopRequested::class.java) { store().install(part(), bytes.inputStream(), { throw StopRequested() }) { _, _ -> } }
        assertFalse(File(directory.root, "model.int8.onnx.part").exists()); assertFalse(store().installed())
    }
    @Test fun tooLargeImportRejected() {
        assertThrows(IllegalArgumentException::class.java) { store().install(part(), ByteArray(bytes.size + 1).inputStream(), {}) { _, _ -> } }
        assertFalse(store().installed())
    }
    @Test fun deletionConfinedToModel() {
        val retained = File(directory.root, "transcript.json").apply { writeText("keep") }
        store().install(part(), bytes.inputStream(), {}) { _, _ -> }; store().delete()
        assertFalse(store().installed()); assertEquals("keep", retained.readText())
    }
    @Test fun truncatedModelMissing() { File(directory.root, "model.int8.onnx").writeBytes(byteArrayOf(1)); assertFalse(store().installed()) }
    @Test fun corruptSameSizeDetected() {
        File(directory.root, "model.int8.onnx").writeBytes(ByteArray(bytes.size))
        assertThrows(IllegalArgumentException::class.java) { store().validate {} }
    }
    @Test fun directoryNotModel() { File(directory.root, "model.int8.onnx").mkdir(); assertFalse(store().installed()) }
    @Test(expected = IllegalArgumentException::class) fun corruptPcmRejected() { PcmWindows { _, _, _ -> }.add(Float.NaN) }
    @Test(expected = IllegalArgumentException::class) fun invalidPcmEncodingRejected() { validatePcmFormat(48000, 2, 99) }
    @Test(expected = IllegalArgumentException::class) fun invalidChannelCountRejected() { validatePcmFormat(48000, 0, 2) }
    @Test fun supportedPcmAccepted() { validatePcmFormat(48000, 2, 2); validatePcmFormat(44100, 1, 4) }
    @Test(expected = IllegalArgumentException::class) fun discontinuousMediaRejected() { validateAudioTimestamp(20.0, 10.0) }
    @Test fun codecRoundingTolerance() { validateAudioTimestamp(1.001, 1.0) }
}

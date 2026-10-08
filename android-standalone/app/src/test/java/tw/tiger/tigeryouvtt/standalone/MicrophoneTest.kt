package tw.tiger.tigeryouvtt.standalone

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.PI
import kotlin.math.sin
import kotlin.math.sqrt

class MicrophoneTest {
    private fun window(start: Long = 0, fresh: Long = start, end: Long = start + 48000) = MicWindow(FloatArray((end - start).toInt()), start, fresh, end)
    private fun source(name: String) = File("src/main/java/tw/tiger/tigeryouvtt/standalone/$name").readText()
    private class FakeSource(val failStart: Boolean = false, val failRead: Boolean = false) : MicPcmSource {
        override val sampleRate = 16000
        val started = AtomicBoolean(false)
        val closed = CountDownLatch(1)
        val reads = AtomicInteger()
        override fun start() { if (failStart) throw MicrophoneFailure("初始化失敗"); started.set(true) }
        override fun read(buffer: ShortArray): Int {
            if (failRead) return -6
            reads.incrementAndGet(); buffer.fill(1000); Thread.sleep(1)
            return buffer.size
        }
        override fun close() { started.set(false); closed.countDown() }
    }
    private fun runAsync(pipeline: MicPipeline, source: MicPcmSource, infer: (MicWindow) -> String = { "hello world" },
        result: (MicWindow, String) -> Unit = { _, _ -> }, capture: (Boolean) -> Unit = {}, guard: () -> Unit = {}): Pair<Thread, AtomicReference<Throwable?>> {
        val error = AtomicReference<Throwable?>(null)
        val thread = Thread {
            try { pipeline.run({ source }, infer, result, capture, {}, guard) }
            catch (e: Throwable) { error.set(e) }
        }
        thread.start()
        return thread to error
    }
    @Test fun permissionDenied() { assertTrue(microphoneStartError(false, true, false, true)!!.contains("權限")) }
    @Test fun permissionGrantedAndReady() { assertNull(microphoneStartError(true, true, false, true)) }
    @Test fun hiddenStartRejected() { assertNotNull(microphoneStartError(true, true, false, false)) }
    @Test fun missingModelRejected() { assertTrue(microphoneStartError(true, false, false, true)!!.contains("尚未就緒")) }
    @Test fun modelBusyRejected() { assertTrue(microphoneStartError(true, true, true, true)!!.contains("使用中")) }
    @Test fun duplicateStartSharesLocalMediaGate() {
        val gate = JobGate(); assertTrue(gate.start())
        assertNotNull(microphoneStartError(true, true, gate.busy(), true))
        assertFalse(gate.start()); gate.finish(); assertTrue(gate.start())
    }
    @Test fun windowLengthOverlapAndCaptureOffsets() {
        val windows = mutableListOf<MicWindow>(); val builder = MicWindows(windows::add)
        repeat(128000) { builder.add(0.1f) }
        assertEquals(3, windows.size)
        assertEquals(listOf(0L, 40000L, 80000L), windows.map { it.startSample })
        assertEquals(listOf(0L, 48000L, 88000L), windows.map { it.freshStartSample })
        assertEquals(listOf(48000L, 88000L, 128000L), windows.map { it.endSample })
        assertEquals(0L, builder.unsubmittedSamples)
    }
    @Test fun tailIsBoundedAndAccounted() {
        val builder = MicWindows {}; repeat(64000) { builder.add(0f) }
        assertEquals(16000L, builder.unsubmittedSamples)
    }
    @Test fun queueDropsOldestAndStaysBounded() {
        val queue = MicQueue()
        queue.offer(window()); queue.offer(window(40000, 48000, 88000)); queue.offer(window(80000, 88000, 128000))
        assertEquals(2, queue.stats().depth); assertEquals(1L, queue.stats().droppedWindows)
        assertEquals(48000L, queue.stats().droppedSamples); assertEquals(40000L, queue.poll()!!.startSample)
    }
    @Test fun overlapNotDoubleCountedOnOverflow() {
        val queue = MicQueue(1)
        queue.offer(window(40000, 48000, 88000)); queue.offer(window(80000, 88000, 128000))
        assertEquals(40000L, queue.stats().droppedSamples)
    }
    @Test fun stopClosesQueueAndCountsTail() {
        val queue = MicQueue(); queue.offer(window()); queue.stop(); queue.discardTail(123)
        queue.offer(window(40000, 48000, 88000))
        assertNull(queue.poll()); assertEquals(0, queue.stats().depth); assertEquals(88123L, queue.stats().stoppedSamples)
    }
    @Test fun queueRejectsUnboundedCapacity() { assertThrows(IllegalArgumentException::class.java) { MicQueue(100) } }
    @Test fun timestampsMonotonicDespiteOverlap() {
        val transcript = MicTranscript(emptyList())
        transcript.append(window(), "你好世界")
        transcript.append(window(40000, 48000, 88000), "你好世界今天晴天")
        assertEquals(0.0, transcript.segments[0].start, 0.0)
        assertEquals(3.0, transcript.segments[1].start, 0.0)
        assertEquals(5.5, transcript.segments[1].end, 0.0)
        assertEquals("今天晴天", transcript.segments[1].text)
        Export.render(transcript.segments, "srt")
    }
    @Test fun timestampsRetainDropGaps() {
        val transcript = MicTranscript(emptyList()); transcript.append(window(), "first")
        transcript.append(window(80000, 88000, 128000), "last")
        assertEquals(5.5, transcript.segments[1].start, 0.0)
    }
    @Test fun newSessionAppendsWithoutRewritingPreviousTimes() {
        val previous = TranscriptSegment(1.0, 7.0, "existing")
        val transcript = MicTranscript(listOf(previous)); transcript.append(window(), "new")
        assertEquals(previous, transcript.segments.first()); assertEquals(7.0, transcript.segments.last().start, 0.0)
    }
    @Test fun negativeCaptureTimeRejected() { assertThrows(IllegalArgumentException::class.java) { window(-1) } }
    @Test fun backwardsCaptureTimeRejected() { assertThrows(IllegalArgumentException::class.java) { window(0, 10, 5) } }
    @Test fun conservativeEnglishDedup() { assertEquals("again", deduplicateMicOverlap("say hello world", "hello world again")) }
    @Test fun conservativeCjkDedup() { assertEquals("今天", deduplicateMicOverlap("大家你好世界", "你好世界今天")) }
    @Test fun shortOrNonexactRepeatPreserved() {
        assertEquals("yes yes", deduplicateMicOverlap("yes", "yes yes"))
        assertEquals("hello WORLD again", deduplicateMicOverlap("hello world", "hello WORLD again"))
    }
    @Test fun noDedupAcrossMissingWindow() {
        val transcript = MicTranscript(emptyList()); transcript.append(window(), "hello world")
        transcript.append(window(80000, 88000, 128000), "hello world again")
        assertEquals("hello world again", transcript.segments.last().text)
    }
    @Test fun emptyRecognitionAddsNoSegment() { val transcript = MicTranscript(emptyList()); assertNull(transcript.append(window(), " ")); assertTrue(transcript.segments.isEmpty()) }
    @Test fun clearOnlyWhenIdle() {
        val segments = listOf(TranscriptSegment(0.0, 3.0, "keep"))
        assertEquals(segments, clearMicTranscript(true, segments)); assertTrue(clearMicTranscript(false, segments).isEmpty())
    }
    @Test fun canonicalTxtExport() { val t = MicTranscript(emptyList()); t.append(window(), "hello"); assertEquals("hello", Export.render(t.segments, "txt")) }
    @Test fun canonicalVttExport() { val t = MicTranscript(emptyList()); t.append(window(), "hello"); assertTrue(Export.render(t.segments, "vtt").contains("00:00:00.000 --> 00:00:03.000")) }
    @Test fun canonicalSrtExport() { val t = MicTranscript(emptyList()); t.append(window(), "hello"); assertTrue(Export.render(t.segments, "srt").startsWith("1\n00:00:00,000 --> 00:00:03,000")) }
    @Test fun allSupportedRatesHaveContinuousSampleAccounting() {
        for (rate in listOf(16000, 44100, 48000)) {
            var count = 0; val resampler = MicResampler(rate) { count++ }
            repeat(rate) { resampler.accept(1000) }; assertEquals(16000, count)
        }
    }
    @Test fun directPcm16Normalization() {
        val samples = mutableListOf<Float>(); val r = MicResampler(16000, samples::add)
        r.accept(Short.MIN_VALUE); r.accept(0); r.accept(Short.MAX_VALUE)
        assertEquals(-1f, samples.first(), 0f); assertEquals(0f, samples[1], 0f); assertTrue(samples[2] < 1f)
    }
    @Test fun antiAliasFilterRejectsHighFrequency() {
        fun rms(frequency: Int): Double {
            val samples = mutableListOf<Float>(); val r = MicResampler(48000, samples::add)
            repeat(48000) { r.accept((sin(2 * PI * frequency * it / 48000) * 30000).toInt().toShort()) }
            return sqrt(samples.drop(100).map { it.toDouble() * it }.average())
        }
        assertTrue(rms(1000) > 0.5); assertTrue(rms(12000) < 0.02)
    }
    @Test fun unsupportedRateRejected() { assertThrows(IllegalArgumentException::class.java) { MicResampler(22050) {} } }
    @Test fun stopReleasesCaptureDuringBlockedInferenceAndRetainsResult() {
        val pipeline = MicPipeline(); val source = FakeSource()
        val entered = CountDownLatch(1); val unblock = CountDownLatch(1)
        val recording = AtomicBoolean(); val transcript = MicTranscript(emptyList())
        val (thread, error) = runAsync(pipeline, source, infer = { entered.countDown(); check(unblock.await(5, TimeUnit.SECONDS)); "retained" },
            result = { w, text -> transcript.append(w, text) }, capture = recording::set)
        try {
            assertTrue(entered.await(3, TimeUnit.SECONDS)); assertTrue(recording.get())
            val started = System.nanoTime(); pipeline.requestStop()
            assertTrue("capture must close independently of native inference", source.closed.await(1, TimeUnit.SECONDS))
            val elapsed = (System.nanoTime() - started) / 1_000_000
            println("Host fake-source STOP-to-close: $elapsed ms (not a phone measurement)")
            assertTrue(thread.isAlive)
        } finally { unblock.countDown(); pipeline.requestStop(); thread.join(3000) }
        assertFalse(thread.isAlive); assertNull(error.get()); assertFalse(recording.get())
        assertEquals("retained", transcript.segments.single().text)
        assertEquals(1, pipeline.timing().processedWindows)
    }
    @Test fun initializationFailureClosesSourceAndNeverRecords() {
        val pipeline = MicPipeline(); val source = FakeSource(failStart = true); val recording = AtomicBoolean()
        val (thread, error) = runAsync(pipeline, source, capture = recording::set)
        thread.join(3000); assertFalse(thread.isAlive); assertNotNull(error.get()); assertFalse(recording.get())
        assertEquals(0L, source.closed.count)
    }
    @Test fun captureFailureClosesAndClearsRecording() {
        val source = FakeSource(failRead = true); val recording = AtomicBoolean()
        val (thread, error) = runAsync(MicPipeline(), source, capture = recording::set)
        thread.join(3000); assertFalse(thread.isAlive); assertNotNull(error.get()); assertFalse(recording.get())
        assertEquals(0L, source.closed.count)
    }
    @Test fun nativeFailureClosesCapture() {
        val source = FakeSource(); val recording = AtomicBoolean()
        val (thread, error) = runAsync(MicPipeline(), source, infer = { throw IllegalStateException("native secret path") }, capture = recording::set)
        thread.join(3000); assertFalse(thread.isAlive); assertNotNull(error.get()); assertFalse(recording.get())
        assertEquals(0L, source.closed.count)
    }
    @Test fun stopBeforeStartNeverOpensMicrophone() {
        val pipeline = MicPipeline(); pipeline.requestStop()
        pipeline.run({ error("must not open source") }, { "" }, { _, _ -> }, {}, {})
        assertEquals(0, pipeline.timing().processedWindows)
    }
    @Test fun factoryFailureNeverLeavesRecordingActive() {
        val recording = AtomicBoolean()
        assertThrows(MicrophoneFailure::class.java) {
            MicPipeline().run({ throw MicrophoneFailure("麥克風不可用") }, { "" }, { _, _ -> }, recording::set, {})
        }
        assertFalse(recording.get())
    }
    @Test fun readStallFailsClosedAndReleasesSource() {
        val clock = java.util.concurrent.atomic.AtomicLong(1000)
        val closed = AtomicBoolean(); val recording = AtomicBoolean()
        val source = object : MicPcmSource {
            override val sampleRate = 16000
            override fun start() {}
            override fun read(buffer: ShortArray): Int { clock.addAndGet(450); return 0 }
            override fun close() { closed.set(true) }
        }
        assertThrows(MicrophoneFailure::class.java) {
            MicPipeline(clock::get).run({ source }, { "" }, { _, _ -> }, recording::set, {})
        }
        assertTrue(closed.get()); assertFalse(recording.get())
    }
    @Test fun lifecycleGuardStopsCapture() {
        val visible = AtomicBoolean(true); val recording = AtomicBoolean(); val source = FakeSource()
        val pipeline = MicPipeline()
        val (thread, error) = runAsync(pipeline, source, capture = recording::set,
            guard = { if (!visible.get()) { pipeline.requestStop(); throw StopRequested() } })
        try {
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2)
            while (!recording.get() && System.nanoTime() < deadline) Thread.sleep(2)
            assertTrue(recording.get()); visible.set(false)
            assertTrue(source.closed.await(1, TimeUnit.SECONDS))
        } finally { pipeline.requestStop(); thread.join(3000) }
        assertFalse(thread.isAlive); assertFalse(recording.get())
        assertTrue(error.get() == null || error.get() is StopRequested)
    }
    @Test fun permissionAndForegroundManifest() {
        val manifest = File("src/main/AndroidManifest.xml").readText()
        assertTrue(manifest.contains("android.permission.RECORD_AUDIO"))
        assertTrue(manifest.contains("android.permission.FOREGROUND_SERVICE_MICROPHONE"))
        assertTrue(manifest.contains("android:foregroundServiceType=\"microphone\""))
        assertOfflineCoreHasNoNetworkDependencies()
        // Playback now owns its separate projection service; microphone stays microphone-only.
        val microphoneService = Regex("<service[^>]*android:name=\".MicrophoneService\"[^>]*/>").find(manifest)!!.value
        assertFalse(microphoneService.contains("mediaProjection"))
        assertTrue(microphoneService.contains("android:foregroundServiceType=\"microphone\""))
    }
    @Test fun uiAndServiceOwnershipWiring() {
        val ui = source("MainActivity.kt"); val service = source("MicrophoneService.kt")
        assertTrue(ui.contains("override fun onStop()")); assertTrue(ui.contains("DisposableEffect(tab)"))
        assertTrue(ui.contains("MicrophoneJobs.stop(")); assertTrue(ui.contains("RequestPermission()"))
        assertTrue(service.contains("LocalJobs.gate.start()")); assertTrue(service.contains("LocalJobs.gate.finish()"))
        assertTrue(service.contains("START_NOT_STICKY")); assertTrue(service.contains("recognizer?.release()"))
        assertFalse(service.contains("e.message")); assertFalse(service.contains("java.net"))
        assertTrue(service.contains("LocalJobs.persist(")); assertTrue(service.contains("stream.release()"))
        assertTrue(service.contains("MicTranscript(LocalJobs.state.value.segments)"))
    }
    @Test fun localMediaAndModelPreparationRemainWired() {
        val service = source("LocalService.kt")
        assertTrue(service.contains("MediaDecoder(context).decode")); assertTrue(service.contains("models.prepareBundled"))
        assertTrue(service.contains("LocalTranscriber(this@LocalService, ::status).transcribe"))
        assertTrue(service.contains("store.validate(checkWork)")); assertTrue(service.contains("LocalJobs.gate.start()"))
    }
    @Test fun metricsArithmetic() {
        val timing = MicTiming(processedWindows = 2, totalMs = 1000, processedSeconds = 5.5)
        assertEquals(500.0, timing.meanMs, 0.0); assertEquals(1.0 / 5.5, timing.rtf, 0.00001)
    }
}

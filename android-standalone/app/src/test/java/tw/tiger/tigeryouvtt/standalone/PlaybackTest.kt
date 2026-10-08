package tw.tiger.tigeryouvtt.standalone

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

class PlaybackTest {
    private fun source(name: String) = File("src/main/java/tw/tiger/tigeryouvtt/standalone/$name").readText()
    private fun window(start: Long = 0, fresh: Long = start, end: Long = start + 48000) = MicWindow(FloatArray((end - start).toInt()), start, fresh, end)
    @Test fun consentAcceptedNotYetCapturing() {
        val consent = PlaybackConsent(); val id = consent.request(true, false)!!
        assertEquals(PlaybackPhase.CONSENT, consent.current())
        assertTrue(consent.result(id, true)); assertEquals(PlaybackPhase.STARTING, consent.current())
        assertTrue(consent.consume(id)); assertTrue(consent.capturing()); assertEquals(PlaybackPhase.CAPTURING, consent.current())
    }
    @Test fun deniedConsentNeverStarts() {
        val consent = PlaybackConsent(); val id = consent.request(true, false)!!
        assertFalse(consent.result(id, false)); assertFalse(consent.consume(id)); assertFalse(consent.capturing())
        assertEquals(PlaybackPhase.STOPPED, consent.current())
    }
    @Test fun permissionRequiredEvenForPlayback() { assertTrue(playbackStartError(false, true, false)!!.contains("此模式不使用麥克風")) }
    @Test fun modelMissingRejectedBeforeConsent() { assertNull(PlaybackConsent().request(false, false)); assertNotNull(playbackStartError(true, false, false)) }
    @Test fun modelBusyRejectedBeforeConsent() { assertNull(PlaybackConsent().request(true, true)); assertNotNull(playbackStartError(true, true, true)) }
    @Test fun duplicateConsentRejected() { val c = PlaybackConsent(); c.request(true, false); assertNull(c.request(true, false)) }
    @Test fun consentCanBeConsumedOnlyOnce() {
        val c = PlaybackConsent(); val id = c.request(true, false)!!; c.result(id, true)
        assertTrue(c.consume(id)); assertFalse(c.consume(id))
    }
    @Test fun lateConsentAfterStopDiscarded() {
        val c = PlaybackConsent(); val id = c.request(true, false)!!; c.stop()
        assertFalse(c.result(id, true)); assertFalse(c.consume(id)); assertEquals(PlaybackPhase.STOPPED, c.current())
    }
    @Test fun duplicateStartWhileCapturingRejected() {
        val c = PlaybackConsent(); val id = c.request(true, false)!!; c.result(id, true); c.consume(id); c.capturing()
        assertNull(c.request(true, false)); assertFalse(c.result(id, true))
    }
    @Test fun stopStateWaitsForFinalInference() {
        val c = PlaybackConsent(); val id = c.request(true, false)!!; c.result(id, true); c.consume(id); c.capturing()
        c.stop(); assertEquals(PlaybackPhase.STOPPING, c.current()); assertNull(c.request(true, false))
        c.finish(); assertEquals(PlaybackPhase.STOPPED, c.current()); assertNotNull(c.request(true, false))
    }
    @Test fun revocationRequiresNewConsent() {
        val c = PlaybackConsent(); val id = c.request(true, false)!!; c.result(id, true); c.consume(id); c.capturing()
        c.stop(); c.finish(); assertFalse(c.consume(id)); val next = c.request(true, false)!!; assertTrue(next != id)
    }
    @Test fun processRestartHasNoConsentOrActiveState() { val c = PlaybackConsent(); assertEquals(PlaybackPhase.IDLE, c.current()); assertFalse(c.consume(1)) }
    @Test fun failedInitializationCanBeRetriedWithNewConsent() {
        val c = PlaybackConsent(); val id = c.request(true, false)!!; c.result(id, true); c.consume(id); c.finish(true)
        assertEquals(PlaybackPhase.ERROR, c.current()); assertNotNull(c.request(true, false)); assertFalse(c.consume(id))
    }
    @Test fun microphoneAndPlaybackMutuallyExclusive() {
        val gate = JobGate(); assertTrue(gate.start())
        assertNotNull(playbackStartError(true, true, gate.busy())); assertFalse(gate.start())
        gate.finish(); assertTrue(gate.start()); assertNotNull(microphoneStartError(true, true, gate.busy(), true))
    }
    @Test fun localFileAndPlaybackMutuallyExclusive() {
        val gate = JobGate(); assertTrue(gate.start()); assertNull(PlaybackConsent().request(true, gate.busy()))
        gate.finish(); assertNotNull(PlaybackConsent().request(true, gate.busy()))
    }
    @Test fun stereoAverageAndReadBoundaryContinuity() {
        val result = mutableListOf<Short>(); val mix = LiveDownmix(2, result::add)
        mix.accept(3000); assertTrue(result.isEmpty()); mix.accept(1000)
        mix.accept(-3000); mix.accept(1000); assertEquals(listOf<Short>(2000, -1000), result)
    }
    @Test fun stereoCannotOverflow() {
        val result = mutableListOf<Short>(); val mix = LiveDownmix(2, result::add)
        repeat(2) { mix.accept(Short.MAX_VALUE) }; repeat(2) { mix.accept(Short.MIN_VALUE) }
        assertEquals(listOf(Short.MAX_VALUE, Short.MIN_VALUE), result)
    }
    @Test fun monoPathUnchanged() { val values = mutableListOf<Short>(); val mix = LiveDownmix(1, values::add); mix.accept(123); assertEquals(listOf<Short>(123), values) }
    @Test fun invalidChannelCountRejected() { assertThrows(IllegalArgumentException::class.java) { LiveDownmix(3) {} } }
    @Test fun stereo44100To16000() { verifyRate(44100) }
    @Test fun stereo48000To16000() { verifyRate(48000) }
    private fun verifyRate(rate: Int) {
        val samples = mutableListOf<Float>(); val r = MicResampler(rate, samples::add); val mix = LiveDownmix(2, r::accept)
        repeat(rate) { mix.accept(2000); mix.accept(0) }
        assertEquals(16000, samples.size); assertEquals(1000 / 32768f, samples.last(), 0.0001f)
    }
    @Test fun boundedPlaybackQueueAndDropAccounting() {
        val q = MicQueue(); q.offer(window()); q.offer(window(40000, 48000, 88000)); q.offer(window(80000, 88000, 128000))
        assertEquals(2, q.stats().depth); assertEquals(1L, q.stats().droppedWindows); assertEquals(48000L, q.stats().droppedSamples)
    }
    @Test fun sampleTimestampsAndDroppedGapRemainMonotonic() {
        val t = MicTranscript(emptyList()); t.append(window(), "first"); t.append(window(80000, 88000, 128000), "second")
        assertEquals(0.0, t.segments.first().start, 0.0); assertEquals(5.5, t.segments.last().start, 0.0)
        assertTrue(t.segments.last().start >= t.segments.first().end)
    }
    @Test fun negativePlaybackTimestampRejected() { assertThrows(IllegalArgumentException::class.java) { window(-1) } }
    @Test fun existingDedupIsShared() {
        val t = MicTranscript(emptyList()); t.append(window(), "hello world")
        t.append(window(40000, 48000, 88000), "hello world again")
        assertEquals("again", t.segments.last().text)
    }
    @Test fun briefSilenceIsNotAnError() {
        val events = mutableListOf<Boolean>(); val time = AtomicLong(0); val s = PlaybackSilence(events::add, time::get)
        time.set(7999); s.accept(ShortArray(320), 320); assertTrue(events.isEmpty())
    }
    @Test fun prolongedSilenceWarnsOnceAndRecovers() {
        val events = mutableListOf<Boolean>(); val time = AtomicLong(0); val s = PlaybackSilence(events::add, time::get)
        time.set(8000); s.accept(ShortArray(320), 320)
        repeat(20) { time.addAndGet(20); s.accept(ShortArray(320), 320) }; assertEquals(listOf(true), events)
        s.accept(ShortArray(320) { 1000 }, 320); assertEquals(listOf(true, false), events)
        time.addAndGet(8000); s.accept(ShortArray(320), 320); assertEquals(listOf(true, false, true), events)
    }
    @Test fun noPcmAlsoGetsPolicyNotice() {
        val events = mutableListOf<Boolean>(); val time = AtomicLong(0); val s = PlaybackSilence(events::add, time::get)
        time.set(8000); s.accept(ShortArray(320), 0); assertEquals(listOf(true), events)
    }
    @Test fun silenceDoesNotInvokeAsr() { assertFalse(meaningfulPlayback(FloatArray(48000))); assertTrue(meaningfulPlayback(FloatArray(48000) { 0.1f })) }
    @Test fun projectionReleaseIsIdempotent() { val r = CaptureRelease(); val n = AtomicInteger(); r.install { n.incrementAndGet() }; r.close(); r.close(); assertEquals(1, n.get()) }
    @Test fun stopRacingProjectionCreationClosesLateResource() { val r = CaptureRelease(); val n = AtomicInteger(); r.close(); r.install { n.incrementAndGet() }; assertEquals(1, n.get()); assertTrue(r.isClosed()) }
    @Test fun stereoPlaybackPcmReachesSingleConsumerAndStopRetainsText() {
        val p = MicPipeline(sourceName = "裝置音訊", stallTimeoutMs = 10000)
        val close = CountDownLatch(1); val entered = CountDownLatch(1); val unblock = CountDownLatch(1)
        val projection = CaptureRelease(); val projectionClosed = AtomicInteger(); projection.install { projectionClosed.incrementAndGet() }
        val recording = AtomicBoolean(); val errors = AtomicReference<Throwable?>(null); val transcript = MicTranscript(emptyList())
        val source = object : MicPcmSource {
            override val sampleRate = 48000; override val channels = 2
            override fun start() {}
            override fun read(buffer: ShortArray): Int { buffer.fill(2000); Thread.sleep(1); return buffer.size }
            override fun close() { projection.close(); close.countDown() }
        }
        val thread = Thread {
            try { p.run({ source }, { w ->
                assertEquals(48000, w.pcm.size); assertEquals(2000 / 32768f, w.pcm.last(), 0.0001f)
                entered.countDown(); check(unblock.await(5, TimeUnit.SECONDS)); "保留播放字幕"
            }, { w, text -> transcript.append(w, text) }, recording::set, {}) } catch (e: Throwable) { errors.set(e) }
        }
        thread.start()
        try {
            assertTrue(entered.await(3, TimeUnit.SECONDS))
            p.requestStop(); projection.close()
            assertTrue(close.await(1, TimeUnit.SECONDS)); assertEquals(1, projectionClosed.get()); assertTrue(thread.isAlive)
        } finally { p.requestStop(); unblock.countDown(); thread.join(3000) }
        assertFalse(thread.isAlive); assertNull(errors.get()); assertFalse(recording.get())
        assertEquals("保留播放字幕", transcript.segments.single().text)
    }
    @Test fun initializationFailureReleasesProjectionAndNoRecordingState() {
        val release = CaptureRelease(); val closed = AtomicInteger(); release.install { closed.incrementAndGet() }
        val recording = AtomicBoolean()
        try {
            assertThrows(MicrophoneFailure::class.java) { MicPipeline().run({ throw MicrophoneFailure("初始化失敗") }, { "" }, { _, _ -> }, recording::set, {}) }
        } finally { release.close() }
        assertFalse(recording.get()); assertEquals(1, closed.get())
    }
    @Test fun canonicalTxtExport() { assertEquals("播放字幕", Export.render(listOf(TranscriptSegment(0.0, 3.0, "播放字幕")), "txt")) }
    @Test fun canonicalVttExport() { assertTrue(Export.render(listOf(TranscriptSegment(0.0, 3.0, "播放字幕")), "vtt").startsWith("WEBVTT\n\n00:00:00.000 --> 00:00:03.000")) }
    @Test fun canonicalSrtExport() { assertTrue(Export.render(listOf(TranscriptSegment(0.0, 3.0, "播放字幕")), "srt").startsWith("1\n00:00:00,000 --> 00:00:03,000")) }
    @Test fun officialProjectionAndNoMicrophoneFallback() {
        val code = source("PlaybackAudioSource.kt")
        assertTrue(code.contains("AudioPlaybackCaptureConfiguration.Builder(projection)")); assertTrue(code.contains("setAudioPlaybackCaptureConfig(capture)"))
        listOf("USAGE_MEDIA", "USAGE_GAME", "USAGE_UNKNOWN").forEach { assertTrue(code.contains(it)) }
        assertFalse(code.contains("setAudioSource(")); assertFalse(code.contains("AudioRecordSource.open")); assertFalse(code.contains("MediaRecorder"))
        assertTrue(code.contains("catch (_: SecurityException)")); assertTrue(code.contains("重新取得系統擷取授權"))
    }
    @Test fun separateForegroundTypesAndNoInternet() {
        val manifest = File("src/main/AndroidManifest.xml").readText()
        assertTrue(manifest.contains("android.permission.RECORD_AUDIO")); assertTrue(manifest.contains("android.permission.FOREGROUND_SERVICE_MEDIA_PROJECTION"))
        val service = Regex("<service[^>]*android:name=\".PlaybackService\"[^>]*/>").find(manifest)!!.value
        assertTrue(service.contains("android:exported=\"false\"")); assertTrue(service.contains("android:foregroundServiceType=\"mediaProjection\""))
        assertOfflineCoreHasNoNetworkDependencies()
    }
    @Test fun lifecycleAndNotificationStopContracts() {
        val playback = source("PlaybackService.kt"); val shared = source("MicrophoneService.kt")
        assertTrue(playback.contains("override fun onStop()")); assertTrue(playback.contains("session.registerCallback"))
        assertTrue(playback.contains("session.unregisterCallback")); assertTrue(playback.contains("session.stop()"))
        assertTrue(playback.contains("Intent.ACTION_SCREEN_OFF")); assertTrue(playback.contains("unregisterReceiver"))
        assertTrue(shared.contains("Intent(this, javaClass).setAction(\"stop\")")); assertTrue(shared.contains("override fun onTaskRemoved"))
        assertTrue(shared.contains("releaseCapture()")); assertTrue(shared.contains("START_NOT_STICKY"))
    }
    @Test fun consentAndForegroundOrderingContract() {
        val shared = source("MicrophoneService.kt"); val playback = source("PlaybackService.kt"); val ui = source("MainActivity.kt")
        assertTrue(shared.indexOf("start = true); prepareCapture(intent)") > 0)
        assertTrue(playback.contains("getMediaProjection(resultCode, data)")); assertEquals(1, Regex("getMediaProjection\\(").findAll(playback).count())
        assertTrue(playback.contains("intent.removeExtra(\"projection-data\")")); assertTrue(playback.contains("consent.consume("))
        assertTrue(ui.contains("createScreenCaptureIntent()")); assertTrue(ui.contains("StartActivityForResult()"))
        assertFalse(playback.contains("createVirtualDisplay")); assertFalse(playback.contains("SharedPreferences"))
    }
    @Test fun sameAsrAndAcceptedSourcePathsPreserved() {
        val playback = source("PlaybackService.kt"); val shared = source("MicrophoneService.kt")
        assertTrue(playback.contains("class PlaybackService : MicrophoneService()")); assertFalse(playback.contains("OfflineRecognizer("))
        assertTrue(shared.contains("AudioRecordSource.open(this@MicrophoneService)")); assertTrue(shared.contains("LocalJobs.gate.start()"))
        assertTrue(shared.contains("MicTranscript(LocalJobs.state.value.segments)"))
        assertTrue(source("LocalService.kt").contains("MediaDecoder(context).decode"))
        assertTrue(source("LocalService.kt").contains("LocalTranscriber(this@LocalService, ::status).transcribe"))
    }
}

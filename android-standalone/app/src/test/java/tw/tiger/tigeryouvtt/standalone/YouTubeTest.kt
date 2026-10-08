package tw.tiger.tigeryouvtt.standalone

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.nio.file.Files
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer

fun assertOfflineCoreHasNoNetworkDependencies() {
    val root = File("src/main/java/tw/tiger/tigeryouvtt/standalone")
    listOf("ModelStore.kt", "LocalService.kt", "MediaDecoder.kt", "MicrophoneService.kt", "MicrophoneCore.kt", "PlaybackService.kt", "PlaybackAudioSource.kt").forEach { name ->
        val code = File(root, name).readText()
        listOf("okhttp", "NewPipe", "YouTubeHttp", "java.net", "openConnection", "URL(", "WebView").forEach { assertFalse("$name: $it", code.contains(it)) }
    }
    val activity = File(root, "MainActivity.kt").readText().substringAfter("override fun onCreate").substringBefore("@OptIn")
    assertFalse(activity.contains("YouTube")); assertFalse(activity.contains("NewPipe"))
}

class YouTubeTest {
    private val id = "dQw4w9WgXcQ"
    private val url = "https://www.youtube.com/watch?v=$id"
    private val vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello &amp; <b>世界</b>\n\n00:00:04.000 --> 00:00:05.000\nNext line\n"
    private val root = File("src/main/java/tw/tiger/tigeryouvtt/standalone")
    private fun code(name: String) = File(root, name).readText()
    private class Fake : YouTubeSource {
        var tracks = listOf(YouTubeCaption("en", false, "caption"))
        var body = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n"
        var audioCalled = false; var downloadCalled = false; var failDownload = false
        override fun search(query: String) = listOf(YouTubeItem("dQw4w9WgXcQ", query, "channel", 30))
        override fun video(url: String) = YouTubeVideo(YouTubeItem(youtubeId(url), "title", "channel", 30), tracks)
        override fun caption(track: YouTubeCaption) = body
        override fun audio(video: YouTubeVideo): List<YouTubeAudio> { audioCalled = true; return listOf(YouTubeAudio("audio", "M4A", 128, true)) }
        override fun download(audio: YouTubeAudio, target: File, check: () -> Unit, progress: (Long, Long) -> Unit) {
            downloadCalled = true; target.writeBytes(byteArrayOf(1, 2, 3)); check()
            if (failDownload) throw IOException("download failed")
        }
    }
    private fun <T> temporary(block: (File) -> T): T {
        val dir = Files.createTempDirectory("tiger-youtube-test-").toFile()
        try { return block(dir) } finally { dir.listFiles()?.forEach { it.delete() }; dir.delete() }
    }
    private fun run(fake: Fake, dir: File, gate: JobGate = JobGate(), cancel: YouTubeCancel = YouTubeCancel(), ready: Boolean = true,
        asr: (File, () -> Unit) -> List<TranscriptSegment> = { _, _ -> error("ASR must not run") }): List<TranscriptSegment> =
        YouTubeWorkflow(fake, YouTubeCache(dir), gate, { ready }, cancel).retrieve(url, {}, {}, {}, {}, asr)

    @Test fun watchUrl() { assertEquals(id, youtubeId(url)) }
    @Test fun actualExtractorAudioFormatMapping() {
        assertEquals("M4A", youtubeAudioFormat(org.schabi.newpipe.extractor.MediaFormat.M4A))
        assertNotEquals("M4A", youtubeAudioFormat(org.schabi.newpipe.extractor.MediaFormat.MPEG_4))
        assertEquals("unknown", youtubeAudioFormat(null))
    }
    @Test fun shortUrl() { assertEquals(id, youtubeId("https://youtu.be/$id?t=2")) }
    @Test fun alternateUrls() { listOf("shorts", "live", "embed").forEach { assertEquals(id, youtubeId("https://www.youtube.com/$it/$id")) } }
    @Test fun invalidUrls() { listOf("http://youtube.com/watch?v=$id", "https://youtube.com.evil.test/watch?v=$id", "https://youtube.com@evil.test/watch?v=$id", "file://$id", "https://youtube.com/watch?v=x", "$url&v=$id").forEach { assertThrows(YouTubeFailure::class.java) { youtubeId(it) } } }
    @Test fun emptyQueryRejected() { assertThrows(YouTubeFailure::class.java) { youtubeQuery("   ") } }
    @Test fun queryTrimmedBounded() { assertEquals("hello", youtubeQuery(" hello ")); assertThrows(YouTubeFailure::class.java) { youtubeQuery("x".repeat(201)) } }
    @Test fun searchMapping() { val item = Fake().search("query").single(); assertEquals(url, item.url); assertEquals("channel", item.channel) }
    @Test fun noResultsHandled() { assertTrue(code("YouTubeExtractor.kt").contains("SearchExtractor.NothingFoundException")); assertTrue(code("YouTubeService.kt").contains("找不到搜尋結果")) }
    @Test fun offlineSearchError() { assertTrue(youtubeError(UnknownHostException()).contains("檢查網路")) }
    @Test fun timeoutSearchError() { assertTrue(youtubeError(SocketTimeoutException()).contains("逾時")) }
    @Test fun extractorError() { assertTrue(youtubeError(IllegalStateException()).contains("網站格式可能已變更")) }
    @Test fun metadataMapping() { val info = Fake().video(url); assertEquals(id, info.item.id); assertEquals("title", info.item.title); assertEquals(30, info.item.seconds.toInt()) }
    @Test fun manualPreferredWithinLanguage() { assertFalse(preferredCaptions(listOf(YouTubeCaption("en", true, "a"), YouTubeCaption("en", false, "b"))).first().automatic) }
    @Test fun automaticIsUsable() { temporary { val f = Fake(); f.tracks = listOf(YouTubeCaption("en", true, "a")); assertEquals(1, run(f, it).size) } }
    @Test fun languagePriority() { assertEquals(listOf("zh-TW", "zh", "en", "ja"), preferredCaptions(listOf("en", "ja", "zh", "zh-TW").map { YouTubeCaption(it, false, "") }).map { it.language }) }
    @Test fun vttParser() { val s = parseYoutubeVtt(vtt); assertEquals(2, s.size); assertEquals("Hello & 世界", s[0].text); assertEquals(1.0, s[0].start, 0.0) }
    @Test fun cueSettingsAndIdentifiers() { assertEquals("hello", parseYoutubeVtt("WEBVTT\n\ncue1\n00:00:00.000 --> 00:00:02.000 align:start\nhello").single().text) }
    @Test fun timestampsMonotonicWithOverlap() { val s = parseYoutubeVtt(vtt.replace("00:00:04.000", "00:00:02.000")); assertEquals(2.0, s[0].end, 0.0); Export.render(s, "srt") }
    @Test fun invalidTimestampsRejected() { assertThrows(YouTubeFailure::class.java) { parseYoutubeVtt(vtt.replace("00:00:01.000", "00:00:09.000")) } }
    @Test fun htmlResponseNotCaption() { assertThrows(YouTubeFailure::class.java) { parseYoutubeVtt("<html>sign in</html>") } }
    @Test fun captionPathDoesNotAcquireModel() { temporary { val gate = JobGate(); gate.start(); val f = Fake(); assertEquals(1, run(f, it, gate, ready = false).size); assertTrue(gate.busy()); assertFalse(f.audioCalled); assertFalse(f.downloadCalled) } }
    @Test fun noCaptionFallsBackAndCleans() { temporary { dir -> val f = Fake(); f.tracks = emptyList(); val gate = JobGate(); val result = run(f, dir, gate) { file, _ -> assertTrue(file.exists()); assertTrue(gate.busy()); listOf(TranscriptSegment(0.0, 1.0, "local")) }; assertEquals("local", result.single().text); assertFalse(gate.busy()); assertTrue(dir.listFiles()!!.isEmpty()) } }
    @Test fun unusableCaptionFallsBack() { temporary { dir -> val f = Fake(); f.body = "WEBVTT\n\n"; run(f, dir) { _, _ -> emptyList() }; assertTrue(f.audioCalled) } }
    @Test fun onlyModerateBitrateM4aSelected() { val a = preferredAudio(listOf(YouTubeAudio("high", "M4A", 256, true), YouTubeAudio("chosen", "M4A", 96, true), YouTubeAudio("manifest", "M4A", 96, false), YouTubeAudio("video", "MP4", 96, true))); assertEquals("chosen", a.url) }
    @Test fun unsupportedAdaptiveManifestRejected() { assertThrows(YouTubeFailure::class.java) { preferredAudio(listOf(YouTubeAudio("mpd", "M4A", 128, false))) } }
    @Test fun staleCacheCleanupIsScoped() { temporary { dir -> File(dir, "yt-stale.m4a").writeText("x"); File(dir, "keep.txt").writeText("x"); YouTubeCache(dir).prepare(); assertFalse(File(dir, "yt-stale.m4a").exists()); assertTrue(File(dir, "keep.txt").exists()) } }
    @Test fun downloadCancellationCleansPartial() { temporary { dir -> val c = YouTubeCache(dir); val file = c.create(); assertThrows(StopRequested::class.java) { c.copy(byteArrayOf(1, 2).inputStream(), file, 2, { throw StopRequested() }, { _, _ -> }) }; assertFalse(file.exists()) } }
    @Test fun downloadNetworkFailureReleasesModelAndFile() { temporary { dir -> val f = Fake(); f.tracks = emptyList(); f.failDownload = true; val gate = JobGate(); assertThrows(IOException::class.java) { run(f, dir, gate) }; assertFalse(gate.busy()); assertTrue(dir.listFiles()!!.isEmpty()) } }
    @Test fun incompleteDownloadRejected() { temporary { dir -> val c = YouTubeCache(dir); val file = c.create(); assertThrows(YouTubeFailure::class.java) { c.copy(byteArrayOf(1).inputStream(), file, 2, {}, { _, _ -> }) }; assertFalse(file.exists()) } }
    @Test fun downloadSizeBound() { temporary { dir -> val c = YouTubeCache(dir); assertThrows(YouTubeFailure::class.java) { c.copy(byteArrayOf().inputStream(), c.create(), YouTubeCache.MAX_BYTES + 1, {}, { _, _ -> }) } } }
    @Test fun decodeFailureReleasesModel() { temporary { dir -> val f = Fake(); f.tracks = emptyList(); val gate = JobGate(); assertThrows(IllegalArgumentException::class.java) { run(f, dir, gate) { _, _ -> throw IllegalArgumentException() } }; assertFalse(gate.busy()); assertTrue(dir.listFiles()!!.isEmpty()) } }
    @Test fun modelBusyRejectsFallbackWithoutDownload() { temporary { dir -> val f = Fake(); f.tracks = emptyList(); val gate = JobGate(); gate.start(); assertThrows(YouTubeFailure::class.java) { run(f, dir, gate) }; assertTrue(gate.busy()); assertFalse(f.downloadCalled) } }
    @Test fun missingModelRejectsFallback() { temporary { dir -> val f = Fake(); f.tracks = emptyList(); assertThrows(YouTubeFailure::class.java) { run(f, dir, ready = false) }; assertFalse(f.downloadCalled) } }
    @Test fun asrCancellationReleasesGateAndTemp() { temporary { dir -> val f = Fake(); f.tracks = emptyList(); val token = YouTubeCancel(); val gate = JobGate(); assertThrows(StopRequested::class.java) { run(f, dir, gate, token) { _, check -> token.stop(); check(); emptyList() } }; assertFalse(gate.busy()); assertTrue(dir.listFiles()!!.isEmpty()) } }
    @Test fun cancellationBeforeNetwork() { temporary { dir -> val token = YouTubeCancel(); token.stop(); assertThrows(StopRequested::class.java) { run(Fake(), dir, cancel = token) } } }
    @Test fun completedTranscriptRetainedOnCancelContract() { assertTrue(code("YouTubeService.kt").contains("it.copy(segments = done")); assertFalse(code("YouTubeService.kt").contains("segments = emptyList()")) }
    @Test fun txtExport() { assertEquals("Hello & 世界\nNext line", Export.render(parseYoutubeVtt(vtt), "txt")) }
    @Test fun vttExport() { assertTrue(Export.render(parseYoutubeVtt(vtt), "vtt").startsWith("WEBVTT")) }
    @Test fun srtExport() { assertTrue(Export.render(parseYoutubeVtt(vtt), "srt").contains("00:00:01,000 --> 00:00:03,000")) }
    @Test fun authRequiredControlled() { assertTrue(youtubeError(RuntimeException("Sign in to confirm you're not a bot")).contains("Standalone 模式暫不支援")) }
    @Test fun noEmbeddedCredentialOrLoginFlow() { val source = code("YouTubeExtractor.kt") + code("YouTubeScreen.kt"); listOf("youtube_cookies.txt", "WebView", "GoogleSignIn", "OAuth", "CookieManager").forEach { assertFalse(source.contains(it)) }; assertTrue(source.contains("CookieJar.NO_COOKIES")) }
    @Test fun offlineCoreIsolation() { assertOfflineCoreHasNoNetworkDependencies() }
    @Test fun internetOnlyPermissionAddition() { val m = File("src/main/AndroidManifest.xml").readText(); assertTrue(m.contains("android.permission.INTERNET")); assertFalse(m.contains("WRITE_EXTERNAL_STORAGE")); assertTrue(m.contains("FOREGROUND_SERVICE_MEDIA_PROJECTION")); assertTrue(m.contains("android.permission.RECORD_AUDIO")) }
    @Test fun noMandatoryNetworkStartup() { assertOfflineCoreHasNoNetworkDependencies(); assertTrue(code("YouTubeService.kt").contains("NewPipeYouTubeSource(YouTubeHttp(cancel, observer")) }
    @Test fun sharedAsrNoDuplicateRecognizer() { assertTrue(code("YouTubeService.kt").contains("LocalTranscriber(this@YouTubeService")); assertFalse(code("YouTubeService.kt").contains("OfflineRecognizer(")) }
    @Test fun noAutomaticPlaybackFallback() { assertFalse(code("YouTubeService.kt").contains("PlaybackService")); assertFalse(code("YouTubeService.kt").contains("MicrophoneService")) }
    @Test fun resourceAllowlist() { assertTrue(allowedYoutubeResource("https://r1.googlevideo.com/a")); assertFalse(allowedYoutubeResource("https://googlevideo.com.evil.test/a")); assertFalse(allowedYoutubeResource("https://127.0.0.1/a")); assertFalse(allowedYoutubeResource("http://youtube.com/a")) }
    @Test fun actualHttpClientTextAndNoCookies() {
        MockWebServer().use { server -> server.enqueue(MockResponse().setBody(vtt)); server.start()
            val http = YouTubeHttp(YouTubeCancel()) { it.startsWith(server.url("/").toString()) }
            val body = http.response(server.url("/caption").toString(), headers = mapOf("Cookie" to listOf("secret"), "Authorization" to listOf("secret"))) { http.limitedText(it, 10000) }
            assertEquals(vtt, body); val request = server.takeRequest(); assertNull(request.getHeader("Cookie")); assertNull(request.getHeader("Authorization"))
        }
    }
    @Test fun actualHttpCancellationUnblocks() {
        MockWebServer().use { server -> server.enqueue(MockResponse().setSocketPolicy(okhttp3.mockwebserver.SocketPolicy.NO_RESPONSE)); server.start()
            val token = YouTubeCancel(); val http = YouTubeHttp(token) { it.startsWith(server.url("/").toString()) }; val error = AtomicReference<Throwable?>()
            val t = Thread { try { http.response(server.url("/").toString()) { http.limitedText(it, 10) } } catch (e: Throwable) { error.set(e) } }
            t.start(); assertNotNull(server.takeRequest(2, TimeUnit.SECONDS)); token.stop(); t.join(2000)
            assertFalse(t.isAlive); assertNotNull(error.get())
        }
    }
    @Test fun redirectOutsideAllowlistRejected() {
        MockWebServer().use { server -> server.enqueue(MockResponse().setResponseCode(302).addHeader("Location", "https://evil.test/a")); server.start()
            val http = YouTubeHttp(YouTubeCancel()) { it.startsWith(server.url("/").toString()) }
            assertThrows(YouTubeFailure::class.java) { http.response(server.url("/").toString()) { } }
        }
    }
}

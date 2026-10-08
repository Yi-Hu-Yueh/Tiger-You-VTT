package tw.tiger.tigeryouvtt.standalone

import org.junit.Assert.*
import org.junit.Test
import okhttp3.mockwebserver.*
import okio.Buffer
import java.io.*
import java.net.*
import java.nio.file.Files
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import javax.net.ssl.SSLHandshakeException

class YouTubeDownloadTest {
    private fun media(size: Int = 1024): ByteArray = ByteArrayOutputStream().also { raw ->
        DataOutputStream(raw).use { out ->
            out.writeInt(16); out.writeBytes("ftyp"); out.writeBytes("M4A "); out.writeInt(0)
            out.writeInt(12); out.writeBytes("moov"); out.writeInt(0)
            out.writeInt(size - 28); out.writeBytes("mdat"); out.write(ByteArray(size - 36) { 7 })
        }
    }.toByteArray()
    private fun <T> environment(block: (MockWebServer, File, YouTubeCache, YouTubeCancel, YouTubeHttp) -> T): T {
        val dir = Files.createTempDirectory("tiger-d2r-test-").toFile()
        try { MockWebServer().use { server ->
            server.start(); val cancel = YouTubeCancel()
            val http = YouTubeHttp(cancel, YouTubeTimeouts(readMs = 500, mediaMs = 3000)) { it.startsWith(server.url("/").toString()) }
            return block(server, dir, YouTubeCache(dir), cancel, http)
        } } finally { dir.listFiles()?.forEach { it.delete() }; dir.delete() }
    }
    private fun stream(server: MockWebServer, size: Long = -1) = YouTubeAudio(server.url("/media?signature=secret").toString(), "M4A", 128, true, size)
    private fun response(bytes: ByteArray, status: Int = 200) = MockResponse().setResponseCode(status).setHeader("Content-Type", "audio/mp4").setBody(Buffer().write(bytes))
    private fun fail(code: Int) = environment { s, dir, cache, token, http ->
        s.enqueue(MockResponse().setResponseCode(code).setBody("secret error body"))
        val e = assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> } }
        assertEquals(code, e.status); assertTrue(e.userMessage.contains("HTTP $code")); assertFalse(e.userMessage.contains("secret"))
        assertTrue(dir.listFiles()!!.isEmpty()); assertEquals(1, s.requestCount)
    }
    @Test fun http200CompleteRename() = environment { s, dir, cache, token, http ->
        val data = media(); s.enqueue(response(data)); val file = cache.create()
        downloadYouTubeAudio(http, cache, stream(s, data.size.toLong()), file, token::check) { _, _ ->
            assertFalse(file.exists()); assertTrue(File(file.path + ".part").exists())
        }
        assertArrayEquals(data, file.readBytes()); assertEquals(listOf(file), dir.listFiles()!!.toList()); validateM4a(file)
    }
    @Test fun http206Complete() = environment { s, _, cache, token, http ->
        val data = media(); s.enqueue(response(data, 206).setHeader("Content-Range", "bytes 0-1023/1024"))
        val file = cache.create(); downloadYouTubeAudio(http, cache, stream(s), file, token::check) { _, _ -> }; assertEquals(1024L, file.length())
    }
    @Test fun multipleRangesAreContiguousFreshAndIdentity() = environment { s, _, cache, token, http ->
        val data = media(AUDIO_RANGE_BYTES.toInt() + 2000)
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val r = request
                assertEquals("identity", r.getHeader("Accept-Encoding")); assertEquals("Mozilla/5.0", r.getHeader("User-Agent"))
                assertNull(r.getHeader("Cookie")); assertNull(r.getHeader("Authorization")); assertNull(r.getHeader("Origin")); assertNull(r.getHeader("Referer"))
                val numbers = r.getHeader("Range")!!.removePrefix("bytes=").split('-').map { it.toInt() }
                val end = minOf(numbers[1], data.lastIndex)
                return response(data.copyOfRange(numbers[0], end + 1), 206).setHeader("Content-Range", "bytes ${numbers[0]}-$end/${data.size}").setHeader("ETag", "fixed")
            }
        }
        val file = cache.create(); downloadYouTubeAudio(http, cache, stream(s), file, token::check) { _, _ -> }
        assertArrayEquals(data, file.readBytes()); assertEquals(2, s.requestCount)
        assertEquals("bytes=0-1048575", s.takeRequest().getHeader("Range"))
        assertEquals("bytes=1048576-1050575", s.takeRequest().getHeader("Range"))
    }
    @Test fun http400() = fail(400)
    @Test fun http401() = fail(401)
    @Test fun http403() = fail(403)
    @Test fun http404() = fail(404)
    @Test fun http416() = fail(416)
    @Test fun http429() = fail(429)
    @Test fun http503() = fail(503)
    @Test fun redirectPreservesFreshRange() = environment { s, _, cache, token, http ->
        s.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "/next")); s.enqueue(response(media()))
        downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> }
        assertEquals("bytes=0-1048575", s.takeRequest().getHeader("Range")); assertEquals("bytes=0-1048575", s.takeRequest().getHeader("Range")); assertEquals(1, http.diagnostic.redirects)
    }
    @Test fun redirectLimit() = environment { s, dir, cache, token, http ->
        repeat(6) { s.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "/again")) }
        val e = assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> } }
        assertEquals(DownloadProblem.REDIRECT, e.problem); assertEquals(6, s.requestCount); assertTrue(dir.listFiles()!!.isEmpty())
    }
    @Test fun httpsDowngradeNotAllowed() {
        assertFalse(allowedYoutubeResource("http://rr1.googlevideo.com/audio"))
        assertFalse(allowedYoutubeResource("https://secret@rr1.googlevideo.com/audio"))
        environment { s, dir, cache, token, http ->
            s.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "http://rr1.googlevideo.com/audio"))
            val e = assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> } }
            assertEquals(DownloadProblem.UNSAFE_REDIRECT, e.problem); assertTrue(dir.listFiles()!!.isEmpty())
        }
    }
    @Test fun readTimeoutAndCleanup() = environment { s, dir, cache, token, http ->
        s.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
        val e = assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> } }
        assertEquals(DownloadProblem.READ_TIMEOUT, e.problem); assertTrue(dir.listFiles()!!.isEmpty())
    }
    @Test fun timeoutDnsTlsNetworkClassification() {
        assertEquals(DownloadProblem.CONNECT_TIMEOUT, downloadException(SocketTimeoutException("secret"), "connect").problem)
        assertEquals(DownloadProblem.CALL_TIMEOUT, downloadException(InterruptedIOException("timeout secret"), "read").problem)
        assertEquals(DownloadProblem.DNS, downloadException(UnknownHostException("secret"), "connect").problem)
        assertEquals(DownloadProblem.NETWORK, downloadException(ConnectException("secret"), "connect").problem)
        assertEquals(DownloadProblem.TLS, downloadException(SSLHandshakeException("secret"), "connect").problem)
    }
    @Test fun actualWholeCallTimeoutClassified() = environment { s, dir, cache, token, _ ->
        s.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
        val http = YouTubeHttp(token, YouTubeTimeouts(readMs = 2000, mediaMs = 100)) { it.startsWith(s.url("/").toString()) }
        val e = assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> } }
        assertEquals(DownloadProblem.CALL_TIMEOUT, e.problem); assertTrue(dir.listFiles()!!.isEmpty())
    }
    @Test fun chunked200UnknownLength() = environment { s, _, cache, token, http ->
        val data = media(); s.enqueue(MockResponse().setHeader("Content-Type", "audio/mp4").setChunkedBody(Buffer().write(data), 100))
        val file = cache.create(); downloadYouTubeAudio(http, cache, stream(s), file, token::check) { _, _ -> }
        assertArrayEquals(data, file.readBytes())
    }
    @Test fun changedRepresentationRejected() = environment { s, dir, cache, token, http ->
        val data = media(AUDIO_RANGE_BYTES.toInt() + 100)
        s.enqueue(response(data.copyOfRange(0, AUDIO_RANGE_BYTES.toInt()), 206).setHeader("Content-Range", "bytes 0-1048575/${data.size}").setHeader("ETag", "first"))
        s.enqueue(response(data.copyOfRange(AUDIO_RANGE_BYTES.toInt(), data.size), 206).setHeader("Content-Range", "bytes 1048576-${data.lastIndex}/${data.size}").setHeader("ETag", "changed"))
        val e = assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> } }
        assertEquals(DownloadProblem.LENGTH, e.problem); assertTrue(dir.listFiles()!!.isEmpty())
    }
    @Test fun prematureEof() = environment { _, dir, cache, _, _ ->
        val e = assertThrows(DownloadFailure::class.java) { cache.copy(byteArrayOf(1).inputStream(), cache.create(), 100, {}, { _, _ -> }) }
        assertEquals(DownloadProblem.EOF, e.problem); assertTrue(dir.listFiles()!!.isEmpty())
    }
    @Test fun disconnectedBodyCleans() = environment { s, dir, cache, token, http ->
        s.enqueue(response(media(100000)).setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY))
        assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> } }
        assertTrue(dir.listFiles()!!.isEmpty()); assertEquals(1, s.requestCount)
    }
    @Test fun contentLengthMismatch() = environment { s, dir, cache, token, http ->
        s.enqueue(response(media()))
        val e = assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, stream(s, 2048), cache.create(), token::check) { _, _ -> } }
        assertEquals(DownloadProblem.LENGTH, e.problem); assertTrue(dir.listFiles()!!.isEmpty())
    }
    @Test fun wrongRangeAndIncomplete206Rejected() {
        listOf("bytes 1-99/100", "bytes 0-99/*", "bytes 0-10/100", "bytes 0-99/99").forEach { header ->
            assertThrows(DownloadFailure::class.java) { chunkResponseLength(header, -1, 0, 1048575, -1) }
        }
        assertThrows(DownloadFailure::class.java) { completeResponseLength(206, 1024, "bytes 0-1023/2000", -1) }
    }
    @Test fun midTransfer200NeverAppended() = environment { s, dir, cache, token, http ->
        val data = media(AUDIO_RANGE_BYTES.toInt() + 100)
        s.enqueue(response(data.copyOfRange(0, AUDIO_RANGE_BYTES.toInt()), 206).setHeader("Content-Range", "bytes 0-1048575/${data.size}"))
        s.enqueue(response(data))
        val e = assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> } }
        assertEquals(DownloadProblem.RANGE, e.problem); assertTrue(dir.listFiles()!!.isEmpty())
    }
    @Test fun cancellationDuringHttp() = environment { s, dir, cache, token, http ->
        s.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE)); val error = AtomicReference<Throwable?>()
        val worker = Thread { try { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> } } catch (e: Throwable) { error.set(e) } }
        worker.start(); assertNotNull(s.takeRequest(2, TimeUnit.SECONDS)); token.stop(); worker.join(2000)
        assertFalse(worker.isAlive); assertTrue(error.get() is StopRequested); assertTrue(dir.listFiles()!!.isEmpty()); assertEquals(DownloadProblem.CANCELLED, http.diagnostic.problem)
    }
    @Test fun cancellationDuringBody() = environment { s, dir, cache, token, http ->
        s.enqueue(response(media()))
        assertThrows(StopRequested::class.java) { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> token.stop() } }
        assertTrue(dir.listFiles()!!.isEmpty())
    }
    @Test fun expiredRejectedWithoutRequestOrRetry() = environment { s, dir, cache, token, http ->
        val a = stream(s).copy(url = s.url("/media?expire=1&signature=secret").toString())
        val e = assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, a, cache.create(), token::check) { _, _ -> } }
        assertEquals(DownloadProblem.EXPIRED, e.problem); assertEquals(0, s.requestCount); assertTrue(dir.listFiles()!!.isEmpty())
    }
    @Test fun malformedUrlAndFreshExpiry() {
        assertThrows(DownloadFailure::class.java) { checkStreamExpiry("not a url") }
        checkStreamExpiry("https://rr1.googlevideo.com/a?expire=100", 99)
    }
    @Test fun storageFailureControlled() = environment { _, dir, _, _, _ ->
        val file = File(dir, "not-directory"); file.writeText("x")
        val e = assertThrows(DownloadFailure::class.java) { YouTubeCache(file).prepare() }; assertEquals(DownloadProblem.STORAGE, e.problem)
    }
    @Test fun stalePartCleanup() = environment { _, dir, cache, _, _ ->
        File(dir, "yt-old.m4a.part").writeText("x"); File(dir, "unrelated.part").writeText("keep"); cache.prepare()
        assertFalse(File(dir, "yt-old.m4a.part").exists()); assertTrue(File(dir, "unrelated.part").exists())
    }
    @Test fun htmlNeverPromoted() = environment { s, dir, cache, token, http ->
        s.enqueue(MockResponse().setHeader("Content-Type", "text/html").setBody("<html>secret</html>"))
        val e = assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> } }
        assertEquals(DownloadProblem.CONTAINER, e.problem); assertTrue(dir.listFiles()!!.isEmpty())
    }
    @Test fun incompleteContainerNeverPromoted() = environment { s, dir, cache, token, http ->
        s.enqueue(response(media().copyOf(50)))
        val e = assertThrows(DownloadFailure::class.java) { downloadYouTubeAudio(http, cache, stream(s), cache.create(), token::check) { _, _ -> } }
        assertEquals(DownloadProblem.CONTAINER, e.problem); assertTrue(dir.listFiles()!!.isEmpty())
    }
    @Test fun diagnosticsNeverContainUrlsHeadersOrExceptionText() {
        val diagnostic = DownloadDiagnostic(host = "https://user:secret@host/a?token=secret", mime = "audio/mp4; token=secret", status = 403)
        assertFalse(diagnostic.safeText().contains("secret")); assertFalse(diagnostic.safeText().contains("https://"))
        assertFalse(downloadException(IOException("https://secret?token=secret"), "read").userMessage.contains("secret"))
        assertFalse(YouTubeAudio("https://host?token=secret", "M4A", 128, true).toString().contains("secret"))
        assertTrue(DownloadFailure(DownloadProblem.DECODE).userMessage.contains("解碼")); assertTrue(DownloadFailure(DownloadProblem.ASR).userMessage.contains("SenseVoice"))
    }
    @Test fun noAutomaticRetriesOrCredentialHeaders() {
        val root = File("src/main/java/tw/tiger/tigeryouvtt/standalone")
        val http = File(root, "YouTubeExtractor.kt").readText()
        assertTrue(http.contains("retryOnConnectionFailure(false)")); assertTrue(http.contains("CookieJar.NO_COOKIES"))
        val code = File(root, "YouTubeDownload.kt").readText(); assertTrue(code.contains("++requests > 100")); assertFalse(code.contains("Authorization")); assertFalse(code.contains("Cookie"))
        assertOfflineCoreHasNoNetworkDependencies()
    }
}

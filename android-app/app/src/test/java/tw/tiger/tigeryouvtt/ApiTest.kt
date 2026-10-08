package tw.tiger.tigeryouvtt

import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ApiTest {
    @Test fun youtubeAuthJobMapping() {
        val snapshot = Snapshot.parse(JSONObject("""{"job_id":"j","status":"failed","error":{"code":"youtube_auth_required","message":"untrusted diagnostic"}}"""))
        assertEquals(YOUTUBE_AUTH_MESSAGE, snapshot.error)
    }
    @Test fun youtubeAuthHttpMapping() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(403).setBody("""{"detail":{"code":"youtube_auth_required","message":"untrusted diagnostic"}}"""))
            val api = TigerApi(server.url("/").toString(), "test-token")
            try {
                try { api.request("/api/youtube/search?q=test"); fail("Expected controlled auth error") }
                catch (e: ApiFailure) { assertEquals(YOUTUBE_AUTH_MESSAGE, e.message) }
            } finally { api.close() }
        }
    }
    @Test fun normalizesUrl() { assertEquals("http://192.168.1.100:8000", normalizeUrl(" http://192.168.1.100:8000/ ")) }
    @Test fun supportsHttps() { assertEquals("https://pc.example", normalizeUrl("https://pc.example/")) }
    @Test(expected = IllegalArgumentException::class) fun rejectsCredentials() { normalizeUrl("http://user:password@pc/") }
    @Test(expected = IllegalArgumentException::class) fun rejectsQueryToken() { normalizeUrl("http://pc/?token=secret") }
    @Test(expected = IllegalArgumentException::class) fun rejectsPaths() { normalizeUrl("http://pc/api") }
    @Test(expected = IllegalArgumentException::class) fun rejectsInvalidUrl() { normalizeUrl("not a url") }
    @Test fun tokenHeaderOnly() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("{}"))
            val client = OkHttpClient.Builder().addInterceptor(TokenInterceptor("unit-test-token")).build()
            client.newCall(Request.Builder().url(server.url("/health")).build()).execute().close()
            val request = server.takeRequest()
            assertEquals("unit-test-token", request.getHeader("X-Tiger-Mobile-Token"))
            assertEquals("/health", request.path)
        }
    }
    @Test fun parsesJob() {
        val snapshot = Snapshot.parse(JSONObject("""{"job_id":"j1","status":"running","elapsed_seconds":4.5,"segment_count":2,"txt":"原文","vtt":"WEBVTT","srt":"1","result":{"live_phase":"capturing"}}"""))
        assertEquals("j1", snapshot.id); assertEquals(2, snapshot.count); assertEquals(4.5, snapshot.elapsed, 0.0)
        assertEquals("低延遲字幕收音中", statusLabel(snapshot.status, snapshot.phase))
    }
    @Test fun parsesSearch() {
        val result = parseSearch(JSONObject("""{"results":[{"video_id":"id","title":"標題","url":"https://youtu.be/id","channel":"頻道","duration_text":"1:00"}]}"""))
        assertEquals("標題", result.single().title); assertEquals("id", result.single().id)
    }
    @Test fun terminalStates() {
        listOf("completed", "failed", "stopped").forEach { assertTrue(terminal(it)) }
        listOf("queued", "running", "stopping").forEach { assertFalse(terminal(it)) }
    }
    @Test fun preparationAndStopLabels() {
        assertEquals("準備低延遲模型...", statusLabel("running", "preparing_model"))
        assertEquals("停止中", statusLabel("stopping", "capturing"))
        assertEquals("已停止", statusLabel("stopped", "capturing"))
    }
    @Test fun controlledErrors() {
        assertTrue(httpError(401).contains("金鑰")); assertTrue(httpError(413).contains("檔案"))
        assertTrue(httpError(422).contains("輸入")); assertTrue(httpError(503).contains("伺服器"))
    }
    @Test fun speakerSelectionPreservesOriginal() {
        val snapshot = Snapshot.parse(JSONObject("""{"job_id":"j","status":"completed","txt":"plain","vtt":"original vtt","srt":"original srt","result":{"speaker_txt":"Speaker 1: hello","speaker_vtt":"speaker vtt","speaker_srt":"speaker srt"}}"""))
        assertEquals("Speaker 1: hello", snapshot.selected(true).txt)
        assertEquals("plain", snapshot.selected(false).txt)
        assertEquals("original srt", snapshot.selected(false).format("srt"))
        assertEquals("speaker vtt", snapshot.selected(true).format("vtt"))
    }
    @Test fun absentSpeakersFallBack() {
        val snapshot = Snapshot.parse(JSONObject("""{"job_id":"j","status":"stopped","txt":"partial","result":{"speaker_txt":null}}"""))
        assertEquals("partial", snapshot.selected(true).txt)
    }
    @Test fun lowLatencyPayload() {
        assertTrue(audioPayload(14, true).getBoolean("low_latency"))
        assertFalse(audioPayload(14, false).getBoolean("low_latency"))
        assertEquals(14, audioPayload(14, true).getInt("device_id"))
    }
    @Test fun diarizationAndRangePayload() {
        val normal = youtubePayload("https://youtu.be/id")
        assertFalse(normal.getBoolean("enable_diarization"))
        assertTrue(normal.getBoolean("end_time_is_default"))
        val custom = youtubePayload("https://youtu.be/id", "0:2", "0:5", true)
        assertTrue(custom.getBoolean("enable_diarization")); assertFalse(custom.getBoolean("end_time_is_default"))
        assertEquals("0:2", custom.getString("start_time"))
    }
    @Test fun apiSendsJsonAndParsesResponse() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"job_id":"job-test","status":"queued"}"""))
            val api = TigerApi(server.url("/").toString(), "test-token")
            try {
                assertEquals("job-test", api.post("/api/jobs/system-audio", audioPayload(3, true)).getString("job_id"))
                val request = server.takeRequest()
                assertEquals("test-token", request.getHeader("X-Tiger-Mobile-Token"))
                assertTrue(JSONObject(request.body.readUtf8()).getBoolean("low_latency"))
            } finally { api.close() }
        }
    }
    @Test fun redirectDoesNotLeakToken() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "http://127.0.0.1:1/"))
            val api = TigerApi(server.url("/").toString(), "sensitive-test-token")
            try {
                try { api.request("/health"); fail("Redirect should fail") } catch (e: java.io.IOException) { assertFalse(e.message!!.contains("sensitive-test-token")) }
                assertEquals(1, server.requestCount)
            } finally { api.close() }
        }
    }
}

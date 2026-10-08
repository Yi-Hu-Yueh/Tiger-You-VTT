package tw.tiger.tigeryouvtt

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

fun normalizeUrl(input: String): String {
    val url = input.trim().toHttpUrlOrNull() ?: throw IllegalArgumentException("請輸入完整的 http:// 或 https:// 伺服器網址")
    require(url.username.isEmpty() && url.password.isEmpty() && url.query == null && url.fragment == null && url.encodedPath.trim('/').isEmpty()) {
        "伺服器網址不可包含帳密、路徑或查詢參數"
    }
    return url.toString().trimEnd('/')
}

fun statusLabel(status: String, phase: String = ""): String = when {
    status == "stopping" -> "停止中"
    phase == "preparing_model" && status == "running" -> "準備低延遲模型..."
    phase == "capturing" && status == "running" -> "低延遲字幕收音中"
    else -> mapOf("queued" to "等待中", "running" to "處理中", "stopped" to "已停止", "completed" to "完成", "failed" to "失敗", "uploading" to "上傳中")[status] ?: status
}
fun terminal(status: String) = status in setOf("completed", "stopped", "failed")
const val YOUTUBE_AUTH_MESSAGE = "YouTube 要求登入驗證，請先在 PC 設定 YouTube Cookie。"
fun httpError(code: Int) = when (code) {
    401, 403 -> "金鑰錯誤或沒有存取權限"
    404 -> "找不到工作／API，請確認伺服器版本"
    413 -> "檔案超過伺服器上傳限制"
    422 -> "輸入無效，請檢查網址、時間或媒體格式"
    in 500..599 -> "伺服器處理失敗，請檢查 PC 狀態後重試"
    else -> "請求失敗（HTTP $code）"
}
fun youtubePayload(url: String, start: String = "0:0", end: String = "0:10", diarize: Boolean = false) = JSONObject()
    .put("url", url).put("start_time", start).put("end_time", end)
    .put("end_time_is_default", end == "0:10").put("enable_diarization", diarize)
fun audioPayload(device: Int, lowLatency: Boolean) = JSONObject().put("device_id", device).put("low_latency", lowLatency)

data class Transcript(val txt: String = "", val vtt: String = "", val srt: String = "") {
    fun format(ext: String) = when (ext) { "vtt" -> vtt; "srt" -> srt; else -> txt }
}
data class Snapshot(val id: String, val status: String, val elapsed: Double, val count: Int,
                    val original: Transcript, val speakers: Transcript?, val phase: String, val error: String?) {
    companion object {
        fun parse(json: JSONObject): Snapshot {
            val result = json.optJSONObject("result")
            val speakers = result?.takeIf { !it.isNull("speaker_txt") && it.optString("speaker_txt").isNotBlank() }?.let {
                Transcript(it.optString("speaker_txt"), it.optString("speaker_vtt", ""), it.optString("speaker_srt", ""))
            }
            return Snapshot(json.getString("job_id"), json.getString("status"), json.optDouble("elapsed_seconds", 0.0),
                json.optInt("segment_count"), Transcript(json.optString("txt"), json.optString("vtt"), json.optString("srt")),
                speakers, result?.optString("live_phase") ?: "", if (json.optJSONObject("error")?.optString("code") == "youtube_auth_required") YOUTUBE_AUTH_MESSAGE else if (json.optJSONObject("error") != null) "伺服器工作失敗；已保留取得的字幕" else
                    if (result?.optString("diarization_status") == "failed") "說話者分離失敗；仍可使用原始字幕" else null)
        }
    }
    fun selected(speakerAware: Boolean) = if (speakerAware) speakers ?: original else original
}
data class SearchVideo(val id: String, val title: String, val url: String, val details: String)
fun parseSearch(json: JSONObject): List<SearchVideo> {
    val array = json.getJSONArray("results")
    return (0 until array.length()).map { index -> array.getJSONObject(index).let {
        SearchVideo(it.getString("video_id"), it.getString("title"), it.getString("url"),
            listOf(it.optString("channel", ""), it.optString("duration_text", "")).filter { s -> s != "null" }.joinToString(" · "))
    } }
}
data class PcDevice(val id: Int, val name: String, val default: Boolean)

class TokenInterceptor(private val token: String) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response = chain.proceed(chain.request().newBuilder()
        .header("X-Tiger-Mobile-Token", token).build())
}

class ApiFailure(val statusCode: Int, backendCode: String = "") : IOException(
    if (backendCode == "youtube_auth_required") YOUTUBE_AUTH_MESSAGE else httpError(statusCode)
)

class TigerApi(base: String, token: String) {
    private val baseUrl = normalizeUrl(base)
    private val client = OkHttpClient.Builder().addInterceptor(TokenInterceptor(token))
        .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false)
        .connectTimeout(10, TimeUnit.SECONDS).readTimeout(90, TimeUnit.SECONDS)
        .writeTimeout(90, TimeUnit.SECONDS).build()

    suspend fun request(path: String, body: RequestBody? = null, onCall: (Call) -> Unit = {}): JSONObject = withContext(Dispatchers.IO) {
        val request = Request.Builder().url(baseUrl + path).apply { if (body != null) post(body) }.build()
        val call = client.newCall(request)
        onCall(call)
        call.execute().use { response ->
            if (!response.isSuccessful) {
                // Whitelist only a known code, never display raw server diagnostics.
                val code = runCatching { JSONObject(response.body?.string() ?: "{}").optJSONObject("detail")?.optString("code") }.getOrNull() ?: ""
                throw ApiFailure(response.code, code)
            }
            JSONObject(response.body?.string() ?: throw IOException("伺服器回應為空"))
        }
    }
    suspend fun post(path: String, body: JSONObject, onCall: (Call) -> Unit = {}) =
        request(path, body.toString().toRequestBody("application/json; charset=utf-8".toMediaType()), onCall)
    suspend fun stop(id: String) = Snapshot.parse(post("/api/jobs/$id/stop", JSONObject()))
    fun close() { client.dispatcher.cancelAll(); client.connectionPool.evictAll() }
}

package tw.tiger.tigeryouvtt.standalone

import java.io.EOFException
import java.io.File
import java.io.IOException
import java.io.InterruptedIOException
import java.io.RandomAccessFile
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.ProtocolException
import java.net.SocketTimeoutException
import java.net.URI
import java.net.UnknownHostException
import javax.net.ssl.SSLException

data class YouTubeTimeouts(val connectMs: Long = 10000, val readMs: Long = 20000,
    val metadataMs: Long = 60000, val mediaMs: Long = 120000)

enum class DownloadProblem(val text: String) {
    DNS("無法解析 YouTube 主機名稱，請檢查網路"),
    NETWORK("無法連線 YouTube 音訊主機，請檢查網路"),
    CONNECT_TIMEOUT("YouTube 音訊連線逾時"), READ_TIMEOUT("YouTube 音訊讀取逾時"),
    CALL_TIMEOUT("YouTube 音訊下載超過整體時間上限"), TLS("YouTube 安全連線失敗（TLS）"),
    HTTP("YouTube 音訊 HTTP 請求失敗"), REDIRECT("YouTube 重新導向次數超過安全上限"),
    UNSAFE_REDIRECT("已拒絕非 HTTPS／非 YouTube 的重新導向"),
    INVALID_URL("音訊網址無效，請重新解析影片"), EXPIRED("音訊網址已過期，請重新取得字幕"),
    LENGTH("音訊長度不符，未交給本機辨識"), EOF("音訊下載提前結束，檔案不完整"),
    RANGE("音訊範圍回應不完整或不一致，未交給本機辨識"),
    STORAGE("手機儲存空間不足或暫存檔無法寫入，無法下載音訊"),
    SIZE("音訊超過 100 MiB 安全上限"), CONTAINER("音訊格式不支援或容器不完整"),
    DECODE("Android 音訊解碼失敗，已清理暫存"), ASR("本機 SenseVoice 辨識失敗，已保留完成字幕"),
    CANCELLED("已取消音訊下載；已完成字幕保留")
}

class DownloadFailure(val problem: DownloadProblem, val status: Int? = null) : YouTubeFailure(
    when (status) {
        400 -> "YouTube 音訊請求無效（HTTP 400），請重新解析影片。"
        401 -> "YouTube 音訊需要驗證（HTTP 401），Standalone 不提供登入。"
        403 -> "YouTube 拒絕音訊下載（HTTP 403）。目前的 Standalone 擷取方式無法取得此音訊。"
        404 -> "YouTube 音訊不存在或已失效（HTTP 404），請重新解析影片。"
        416 -> "YouTube 拒絕音訊範圍（HTTP 416），請重新解析影片。"
        null -> problem.text
        else -> if (status >= 500) "YouTube 音訊伺服器錯誤（HTTP $status），請稍後重試。"
            else "YouTube 音訊請求失敗（HTTP $status），請稍後重試。"
    } + " [${problem.name}${status?.let { "_$it" } ?: ""}]"
)

/** Only allowlisted, bounded fields; never accept exception messages or complete URLs. */
data class DownloadDiagnostic(val host: String = "", val status: Int = 0, val mime: String = "",
    val expected: Long = -1, val downloaded: Long = 0, val redirects: Int = 0,
    val range: Boolean = false, val problem: DownloadProblem? = null) {
    fun safeText(): String {
        val safeHost = host.takeIf { it.length <= 253 && Regex("[a-zA-Z0-9.-]+").matches(it) } ?: "redacted"
        val safeMime = mime.takeIf { Regex("[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+").matches(it) } ?: "unknown"
        return "host=$safeHost status=$status mime=$safeMime expected=$expected downloaded=$downloaded redirects=$redirects range=$range problem=${problem?.name ?: "none"}"
    }
}

internal fun downloadException(error: IOException, phase: String): DownloadFailure = DownloadFailure(when {
    error is UnknownHostException -> DownloadProblem.DNS
    error is SSLException -> DownloadProblem.TLS
    error is SocketTimeoutException -> if (phase == "connect") DownloadProblem.CONNECT_TIMEOUT else DownloadProblem.READ_TIMEOUT
    error is InterruptedIOException -> DownloadProblem.CALL_TIMEOUT
    error is EOFException || (error is ProtocolException && error.message.orEmpty().contains("unexpected end", true)) -> DownloadProblem.EOF
    error is ConnectException || error is NoRouteToHostException -> DownloadProblem.NETWORK
    else -> DownloadProblem.NETWORK
})

internal fun checkStreamExpiry(url: String, nowSeconds: Long = System.currentTimeMillis() / 1000) {
    val uri = try { URI(url) } catch (_: Exception) { throw DownloadFailure(DownloadProblem.INVALID_URL) }
    if (uri.host == null || uri.userInfo != null) throw DownloadFailure(DownloadProblem.INVALID_URL)
    val expiry = uri.rawQuery?.split('&')?.firstOrNull { it.startsWith("expire=") }?.substringAfter('=')?.toLongOrNull()
    if (expiry != null && expiry <= nowSeconds) throw DownloadFailure(DownloadProblem.EXPIRED)
}

/** A fresh transfer only: no resume, no concatenation of arbitrary partial responses. */
internal fun completeResponseLength(status: Int, length: Long, range: String?, expected: Long): Long {
    val total = if (status == 206) {
        val match = Regex("bytes (\\d+)-(\\d+)/(\\d+)").matchEntire(range.orEmpty())
            ?: throw DownloadFailure(DownloadProblem.RANGE)
        val numbers = match.groupValues.drop(1).map { it.toLongOrNull() ?: throw DownloadFailure(DownloadProblem.RANGE) }
        val (start, end, size) = numbers
        if (start != 0L || size <= 0 || end != size - 1 || (length >= 0 && length != size)) throw DownloadFailure(DownloadProblem.RANGE)
        size
    } else {
        if (status != 200 || range != null) throw DownloadFailure(DownloadProblem.RANGE)
        length
    }
    if (expected > 0 && total >= 0 && total != expected) throw DownloadFailure(DownloadProblem.LENGTH)
    return if (total >= 0) total else expected
}

internal const val AUDIO_RANGE_BYTES = 1024L * 1024
internal fun chunkResponseLength(range: String?, length: Long, start: Long, end: Long, expected: Long): Long {
    val m = Regex("bytes (\\d+)-(\\d+)/(\\d+)").matchEntire(range.orEmpty()) ?: throw DownloadFailure(DownloadProblem.RANGE)
    val v = m.groupValues.drop(1).map { it.toLongOrNull() ?: throw DownloadFailure(DownloadProblem.RANGE) }
    if (v[2] <= start || v[0] != start || v[1] != minOf(end, v[2] - 1) ||
        (length >= 0 && length != v[1] - v[0] + 1)) throw DownloadFailure(DownloadProblem.RANGE)
    if (expected > 0 && expected != v[2]) throw DownloadFailure(DownloadProblem.LENGTH)
    return v[2]
}

/** Sequential bounded ranges are one fresh transfer, not persistent resume or retries.
 * The CDN throttles the sample's unbounded GET to ~32 KiB/s; the old whole-call 60s cap
 * aborted a valid HTTP 200. Small standard ranges avoid the unbounded request pattern.
 */
internal fun downloadYouTubeAudio(http: YouTubeHttp, cache: YouTubeCache, audio: YouTubeAudio,
    target: File, check: () -> Unit, progress: (Long, Long) -> Unit) {
    if (!audio.direct || audio.format != "M4A") throw DownloadFailure(DownloadProblem.CONTAINER)
    var expected = audio.expectedBytes.takeIf { it > 0 } ?: -1
    var done = 0L
    var etag: String? = null
    val started = System.nanoTime()
    fun checkpoint() {
        check()
        if (System.nanoTime() - started > 15L * 60 * 1_000_000_000) throw DownloadFailure(DownloadProblem.CALL_TIMEOUT)
    }
    try {
        checkStreamExpiry(audio.url)
        cache.receive(target, { expected }, ::checkpoint, { bytes, total ->
            done = bytes; http.report(http.diagnostic.copy(expected = total, downloaded = bytes)); progress(bytes, total)
        }) { accept ->
            var requests = 0
            do {
                checkpoint(); checkStreamExpiry(audio.url)
                if (++requests > 100) throw DownloadFailure(DownloadProblem.SIZE)
                val start = done
                val end = minOf(start + AUDIO_RANGE_BYTES - 1, if (expected > 0) expected - 1 else Long.MAX_VALUE)
                val headers = mapOf("Range" to listOf("bytes=$start-$end"), "Accept-Encoding" to listOf("identity"))
                http.response(audio.url, headers = headers, media = true) { response ->
                    val body = response.body ?: throw DownloadFailure(DownloadProblem.EOF)
                    val encoding = response.header("Content-Encoding")
                    if (encoding != null && !encoding.equals("identity", true)) throw DownloadFailure(DownloadProblem.CONTAINER)
                    val mime = body.contentType()?.let { "${it.type}/${it.subtype}" }
                    if (mime != null && mime !in setOf("audio/mp4", "video/mp4", "application/octet-stream")) throw DownloadFailure(DownloadProblem.CONTAINER)
                    val tag = response.header("ETag")
                    if (etag != null && tag != etag) throw DownloadFailure(DownloadProblem.LENGTH)
                    if (start == 0L) etag = tag
                    val length: Long
                    if (response.code == 206) {
                        expected = chunkResponseLength(response.header("Content-Range"), body.contentLength(), start, end, expected)
                        length = minOf(end, expected - 1) - start + 1
                    } else {
                        // A server may ignore Range only on the first request. Never append a 200 to prior bytes.
                        if (start != 0L) throw DownloadFailure(DownloadProblem.RANGE)
                        expected = completeResponseLength(response.code, body.contentLength(), response.header("Content-Range"), expected)
                        length = expected
                    }
                    http.report(http.diagnostic.copy(expected = expected, downloaded = done))
                    body.byteStream().use { accept(it, length) }
                    if (response.code == 200 && expected < 0) expected = done
                }
            } while (done < expected)
        }
    } catch (e: StopRequested) {
        http.report(http.diagnostic.copy(expected = expected, downloaded = done, problem = DownloadProblem.CANCELLED)); throw e
    } catch (e: DownloadFailure) {
        http.report(http.diagnostic.copy(expected = expected, downloaded = done, problem = e.problem)); throw e
    }
}

/** Structural ISO-BMFF check, not a replacement for Android's codec validation. */
internal fun validateM4a(file: File) {
    RandomAccessFile(file, "r").use { input ->
        var offset = 0L; var boxes = 0
        var ftyp = false; var moov = false; var mdat = false
        while (offset < input.length()) {
            if (++boxes > 100000 || input.length() - offset < 8) throw DownloadFailure(DownloadProblem.CONTAINER)
            input.seek(offset)
            var size = input.readInt().toLong() and 0xffffffffL
            val name = ByteArray(4); input.readFully(name)
            val type = String(name, Charsets.US_ASCII)
            var header = 8L
            if (size == 1L) { if (input.length() - offset < 16) throw DownloadFailure(DownloadProblem.CONTAINER); size = input.readLong(); header = 16 }
            if (size == 0L) size = input.length() - offset
            if (size < header || size > input.length() - offset) throw DownloadFailure(DownloadProblem.CONTAINER)
            if (type == "ftyp" && offset == 0L && size >= 16) ftyp = true
            if (type == "moov" && size > header) moov = true
            if (type == "mdat" && size > header) mdat = true
            offset += size
        }
        if (!ftyp || !moov || !mdat) throw DownloadFailure(DownloadProblem.CONTAINER)
    }
}

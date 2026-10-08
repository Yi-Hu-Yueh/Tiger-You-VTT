package tw.tiger.tigeryouvtt.standalone

import java.io.File
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.URI
import java.net.UnknownHostException
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import okhttp3.Call
import okhttp3.CookieJar
import okhttp3.OkHttpClient
import okhttp3.RequestBody.Companion.toRequestBody
import org.schabi.newpipe.extractor.MediaFormat
import org.schabi.newpipe.extractor.NewPipe
import org.schabi.newpipe.extractor.ServiceList
import org.schabi.newpipe.extractor.downloader.Downloader
import org.schabi.newpipe.extractor.downloader.Request
import org.schabi.newpipe.extractor.downloader.Response
import org.schabi.newpipe.extractor.search.SearchExtractor
import org.schabi.newpipe.extractor.stream.DeliveryMethod
import org.schabi.newpipe.extractor.stream.StreamExtractor
import org.schabi.newpipe.extractor.stream.StreamInfoItem

fun allowedYoutubeResource(url: String): Boolean = runCatching {
    val uri = URI(url); val host = uri.host?.lowercase() ?: return@runCatching false
    uri.scheme == "https" && uri.userInfo == null && uri.port == -1 &&
        listOf("youtube.com", "youtu.be", "googlevideo.com", "ytimg.com", "google.com", "googleapis.com", "ggpht.com")
            .any { host == it || host.endsWith(".$it") }
}.getOrDefault(false)

fun youtubeError(error: Throwable): String {
    if (error is YouTubeFailure) return error.userMessage
    val causes = generateSequence(error) { it.cause }.take(8).toList()
    val details = causes.joinToString(" ") { "${it.javaClass.simpleName} ${it.message}" }.lowercase()
    return when {
        listOf("recaptcha", "captcha", "sign in", "login", "log in", "authentication", "confirm you're not a bot", "age-restricted").any { it in details } ->
            "此影片目前需要 YouTube 驗證，Standalone 模式暫不支援此驗證方式。"
        causes.any { it is SocketTimeoutException } -> "YouTube 連線逾時，請稍後重試"
        causes.any { it is UnknownHostException } -> "無法連線 YouTube，請檢查網路；離線核心仍可使用"
        causes.any { it is IOException } -> "YouTube 網路或下載失敗，請檢查連線後重試"
        "contentnotavailable" in details -> "影片目前無法公開存取，可能已移除、受地區限制或需要驗證"
        else -> "YouTube 解析失敗，網站格式可能已變更；請稍後更新擷取器或手動使用裝置音訊"
    }
}

/** Constructed only inside an explicit YouTube job. No cookies, logging or startup requests. */
class YouTubeHttp(private val cancel: YouTubeCancel,
    private val timeouts: YouTubeTimeouts = YouTubeTimeouts(),
    private val observer: (DownloadDiagnostic) -> Unit = {},
    private val allowed: (String) -> Boolean = ::allowedYoutubeResource) : Downloader() {
    private val client = OkHttpClient.Builder().cookieJar(CookieJar.NO_COOKIES)
        .connectTimeout(timeouts.connectMs, TimeUnit.MILLISECONDS).readTimeout(timeouts.readMs, TimeUnit.MILLISECONDS)
        .callTimeout(timeouts.metadataMs, TimeUnit.MILLISECONDS).followRedirects(false).followSslRedirects(false)
        .retryOnConnectionFailure(false).build()
    private val active = AtomicReference<Call?>(null)
    var diagnostic = DownloadDiagnostic(); private set
    fun report(value: DownloadDiagnostic) { diagnostic = value; observer(value) }
    init { cancel.abortHttp = { active.get()?.cancel() } }
    fun <T> response(url: String, method: String = "GET", headers: Map<String, List<String>> = emptyMap(),
        data: ByteArray? = null, media: Boolean = false, consume: (okhttp3.Response) -> T): T {
        var destination = url
        repeat(6) { redirects ->
            cancel.check()
            if (!allowed(destination)) throw DownloadFailure(if (redirects == 0) DownloadProblem.INVALID_URL else DownloadProblem.UNSAFE_REDIRECT)
            if (media) report(DownloadDiagnostic(host = URI(destination).host.orEmpty(), redirects = redirects, range = headers.keys.any { it.equals("Range", true) }))
            val builder = okhttp3.Request.Builder().url(destination).header("User-Agent", "Mozilla/5.0")
            headers.forEach { (name, values) ->
                // No account cookies/Authorization; extractor-supplied anonymous consent is unnecessary here.
                if (!name.equals("Cookie", true) && !name.equals("Authorization", true)) values.forEach { builder.addHeader(name, it) }
            }
            builder.method(method, data?.toRequestBody() ?: if (method == "POST") ByteArray(0).toRequestBody() else null)
            var phase = "connect"
            val requestClient = client.newBuilder().eventListener(object : okhttp3.EventListener() {
                override fun connectionAcquired(call: Call, connection: okhttp3.Connection) { phase = "read" }
            }).callTimeout(if (media) timeouts.mediaMs else timeouts.metadataMs, TimeUnit.MILLISECONDS).build()
            val call = requestClient.newCall(builder.build()); active.set(call)
            try {
                cancel.check()
                call.execute().use { result ->
                    if (media) report(diagnostic.copy(status = result.code, mime = result.body?.contentType()?.let { "${it.type}/${it.subtype}" }.orEmpty(), expected = result.body?.contentLength() ?: -1))
                    if (result.code in setOf(301, 302, 303, 307, 308)) {
                        if (redirects == 5) throw DownloadFailure(DownloadProblem.REDIRECT)
                        if (method != "GET") throw YouTubeFailure("YouTube 請求重新導向無法安全處理")
                        destination = try { result.header("Location")?.let { URI(destination).resolve(it).toString() }
                            ?: throw DownloadFailure(DownloadProblem.UNSAFE_REDIRECT) } catch (_: java.net.URISyntaxException) { throw DownloadFailure(DownloadProblem.UNSAFE_REDIRECT)
                            } catch (_: IllegalArgumentException) { throw DownloadFailure(DownloadProblem.UNSAFE_REDIRECT) }
                    } else {
                        if (media && !result.isSuccessful) throw DownloadFailure(DownloadProblem.HTTP, result.code)
                        if (result.code in setOf(401, 403)) throw YouTubeFailure("此影片目前需要 YouTube 驗證或拒絕存取，Standalone 模式暫不支援此驗證方式。")
                        if (result.code == 429) throw YouTubeFailure("YouTube 暫時限制請求，請稍後再試")
                        if (!result.isSuccessful) throw IOException("YouTube HTTP ${result.code}")
                        cancel.check(); return consume(result)
                    }
                }
            } catch (e: IOException) {
                cancel.check()
                if (media) throw downloadException(e, phase) else throw e
            } finally { active.compareAndSet(call, null) }
        }
        throw DownloadFailure(DownloadProblem.REDIRECT)
    }
    override fun execute(request: Request): Response = response(request.url(), request.httpMethod(), request.headers(), request.dataToSend()) { result ->
        val text = limitedText(result, 8 * 1024 * 1024)
        Response(result.code, result.message, result.headers.toMultimap(), text, result.request.url.toString())
    }
    fun limitedText(response: okhttp3.Response, limit: Int): String {
        val body = response.body ?: throw IOException("Empty response")
        if (body.contentLength() > limit) throw YouTubeFailure("YouTube 回應超過安全上限")
        val out = java.io.ByteArrayOutputStream()
        body.byteStream().use { input ->
            val buf = ByteArray(32768)
            while (true) {
                cancel.check(); val n = input.read(buf); if (n < 0) break
                if (out.size() + n > limit) throw YouTubeFailure("YouTube 回應超過安全上限")
                out.write(buf, 0, n)
            }
        }
        return out.toString("UTF-8")
    }
}

// MediaFormat.name is a human-readable field, not the enum identifier (M4A displays as m4a).
internal fun youtubeAudioFormat(format: MediaFormat?): String = if (format == MediaFormat.M4A) "M4A" else format?.toString() ?: "unknown"

class NewPipeYouTubeSource(private val http: YouTubeHttp, private val cache: YouTubeCache) : YouTubeSource {
    private var extracted: StreamExtractor? = null
    init { NewPipe.init(http) } // YouTubeService serializes all extractor operations; never app-wide startup.
    override fun search(query: String): List<YouTubeItem> {
        val extractor = ServiceList.YouTube.getSearchExtractor(youtubeQuery(query))
        return try {
            extractor.fetchPage()
            extractor.initialPage.items.filterIsInstance<StreamInfoItem>().mapNotNull { item ->
                runCatching { YouTubeItem(youtubeId(item.url), item.name.take(500), item.uploaderName.take(300), item.duration) }.getOrNull()
            }.distinctBy { it.id }.take(20)
        } catch (_: SearchExtractor.NothingFoundException) { emptyList() }
    }
    override fun video(url: String): YouTubeVideo {
        val id = youtubeId(url)
        val extractor = ServiceList.YouTube.getStreamExtractor("https://www.youtube.com/watch?v=$id")
        extractor.fetchPage(); extracted = extractor
        return YouTubeVideo(YouTubeItem(id, extractor.name.take(500), extractor.uploaderName.take(300), extractor.length),
            extractor.getSubtitles(MediaFormat.VTT).filter { it.isUrl }.map { YouTubeCaption(it.languageTag, it.isAutoGenerated, it.content) })
    }
    override fun caption(track: YouTubeCaption) = http.response(track.url) { http.limitedText(it, 2_000_000) }
    override fun audio(video: YouTubeVideo): List<YouTubeAudio> {
        val extractor = requireNotNull(extracted)
        check(youtubeId(extractor.url) == video.item.id)
        return extractor.audioStreams.map { YouTubeAudio(it.content, youtubeAudioFormat(it.format), it.averageBitrate,
            it.isUrl && it.deliveryMethod == DeliveryMethod.PROGRESSIVE_HTTP,
            it.itagItem?.contentLength ?: -1, it.itag, it.format?.mimeType.orEmpty(), it.deliveryMethod.toString()) }
    }
    override fun download(audio: YouTubeAudio, target: File, check: () -> Unit, progress: (Long, Long) -> Unit) {
        downloadYouTubeAudio(http, cache, audio, target, check, progress)
    }
}

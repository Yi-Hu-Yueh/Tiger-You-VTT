package tw.tiger.tigeryouvtt.standalone

import java.io.File
import java.io.InputStream
import java.net.URI
import java.net.URLDecoder
import java.util.concurrent.atomic.AtomicBoolean
import org.jsoup.Jsoup

open class YouTubeFailure(val userMessage: String) : RuntimeException(userMessage)
data class YouTubeItem(val id: String, val title: String, val channel: String, val seconds: Long) {
    val url get() = "https://www.youtube.com/watch?v=$id"
}
data class YouTubeCaption(val language: String, val automatic: Boolean, val url: String)
data class YouTubeAudio(val url: String, val format: String, val bitrate: Int, val direct: Boolean,
    val expectedBytes: Long = -1, val itag: Int = -1, val mime: String = "", val delivery: String = "PROGRESSIVE_HTTP") {
    override fun toString() = "YouTubeAudio(format=$format, bitrate=$bitrate, direct=$direct, expectedBytes=$expectedBytes, itag=$itag)"
}
data class YouTubeVideo(val item: YouTubeItem, val captions: List<YouTubeCaption>)

fun youtubeId(input: String): String {
    try {
        val uri = URI(input.trim())
        require(uri.scheme.equals("https", true) && uri.userInfo == null && uri.port == -1)
        val host = uri.host?.lowercase() ?: error("host")
        val parts = uri.path.split('/').filter { it.isNotEmpty() }
        val id = when {
            host == "youtu.be" && parts.size == 1 -> parts[0]
            host in setOf("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com") -> {
                if (uri.path == "/watch") {
                    val values = (uri.rawQuery ?: "").split('&').map { it.split('=', limit = 2) }
                        .filter { it[0] == "v" }.map { URLDecoder.decode(it.getOrElse(1) { "" }, "UTF-8") }
                    require(values.size == 1); values.single()
                } else { require(parts.size == 2 && parts[0] in setOf("shorts", "live", "embed")); parts[1] }
            }
            else -> error("host")
        }
        require(Regex("[A-Za-z0-9_-]{11}").matches(id)); return id
    } catch (_: Exception) { throw YouTubeFailure("請輸入有效的 HTTPS YouTube 影片網址（watch、youtu.be、shorts、live 或 embed）") }
}
fun youtubeQuery(query: String): String = query.trim().also {
    if (it.isEmpty() || it.length > 200) throw YouTubeFailure("請輸入 1–200 字的搜尋關鍵字")
}
fun preferredCaptions(tracks: List<YouTubeCaption>): List<YouTubeCaption> = tracks.sortedWith(
    compareBy<YouTubeCaption> {
        val lang = it.language.lowercase()
        when { lang == "zh-tw" -> 0; lang == "zh" || lang.startsWith("zh-") -> 1; lang == "en" || lang.startsWith("en-") -> 2; else -> 3 }
    }.thenBy { it.automatic }.thenBy { it.language })
fun preferredAudio(streams: List<YouTubeAudio>): YouTubeAudio = streams
    .filter { it.direct && it.format == "M4A" }
    .minByOrNull { kotlin.math.abs(it.bitrate - 96) }
    ?: throw YouTubeFailure("目前沒有支援的 M4A 音訊直連；不下載影片或繞過驗證，可手動使用裝置音訊")

/** Only the extractor's explicitly requested WebVTT format is accepted. */
fun parseYoutubeVtt(content: String): List<TranscriptSegment> {
    if (content.length > 2_000_000) throw YouTubeFailure("字幕檔案超過安全上限")
    val normalized = content.removePrefix("\uFEFF").replace("\r\n", "\n").replace('\r', '\n')
    if (!normalized.startsWith("WEBVTT")) throw YouTubeFailure("YouTube 未回傳可用的 WebVTT 字幕")
    fun time(s: String): Double {
        require(Regex("(?:\\d{2,}:)?[0-5]\\d:[0-5]\\d\\.\\d{3}").matches(s))
        val p = s.split(':'); return if (p.size == 3) p[0].toDouble() * 3600 + p[1].toDouble() * 60 + p[2].toDouble()
            else p[0].toDouble() * 60 + p[1].toDouble()
    }
    try {
        val cues = normalized.split(Regex("\n[ \t]*\n")).drop(1).mapNotNull { block ->
            val lines = block.lines()
            if (lines.first().startsWith("NOTE") || lines.first() in setOf("STYLE", "REGION")) return@mapNotNull null
            val index = lines.indexOfFirst { it.contains(" --> ") }
            if (index < 0) return@mapNotNull null
            val times = lines[index].trim().split(Regex("\\s+"))
            val start = time(times[0]); val end = time(times[2])
            require(end > start && end <= 24 * 3600)
            val raw = lines.drop(index + 1).joinToString(" ").replace(Regex("<\\d{2}:\\d{2}[^>]*>"), "")
            val text = Jsoup.parseBodyFragment(raw).text().trim()
            if (text.isBlank()) null else TranscriptSegment(start, end, text)
        }
        require(cues.size <= 10000 && cues.sumOf { it.text.length } <= 1_000_000)
        require(cues.zipWithNext().all { (a, b) -> b.start >= a.start })
        val result = mutableListOf<TranscriptSegment>()
        for (cue in cues) {
            val previous = result.lastOrNull()
            if (previous != null && previous.start == cue.start) {
                result[result.lastIndex] = previous.copy(end = maxOf(previous.end, cue.end), text = previous.text + " " + cue.text)
            } else {
                // Canonical exporters require non-overlap: clip only an overlapping predecessor.
                if (previous != null && previous.end > cue.start) result[result.lastIndex] = previous.copy(end = cue.start)
                result.add(cue)
            }
        }
        Export.render(result, "vtt"); return result
    } catch (e: YouTubeFailure) { throw e
    } catch (_: Exception) { throw YouTubeFailure("YouTube 字幕格式或時間戳無法解析") }
}

class YouTubeCancel {
    private val stopped = AtomicBoolean()
    @Volatile var abortHttp: (() -> Unit)? = null
    fun stop() { stopped.set(true); abortHttp?.invoke() }
    fun check() { if (stopped.get()) throw StopRequested() }
    fun isStopped() = stopped.get()
}
interface YouTubeSource {
    fun search(query: String): List<YouTubeItem>
    fun video(url: String): YouTubeVideo
    fun caption(track: YouTubeCaption): String
    fun audio(video: YouTubeVideo): List<YouTubeAudio>
    fun download(audio: YouTubeAudio, target: File, check: () -> Unit, progress: (Long, Long) -> Unit)
}
class YouTubeCache(private val root: File) {
    companion object { const val MAX_BYTES = 100L * 1024 * 1024; const val RESERVE = 32L * 1024 * 1024 }
    fun prepare() {
        if (!root.mkdirs() && !root.isDirectory) throw DownloadFailure(DownloadProblem.STORAGE)
        root.listFiles()?.filter { it.isFile && Regex("yt-[A-Za-z0-9_-]+\\.m4a(?:\\.part)?").matches(it.name) }?.forEach {
            if (!it.delete()) throw YouTubeFailure("無法清理先前暫存音訊，請釋放儲存空間後重試")
        }
    }
    fun create(): File = File(root, "yt-${java.util.UUID.randomUUID()}.m4a")
    fun copy(input: InputStream, target: File, length: Long, check: () -> Unit, progress: (Long, Long) -> Unit) {
        receive(target, { length }, check, progress, false) { accept -> accept(input, length) }
    }
    fun receive(target: File, expected: () -> Long, check: () -> Unit, progress: (Long, Long) -> Unit,
        validate: Boolean = true, transfer: ((InputStream, Long) -> Unit) -> Unit) {
        require(target.canonicalFile.parentFile == root.canonicalFile)
        val part = File(target.path + ".part")
        if (target.exists() || part.exists()) throw DownloadFailure(DownloadProblem.STORAGE)
        fun capacity() {
            if (expected() > MAX_BYTES) throw DownloadFailure(DownloadProblem.SIZE)
            if (root.usableSpace < (if (expected() >= 0) expected() else MAX_BYTES) + RESERVE)
                throw DownloadFailure(DownloadProblem.STORAGE)
        }
        try {
            capacity(); check()
            val out = try { part.outputStream() } catch (_: java.io.IOException) { throw DownloadFailure(DownloadProblem.STORAGE) }
            var total = 0L
            try {
                transfer { input, length ->
                    if (expected() > MAX_BYTES) throw DownloadFailure(DownloadProblem.SIZE)
                    val buffer = ByteArray(65536); var received = 0L
                    while (true) {
                        check(); val n = input.read(buffer); if (n < 0) break
                        received += n; total += n
                        if (total > MAX_BYTES) throw DownloadFailure(DownloadProblem.SIZE)
                        if ((length >= 0 && received > length) || (expected() >= 0 && total > expected())) throw DownloadFailure(DownloadProblem.LENGTH)
                        try { out.write(buffer, 0, n) } catch (_: java.io.IOException) { throw DownloadFailure(DownloadProblem.STORAGE) }
                        progress(total, expected())
                    }
                    if (length >= 0 && received != length) throw DownloadFailure(DownloadProblem.EOF)
                }
                check()
                if (total == 0L || (expected() >= 0 && total != expected())) throw DownloadFailure(DownloadProblem.LENGTH)
                try { out.fd.sync() } catch (_: java.io.IOException) { throw DownloadFailure(DownloadProblem.STORAGE) }
            } finally {
                try { out.close() } catch (_: java.io.IOException) { throw DownloadFailure(DownloadProblem.STORAGE) }
            }
            if (validate) try { validateM4a(part) } catch (_: java.io.IOException) { throw DownloadFailure(DownloadProblem.CONTAINER) }
            check()
            if (!part.renameTo(target)) throw DownloadFailure(DownloadProblem.STORAGE)
        } catch (e: Throwable) {
            val partClean = !part.exists() || part.delete()
            val finalClean = !target.exists() || target.delete()
            if (!partClean || !finalClean) throw DownloadFailure(DownloadProblem.STORAGE)
            throw e
        }
    }
}

/** Caption-only never acquires the ASR gate. Fallback owns it until decode/native cleanup completes. */
class YouTubeWorkflow(private val source: YouTubeSource, private val cache: YouTubeCache,
    private val gate: JobGate, private val ready: () -> Boolean, private val cancel: YouTubeCancel) {
    fun retrieve(url: String, status: (String) -> Unit, video: (YouTubeVideo) -> Unit,
        provenance: (String) -> Unit, modelOwnership: (Boolean) -> Unit,
        asr: (File, () -> Unit) -> List<TranscriptSegment>): List<TranscriptSegment> {
        cancel.check(); status("解析影片")
        val info = source.video("https://www.youtube.com/watch?v=${youtubeId(url)}"); video(info)
        cancel.check(); status("取得 YouTube 字幕")
        for (track in preferredCaptions(info.captions).take(12)) {
            cancel.check()
            val body = source.caption(track) // Network/auth failures are not disguised as missing captions.
            val parsed = try { parseYoutubeVtt(body) } catch (_: YouTubeFailure) { emptyList() }
            if (parsed.isNotEmpty()) {
                cancel.check(); provenance("YouTube ${if (track.automatic) "自動字幕" else "人工字幕"} / ${track.language}")
                return parsed
            }
        }
        status("找不到可用字幕，準備本機語音辨識")
        if (!ready()) throw YouTubeFailure("內建模型尚未就緒，請先完成離線模型準備")
        if (!gate.start()) throw YouTubeFailure("模型使用中，請先停止本機檔案、麥克風或裝置音訊工作")
        var file: File? = null
        try {
            modelOwnership(true)
            val check = { cancel.check(); gate.check() }
            check()
            if (info.item.seconds !in 1..3600) throw YouTubeFailure("音訊轉錄限 60 分鐘內的非直播影片")
            status("準備音訊來源")
            val stream = preferredAudio(source.audio(info)); check()
            cache.prepare(); file = cache.create()
            provenance("下載 M4A 音訊 → 本機 SenseVoice（非逐字時間對齊）")
            source.download(stream, file, check) { done, total ->
                val mb = "%.2f".format(java.util.Locale.ROOT, done / 1_000_000.0)
                status("下載音訊：已下載 $mb MB" + if (total > 0) "（${done * 100 / total}%）" else "")
            }
            check(); status("下載完成，準備解碼音訊")
            return asr(file, check)
        } finally {
            val cleaned = file?.let { !it.exists() || it.delete() } ?: true
            try { modelOwnership(false) } finally { gate.finish() }
            if (!cleaned) throw YouTubeFailure("暫存音訊清理失敗；下次使用 YouTube 時將再次清理，已完成字幕仍保留")
        }
    }
}

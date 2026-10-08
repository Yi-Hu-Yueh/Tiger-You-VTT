package tw.tiger.tigeryouvtt.standalone

import java.nio.file.Files

/** Manually invoked production-adapter smoke; never a JUnit test or APK component. */
object YouTubeSmoke {
    @JvmStatic fun main(args: Array<String>) {
        require(args.contentEquals(arrayOf("--live")))
        val root = Files.createTempDirectory("tiger-youtube-smoke-").toFile()
        val token = YouTubeCancel()
        val watchdog = Thread { try { Thread.sleep(300000); token.stop() } catch (_: InterruptedException) {} }.apply { isDaemon = true; start() }
        try {
            val cache = YouTubeCache(root); cache.prepare()
            val http = YouTubeHttp(token); val source = NewPipeYouTubeSource(http, cache)
            val search = source.search("Rick Astley Never Gonna Give You Up")
            check(search.isNotEmpty()); println("SEARCH_PASS results=${search.size}")
            val url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            val gate = JobGate(); gate.start() // Prove real caption retrieval can run while model is unavailable.
            val captions = YouTubeWorkflow(source, cache, gate, { false }, token).retrieve(url, {},
                { println("CAPTION_VIDEO id=${it.item.id} tracks=${it.captions.size}") }, { println("CAPTION_SOURCE $it") }, {}) { _, _ -> error("ASR must not run") }
            check(captions.isNotEmpty() && gate.busy())
            listOf("txt", "vtt", "srt").forEach { check(Export.render(captions, it).isNotEmpty()) }
            println("D1_PASS id=dQw4w9WgXcQ segments=${captions.size} exports=TXT,VTT,SRT no_asr=true")
            val noCaption = source.video("https://www.youtube.com/watch?v=Y_5hEaDzAsE")
            check(noCaption.captions.isEmpty()) { "Sample now has captions; choose a new no-caption sample" }
            val audio = preferredAudio(source.audio(noCaption))
            println("D2_STREAM id=Y_5hEaDzAsE captions=0 format=${audio.format} bitrate=${audio.bitrate} host=${java.net.URI(audio.url).host} direct=${audio.direct} itag=${audio.itag} expected=${audio.expectedBytes} delivery=${audio.delivery}")
            val target = cache.create()
            val started = System.nanoTime()
            var received = 0L
            try {
                // Exact production download path, including every range and final .part promotion.
                source.download(audio, target, token::check) { done, _ -> received = done }
                println("D2_RESPONSE ${http.diagnostic.safeText()}")
                validateM4a(target)
                check(!java.io.File(target.path + ".part").exists())
                println("M4A_COMPLETE_BOX_VALIDATION_PASS ftyp=true moov=true mdat=true no_partial=true")
                println("D2_FULL_DOWNLOAD_PASS bytes=${target.length()} elapsed_ms=${(System.nanoTime()-started)/1000000}")
            } catch (e: Exception) {
                println("D2_FULL_DOWNLOAD_FAIL type=${e.javaClass.simpleName} downloaded=$received elapsed_ms=${(System.nanoTime()-started)/1000000} diagnostic=${http.diagnostic.safeText()} safe_error=${youtubeError(e)}")
                throw IllegalStateException("Full download failed; see redacted evidence above")
            }
            println("PHONE_DECODE_ASR_NOT_TESTED")
        } finally {
            token.stop(); watchdog.interrupt()
            root.listFiles()?.forEach { it.delete() }; root.delete()
        }
    }
}

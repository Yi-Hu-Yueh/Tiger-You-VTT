package tw.tiger.tigeryouvtt.standalone

import android.content.Context
import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import java.nio.ByteOrder

/** Android-only compressed media -> mono float PCM 16 kHz, bounded and backpressured. */
class MediaDecoder(private val context: Context) {
    fun decode(uri: Uri, check: () -> Unit, consume: (FloatArray, Double, Double) -> Unit) {
        val extractor = MediaExtractor()
        var decoder: MediaCodec? = null
        try {
            extractor.setDataSource(context, uri, null)
            val track = (0 until extractor.trackCount).firstOrNull {
                extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
            } ?: error("檔案沒有可解碼的音軌")
            extractor.selectTrack(track)
            val format = extractor.getTrackFormat(track)
            if (format.containsKey(MediaFormat.KEY_DURATION)) require(format.getLong(MediaFormat.KEY_DURATION) <= 3600_000_000L) { "此版本限 60 分鐘內檔案" }
            var rate = format.getInteger(MediaFormat.KEY_SAMPLE_RATE)
            var channels = format.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
            var encoding = AudioFormat.ENCODING_PCM_16BIT
            val codec = MediaCodec.createDecoderByType(requireNotNull(format.getString(MediaFormat.KEY_MIME)))
            decoder = codec
            codec.configure(format, null, null, 0)
            codec.start()
            var origin: Double? = null
            val windows = PcmWindows { pcm, start, end -> consume(pcm, start + (origin ?: 0.0), end + (origin ?: 0.0)) }
            var resampler = Resampler(rate, windows::add)
            var decodedFrames = 0L
            var inputEnded = false
            var outputEnded = false
            var lastProgress = System.nanoTime()
            val info = MediaCodec.BufferInfo()
            while (!outputEnded) {
                check()
                require(System.nanoTime() - lastProgress < 30_000_000_000L) { "音訊解碼逾時" }
                if (!inputEnded) {
                    val index = codec.dequeueInputBuffer(10000)
                    if (index >= 0) {
                        val buffer = requireNotNull(codec.getInputBuffer(index))
                        val size = extractor.readSampleData(buffer, 0)
                        if (size < 0) {
                            codec.queueInputBuffer(index, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            inputEnded = true
                        } else {
                            codec.queueInputBuffer(index, 0, size, extractor.sampleTime, 0)
                            extractor.advance()
                        }
                        lastProgress = System.nanoTime()
                    }
                }
                when (val index = codec.dequeueOutputBuffer(info, 10000)) {
                    MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                        val output = codec.outputFormat
                        val nextRate = output.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                        require(decodedFrames == 0L || nextRate == rate) { "不支援中途變更取樣率的音軌" }
                        rate = nextRate; channels = output.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                        encoding = if (output.containsKey(MediaFormat.KEY_PCM_ENCODING)) output.getInteger(MediaFormat.KEY_PCM_ENCODING) else AudioFormat.ENCODING_PCM_16BIT
                        validatePcmFormat(rate, channels, encoding)
                        if (decodedFrames == 0L) resampler = Resampler(rate, windows::add)
                    }
                    else -> if (index >= 0) {
                        try {
                            if (info.size > 0) {
                                if (origin == null) origin = (info.presentationTimeUs / 1e6).coerceAtLeast(0.0)
                                val expected = (origin ?: 0.0) + decodedFrames.toDouble() / rate
                                validateAudioTimestamp(info.presentationTimeUs / 1e6, expected)
                                val buffer = requireNotNull(codec.getOutputBuffer(index)).order(ByteOrder.LITTLE_ENDIAN)
                                buffer.position(info.offset); buffer.limit(info.offset + info.size)
                                val bytesPerFrame = channels * if (encoding == AudioFormat.ENCODING_PCM_FLOAT) 4 else 2
                                require(info.size % bytesPerFrame == 0) { "PCM frame 不完整" }
                                while (buffer.remaining() >= bytesPerFrame) {
                                    if (decodedFrames % 4096 == 0L) check()
                                    var mono = 0f
                                    repeat(channels) { mono += if (encoding == AudioFormat.ENCODING_PCM_FLOAT) buffer.float else buffer.short / 32768f }
                                    resampler.accept(mono / channels)
                                    decodedFrames++
                                }
                            }
                            outputEnded = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                        } finally { codec.releaseOutputBuffer(index, false) }
                        lastProgress = System.nanoTime() // native inference is intentional backpressure
                    }
                }
            }
            require(decodedFrames > 0) { "音軌沒有可讀取的音訊" }
            windows.flush()
        } finally {
            decoder?.let { runCatching { it.stop() }; it.release() }
            extractor.release()
        }
    }
}

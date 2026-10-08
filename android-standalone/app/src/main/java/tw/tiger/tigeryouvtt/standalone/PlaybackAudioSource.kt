package tw.tiger.tigeryouvtt.standalone

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.os.SystemClock

/** Playback-only: never creates a microphone AudioRecord or changes another app's policy. */
class PlaybackAudioSource private constructor(private val recorder: AudioRecord,
    private val closed: () -> Unit, warning: (Boolean) -> Unit) : MicPcmSource {
    override val sampleRate = recorder.sampleRate
    override val channels = recorder.channelCount
    private val silence = PlaybackSilence(warning, SystemClock::elapsedRealtime)
    override fun start() {
        android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_AUDIO)
        recorder.startRecording()
        if (recorder.recordingState != AudioRecord.RECORDSTATE_RECORDING)
            throw MicrophoneFailure("裝置音訊 AudioRecord 無法開始擷取，請重新授權後重試")
    }
    override fun read(buffer: ShortArray): Int {
        val count = recorder.read(buffer, 0, buffer.size, AudioRecord.READ_NON_BLOCKING)
        if (count >= 0) silence.accept(buffer, count)
        return count
    }
    override fun close() {
        try { if (recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING) recorder.stop() }
        finally { try { recorder.release() } finally { closed() } }
    }
    companion object {
        fun open(projection: MediaProjection, closed: () -> Unit, warning: (Boolean) -> Unit): PlaybackAudioSource {
            val capture = AudioPlaybackCaptureConfiguration.Builder(projection)
                .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                .addMatchingUsage(AudioAttributes.USAGE_GAME)
                .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN).build()
            for ((rate, channels) in listOf(16000 to 1, 48000 to 1, 44100 to 1, 48000 to 2, 44100 to 2)) {
                var recorder: AudioRecord? = null
                var accepted = false
                try {
                    val mask = if (channels == 1) AudioFormat.CHANNEL_IN_MONO else AudioFormat.CHANNEL_IN_STEREO
                    val minimum = AudioRecord.getMinBufferSize(rate, mask, AudioFormat.ENCODING_PCM_16BIT)
                    if (minimum <= 0) continue
                    recorder = AudioRecord.Builder().setAudioPlaybackCaptureConfig(capture)
                        .setAudioFormat(AudioFormat.Builder().setSampleRate(rate).setChannelMask(mask)
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                        .setBufferSizeInBytes(maxOf(minimum * 2, rate * channels)).build()
                    if (recorder.state == AudioRecord.STATE_INITIALIZED && recorder.sampleRate in setOf(16000, 44100, 48000) &&
                        recorder.channelCount in 1..2 && recorder.audioFormat == AudioFormat.ENCODING_PCM_16BIT) {
                        val source = PlaybackAudioSource(recorder, closed, warning)
                        accepted = true; return source
                    }
                } catch (_: SecurityException) {
                    // Permission/projection can be revoked after the service's initial check.
                    throw MicrophoneFailure("裝置音訊授權已失效；請確認錄音權限並重新取得系統擷取授權")
                } catch (_: IllegalArgumentException) {
                    // Only retry format negotiation, never substitute a microphone source.
                } catch (_: UnsupportedOperationException) {
                    // Device does not support this playback format.
                } finally { if (!accepted) recorder?.release() }
            }
            throw MicrophoneFailure("裝置音訊 AudioRecord 初始化失敗；請使用允許擷取的播放 App 並重新授權")
        }
    }
}

package tw.tiger.tigeryouvtt.standalone

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder

/** Created, read and released by the capture owner; no recording starts in the factory. */
class AudioRecordSource private constructor(private val recorder: AudioRecord) : MicPcmSource {
    override val sampleRate = recorder.sampleRate
    private var lastSilenceCheck = 0L
    override fun start() {
        android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_AUDIO)
        recorder.startRecording()
        if (recorder.recordingState != AudioRecord.RECORDSTATE_RECORDING)
            throw MicrophoneFailure("麥克風無法開始錄音，請確認權限及裝置是否被占用")
    }
    override fun read(buffer: ShortArray): Int {
        val now = android.os.SystemClock.elapsedRealtime()
        if (now - lastSilenceCheck >= 200) {
            lastSilenceCheck = now
            if (recorder.activeRecordingConfiguration?.isClientSilenced == true)
                throw MicrophoneFailure("麥克風已被系統靜音或其他 App 占用，請檢查隱私設定")
        }
        return recorder.read(buffer, 0, buffer.size, AudioRecord.READ_NON_BLOCKING)
    }
    override fun close() {
        try { if (recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING) recorder.stop() }
        finally { recorder.release() }
    }
    companion object {
        fun open(context: Context): AudioRecordSource {
            if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED)
                throw MicrophoneFailure("麥克風權限未授予，請允許錄音後再按開始")
            for (rate in listOf(16000, 48000, 44100)) {
                var recorder: AudioRecord? = null
                var accepted = false
                try {
                    val minimum = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
                    if (minimum <= 0) continue
                    recorder = AudioRecord.Builder().setAudioSource(MediaRecorder.AudioSource.MIC)
                        .setAudioFormat(AudioFormat.Builder().setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                        .setBufferSizeInBytes(maxOf(minimum * 2, rate)).build() // >= 0.5 s device buffer
                    if (recorder.state == AudioRecord.STATE_INITIALIZED && recorder.channelCount == 1 &&
                        recorder.audioFormat == AudioFormat.ENCODING_PCM_16BIT && recorder.sampleRate in setOf(16000, 48000, 44100)) {
                        val source = AudioRecordSource(recorder)
                        accepted = true
                        return source
                    }
                } catch (_: IllegalArgumentException) {
                    // Try only the supported fixed alternatives; no silent format assumption.
                } catch (_: UnsupportedOperationException) {
                    // A device may reject a requested capture format.
                } finally {
                    if (!accepted) recorder?.release()
                }
            }
            throw MicrophoneFailure("AudioRecord 初始化失敗，此裝置無法提供支援的單聲道 PCM 格式")
        }
    }
}
